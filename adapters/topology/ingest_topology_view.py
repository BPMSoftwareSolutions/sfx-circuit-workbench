"""Ingest a frozen estate-topology view product into a circuit-scene.v1 artifact.

The existing compiler and renderer stay where they are and keep their authority.
This adapter reads their product as *data* and lowers it into the workbench's own
scene contract, so that nothing downstream depends on `window.ESTATE_TOPOLOGY_*`
executable transport or on `viewer.js` to supply state or behavior.

What it establishes:

  * separate identities  - the graph entity id is a projection identifier; the
    source identity is the authority, and both are retained
  * screened scene       - scripts, inline event handlers and references outside
    the approved prefix are rejected before an SVG may be retained
  * semantic hit targets - extracted from the scene so a future Canvas or WebGL
    provider must reproduce them rather than inherit them from the SVG
  * separated coverage   - representation completeness and execution
    observability are reported as two different facts
  * traversability       - provider-binding routes are marked, because they
    accompany the wave reaching their port rather than being traversed

Usage
-----
    python adapters/topology/ingest_topology_view.py [--fixture ID ...]

Exit codes
----------
    0  every requested view ingested and schema-valid
    7  an ingestion, screening or validation check failed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent.parent
BASELINE = WORKBENCH / "fixtures" / "baseline"
DEFAULT_OUT = WORKBENCH / "fixtures" / "scenes"
CONTRACT = WORKBENCH / "contracts" / "circuit-scene.v1.schema.json"

sys.path.insert(0, str(WORKBENCH / "tools"))
from resolve_ui_dependencies import now_utc, sha256_file  # noqa: E402

ADAPTER = {"name": "ingest-topology-view", "version": "1.0.0"}

SCRIPT_PATTERN = re.compile(r"<\s*script", re.IGNORECASE)
HANDLER_PATTERN = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
HREF_PATTERN = re.compile(r'(?:xlink:)?href\s*=\s*"([^"]+)"', re.IGNORECASE)
GROUP_PATTERN = re.compile(r"<g\b[^>]*>", re.IGNORECASE)
ATTR_PATTERN = re.compile(r'([a-zA-Z_:][-\w:.]*)\s*=\s*"([^"]*)"')

# Routes of this kind are not traversed by the illustrative planner; they are
# appended alongside the flow wave that reaches their port.
NON_TRAVERSABLE_ROUTE_KIND = "provider-binding"


def read_payload(path: Path) -> dict:
    """Read a `window.X = {...};` product file as data, without executing it."""
    text = path.read_text(encoding="utf-8")
    body = text[text.index("=") + 1:].strip()
    if body.endswith(";"):
        body = body[:-1]
    return json.loads(body)


def screen(svg: str, approved: list, findings: list, scene_id: str) -> tuple[dict, list]:
    """Reject scripts, handlers and unapproved references. Return report and references."""
    scripts = bool(SCRIPT_PATTERN.search(svg))
    handlers = bool(HANDLER_PATTERN.search(svg))
    if scripts:
        findings.append({"code": "SCENE_SCRIPT_REJECTED", "severity": "error",
                         "identity": scene_id, "detail": "script element in scene artifact"})
    if handlers:
        findings.append({"code": "SCENE_EVENT_HANDLER_REJECTED", "severity": "error",
                         "identity": scene_id, "detail": "inline event handler in scene artifact"})

    references, unapproved = [], []
    for reference in sorted(set(HREF_PATTERN.findall(svg))):
        if reference.startswith("#"):
            continue
        if any(reference.startswith(prefix) for prefix in approved):
            references.append(reference)
        else:
            unapproved.append(reference)
            findings.append({"code": "SCENE_UNAPPROVED_REFERENCE", "severity": "error",
                             "identity": scene_id, "detail": reference})

    report = {
        "scriptsRejected": not scripts,
        "eventHandlersRejected": not handlers,
        "unapprovedReferencesRejected": not unapproved,
    }
    report["passed"] = all(report.values())
    return report, references


def extract_hit_targets(svg: str, nodes: set, routes: set, findings: list,
                        scene_id: str) -> list:
    """Lift the accessible selection targets out of the scene artifact.

    These become part of the scene contract so that changing renderer cannot
    quietly drop the selection and keyboard model along with the SVG.
    """
    targets, seen = [], set()
    for match in GROUP_PATTERN.finditer(svg):
        attributes = dict(ATTR_PATTERN.findall(match.group(0)))
        if "data-entity" in attributes:
            kind, entity_id = "node", attributes["data-entity"]
        elif "data-route" in attributes:
            kind, entity_id = "route", attributes["data-route"]
        else:
            continue
        target_id = attributes.get("id") or entity_id
        if target_id in seen:
            continue
        seen.add(target_id)
        targets.append({
            "targetId": target_id,
            "entityKind": kind,
            "entityId": entity_id,
            "accessibleName": attributes.get("aria-label", ""),
            "keyboardActivable": attributes.get("tabindex") == "0",
        })

    # Every declared entity must be reachable by selection, or coverage is a lie.
    covered = {t["entityId"] for t in targets}
    for missing in sorted(nodes - covered):
        findings.append({"code": "SCENE_NODE_NOT_SELECTABLE", "severity": "error",
                         "identity": missing,
                         "detail": "node has no hit target in the scene artifact"})
    for missing in sorted(routes - covered):
        findings.append({"code": "SCENE_ROUTE_NOT_SELECTABLE", "severity": "error",
                         "identity": missing,
                         "detail": "route has no hit target in the scene artifact"})
    unkeyed = [t["targetId"] for t in targets if not t["keyboardActivable"]]
    if unkeyed:
        findings.append({"code": "SCENE_TARGET_NOT_KEYBOARD_ACTIVABLE", "severity": "error",
                         "identity": scene_id,
                         "detail": "%d hit targets are not keyboard activable" % len(unkeyed)})
    return targets


def build_scene(payload: dict, view: dict, capability_id: str, source_path: Path,
                materials_index: dict, approved: list) -> dict:
    findings: list = []
    scene_id = "%s/%s" % (capability_id, payload["id"])
    analytics = payload.get("analytics", {})
    layout = payload.get("layout", {})

    node_ids = {n["id"] for n in payload.get("nodes", [])}
    route_ids = {e["id"] for e in payload.get("edges", [])}

    screening, references = screen(payload.get("svg", ""), approved, findings, scene_id)
    hit_targets = extract_hit_targets(payload.get("svg", ""), node_ids, route_ids,
                                      findings, scene_id)

    nodes = []
    for node in payload.get("nodes", []):
        record = {k: node[k] for k in ("id", "identity", "kind", "label") if k in node}
        for optional in ("detail", "source", "facts"):
            if optional in node:
                record[optional] = node[optional]
        if node["id"] not in layout.get("boxes", {}):
            findings.append({"code": "SCENE_GEOMETRY_MISSING", "severity": "error",
                             "identity": node["id"], "detail": "node has no layout box"})
        nodes.append(record)

    routes = []
    for edge in payload.get("edges", []):
        record = {k: edge[k] for k in ("id", "identity", "source", "target", "kind") if k in edge}
        record["traversable"] = edge.get("kind") != NON_TRAVERSABLE_ROUTE_KIND
        for optional in ("label", "provenance", "facts"):
            if optional in edge:
                record[optional] = edge[optional]
        for endpoint in ("source", "target"):
            if edge[endpoint] not in node_ids:
                findings.append({"code": "SCENE_ROUTE_ENDPOINT_UNRESOLVED", "severity": "error",
                                 "identity": edge["id"],
                                 "detail": "%s %r is not a node in this view" % (endpoint, edge[endpoint])})
        routes.append(record)

    # The displayed coverage strip must agree with what is actually retained.
    if len(nodes) != analytics.get("nodes") or len(routes) != analytics.get("edges"):
        findings.append({"code": "SCENE_COVERAGE_INCONSISTENT", "severity": "error",
                         "identity": scene_id,
                         "detail": "retained %d/%d, analytics %r/%r"
                                   % (len(nodes), len(routes),
                                      analytics.get("nodes"), analytics.get("edges"))})

    materials = []
    for reference in references:
        name = reference.rsplit("/", 1)[-1]
        entry = materials_index.get(name)
        if entry is None:
            findings.append({"code": "SCENE_MATERIAL_UNRETAINED", "severity": "error",
                             "identity": scene_id, "detail": reference})
            continue
        materials.append({"reference": reference, "sha256": entry["sha256"],
                          "retained": entry["retained"]})

    svg_bytes = payload.get("svg", "").encode("utf-8")
    return {
        "sceneVersion": "circuit-scene.v1",
        "sceneId": scene_id,
        "label": payload.get("label"),
        "identities": {
            "capabilityId": capability_id,
            "viewId": payload["id"],
            "viewKind": payload["kind"],
            "sourceIdentity": payload.get("identity"),
            "scenarioId": view.get("scenarioId"),
        },
        "traceMode": "ILLUSTRATIVE",
        "graph": {"nodes": nodes, "routes": routes},
        "geometry": {
            "width": layout.get("width"),
            "height": layout.get("height"),
            "engine": analytics.get("engine", "unknown"),
            "boxes": layout.get("boxes", {}),
            "overlaps": (payload.get("geometry") or {}).get("overlaps", 0),
        },
        "materials": materials,
        "hitTargets": hit_targets,
        "scene": {
            "kind": "svg",
            "retained": None,
            "sha256": hashlib.sha256(svg_bytes).hexdigest(),
            "bytes": len(svg_bytes),
            "screening": screening,
        },
        "coverage": {
            "nodes": len(nodes),
            "routes": len(routes),
            "omittedSourceNodes": analytics.get("omittedSourceNodes", 0),
            "omittedSourceEdges": analytics.get("omittedSourceEdges", 0),
            "representationComplete": (analytics.get("omittedSourceNodes", 0) == 0
                                       and analytics.get("omittedSourceEdges", 0) == 0),
            "nodeKinds": analytics.get("nodeKinds"),
            "routeKinds": analytics.get("routeKinds"),
            "observability": {
                "instrumented": False,
                "note": ("Declared topology only. No execution instrumentation exists for this "
                         "view; representation completeness says nothing about observability."),
            },
        },
        "topology": {
            "roots": analytics.get("roots", []),
            "leaves": analytics.get("leaves", []),
            "weakComponents": analytics.get("weakComponents", 0),
            "cyclicComponentCount": len(analytics.get("cyclicComponents") or []),
        },
        "provenance": {
            "sourceAuthority": payload.get("source", {}),
            "ingestedFrom": {"path": source_path.as_posix(), "sha256": sha256_file(source_path)},
            "adapter": dict(ADAPTER),
        },
        "findings": findings,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=BASELINE / "baseline-manifest.json")
    parser.add_argument("--sources", type=Path, default=BASELINE / "baseline-sources.json")
    parser.add_argument("--fixture", action="append", default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    declaration = json.loads(args.sources.read_text(encoding="utf-8"))
    approved = declaration["ingestionPolicy"].get("approvedReferencePrefixes", [])
    capability_id = manifest["capability"]["capabilityId"]

    materials_index = {
        entry["retained"].rsplit("/", 1)[-1]: entry
        for entry in manifest["artifacts"]["materials"]
    }
    by_fixture = {v["fixtureId"]: v for v in declaration["views"]}

    schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
    import jsonschema

    args.out_dir.mkdir(parents=True, exist_ok=True)
    scenes_dir = args.out_dir
    results, failed = [], 0

    for record in manifest["artifacts"]["views"]:
        fixture_id = record["fixtureId"]
        if args.fixture and fixture_id not in args.fixture:
            continue
        source_path = WORKBENCH / record["artifact"]["retained"]
        payload = read_payload(source_path)
        scene = build_scene(payload, by_fixture.get(fixture_id, {}), capability_id,
                            source_path, materials_index, approved)

        # The screened artifact is retained beside its scene, as data.
        svg_path = scenes_dir / (fixture_id + ".svg")
        svg_path.write_text(payload.get("svg", ""), encoding="utf-8")
        scene["scene"]["retained"] = svg_path.relative_to(WORKBENCH).as_posix()

        try:
            jsonschema.validate(scene, schema)
            valid, detail = True, None
        except jsonschema.ValidationError as error:
            valid = False
            detail = "%s at /%s" % (error.message,
                                    "/".join(str(p) for p in error.absolute_path))

        scene_path = scenes_dir / (fixture_id + ".scene.json")
        scene_path.write_text(json.dumps(scene, indent=2) + "\n", encoding="utf-8")

        errors = [f for f in scene["findings"] if f["severity"] == "error"]
        ok = valid and not errors and scene["scene"]["screening"]["passed"]
        if not ok:
            failed += 1

        results.append({
            "fixtureId": fixture_id,
            "scene": scene_path.relative_to(WORKBENCH).as_posix(),
            "sha256": sha256_file(scene_path),
            "schemaValid": valid,
            "schemaDetail": detail,
            "screeningPassed": scene["scene"]["screening"]["passed"],
            "nodes": scene["coverage"]["nodes"],
            "routes": scene["coverage"]["routes"],
            "hitTargets": len(scene["hitTargets"]),
            "materials": len(scene["materials"]),
            "errorFindings": len(errors),
            "ingested": ok,
        })

        print("  %-20s %s · %d nodes / %d routes · %d hit targets · %d materials%s" % (
            fixture_id, scene["identities"]["viewKind"], scene["coverage"]["nodes"],
            scene["coverage"]["routes"], len(scene["hitTargets"]), len(scene["materials"]),
            "" if ok else "  REFUSED"))
        if detail:
            print("    schema: " + detail, file=sys.stderr)
        for finding in errors[:5]:
            print("    %s: %s" % (finding["code"], finding["detail"]), file=sys.stderr)

    receipt = {
        "receiptType": "topology-ingestion-receipt.v1",
        "ingestedAt": now_utc(),
        "adapter": ADAPTER,
        "contract": {"path": CONTRACT.relative_to(WORKBENCH).as_posix(),
                     "sha256": sha256_file(CONTRACT)},
        "summary": {"requested": len(results), "ingested": len(results) - failed,
                    "refused": failed},
        "scenes": results,
        "traceMode": "ILLUSTRATIVE",
        "evidenceLimit": "Declared topology. No execution evidence is carried by these scenes.",
    }
    receipt_dir = WORKBENCH / "evidence" / "scenes"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "topology-ingestion.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print("ingestion      %d/%d ingested" % (len(results) - failed, len(results)))
    print("receipt        %s" % receipt_path.as_posix())
    return 0 if failed == 0 and results else 7


if __name__ == "__main__":
    raise SystemExit(main())
