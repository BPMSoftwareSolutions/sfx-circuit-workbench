"""Derive a capability's circuit from live authority, per request.

The estate says how it works: `sfx capability invoke` reads and plans authority
*per invocation*. The circuit is the same kind of thing — a view of that
authority — so it is derived the same way rather than waited for as a prerendered
product.

That distinction is not academic. `resolve-equity-market-price-evidence` executes
against live RapidAPI through Azure SQL authority every time it is invoked, and
has no compiled topology anywhere: not in the estate-topology products, the
estate-circuits index, the scenario inventory, or `media.v_requirement`. Waiting
for a prerendered artifact would have left the one capability with a live
external provider as the only one without a circuit.

What this reads, all declared and all real:

    authority spine   input_id, event_id, responsibility, outcome_id, and the
                      contract states, from the same read invocation performs
    effect ports      the declared credential-binding and exchange ports, their
                      endpoint authority and credential references
    closure           the downstream scenario closure and whether it cycles

It produces an `operations` view with the same node and route vocabulary the
estate's own compiler produces for every other capability — `input`,
`provider-port`, `provider`, `outcome`, joined by `operation-order`,
`provider-binding` and `operation-result` — so one execution mapping serves both.

Geometry is laid out here rather than by Graphviz, and says so: `engine` is
recorded as this adapter, not as a layout the estate produced.

Usage
-----
    python adapters/estate/derive_circuit.py --capability ID [--authority FILE]

Exit codes
----------
    0  derived and schema-valid
   22  the authority could not be read, or the derived scene was refused
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent.parent
CONTRACT = WORKBENCH / "contracts" / "circuit-scene.v1.schema.json"
DEFAULT_OUT = WORKBENCH / "fixtures" / "estate"

sys.path.insert(0, str(WORKBENCH / "tools"))
from resolve_ui_dependencies import now_utc, sha256_file  # noqa: E402

ADAPTER = {"name": "derive-circuit", "version": "1.0.0"}

# Node geometry, in graph space, matching the proportions the estate's compiler
# produces so a derived circuit sits beside a compiled one without looking alien.
BOX = [270, 135]
GAP = 190

TRANSFORMATION_PORT = "sda-authority-transformation-port.v1"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def source_document(bundle: dict, source_path: str):
    """Read one declared document from the authority's retained source records."""
    records = bundle["authority"]["recordsets"][1] + bundle["authority"]["recordsets"][2]
    for record in records:
        if record.get("source_path") == source_path:
            return json.loads(base64.b64decode(record["content_bytes"]["base64"]).decode("utf-8"))
    return None


def declared_effect_ports(capability_id: str, authority: dict) -> list:
    """The capability's declared effect ports, read from its interface authority.

    A capability obtains external testimony through declared effect ports, not
    through an invocation-time binding document. This is the same authority the
    planner materializes, so the circuit and the executed body agree by
    construction.
    """
    interfaces = source_document(authority["bundle"],
                                 "capabilities/%s/interfaces.authority.json" % capability_id)
    if not interfaces:
        return []
    return [binding for binding in interfaces.get("portBindings", [])
            if binding.get("platformCapabilityId") != TRANSFORMATION_PORT]


def entity_id(*parts: str) -> str:
    """Projection identifier, derived so it is stable across derivations."""
    return "n-" + hashlib.sha256("/".join(parts).encode("utf-8")).hexdigest()[:24]


def derive(capability_id: str, authority: dict, effect_ports: list,
           findings: list) -> dict:
    bundle = authority["bundle"]
    spine = bundle["authority"]["recordsets"][0][0]
    closure_rows = bundle["closure"]["recordsets"][0]
    scenario_id = spine["scenario_id"]
    source_digest = spine.get("scenario_definition_digest", "")

    def source(pointer: str) -> dict:
        return {
            "id": "authority",
            "path": "analysis/capability-embodiment.sql",
            "sha256": (source_digest or "sha256:" + "0" * 64).replace("sha256:", ""),
            "pointer": pointer,
            "kind": "DECLARED",
            "label": "estate authority / %s" % capability_id,
            "encoding": "sql-recordset",
        }

    nodes, routes, boxes = [], [], {}

    def place(index: int, node: dict) -> None:
        nodes.append(node)
        boxes[node["id"]] = [index * (BOX[0] + GAP) + 40.0, 60.0, float(BOX[0]), float(BOX[1])]

    input_node = entity_id(capability_id, "input")
    port_node = entity_id(capability_id, "operation")
    provider_node = entity_id(capability_id, "provider")
    outcome_node = entity_id(capability_id, "outcome")

    place(0, {
        "id": input_node, "identity": "%s/input" % capability_id, "kind": "input",
        "label": spine["input_id"], "detail": "Declared capability input contract.",
        "source": source("/input_id"),
        "facts": {"contractId": spine["input_id"],
                  "contractState": spine.get("input_contract_state"),
                  "scenarioId": scenario_id},
    })
    place(1, {
        "id": port_node, "identity": "%s.operation.1" % capability_id, "kind": "provider-port",
        "label": spine["event_id"],
        "detail": spine.get("responsibility") or "Declared event authority.",
        "source": source("/event_id"),
        "facts": {"eventId": spine["event_id"],
                  "eventAuthorityState": spine.get("event_authority_state"),
                  "responsibility": spine.get("responsibility")},
    })
    place(3, {
        "id": outcome_node, "identity": "%s/outcome" % capability_id, "kind": "outcome",
        "label": spine["outcome_id"], "detail": "Declared capability outcome contract.",
        "source": source("/outcome_id"),
        "facts": {"contractId": spine["outcome_id"],
                  "outcomeContractVersionPk": spine.get("outcome_contract_version_pk"),
                  "scenarioId": scenario_id},
    })

    if effect_ports:
        exchange = next((p for p in effect_ports
                         if (p.get("configuration") or {}).get("endpointAuthorities")), None)
        credential = next((p for p in effect_ports
                           if (p.get("configuration") or {}).get("credentialAuthorities")), None)
        endpoint = ((exchange or {}).get("configuration") or {}).get("endpointAuthorities", [{}])[0]
        origins = endpoint.get("urlPrefixes") or []
        references = sorted({a.get("referenceName")
                             for a in ((credential or {}).get("configuration") or {}).get("credentialAuthorities", [])
                             if a.get("referenceName")})
        place(2, {
            "id": provider_node,
            "identity": "%s/provider" % capability_id,
            "kind": "provider", "label": origins[0] if origins else "declared provider",
            "detail": "Declared effect ports: %s"
                      % ", ".join(p.get("platformCapabilityId", "") for p in effect_ports),
            "source": {**source("/interfaces"), "path": "interfaces.authority.json",
                       "encoding": "declared-authority"},
            "facts": {"effectPorts": [p.get("platformCapabilityId") for p in effect_ports],
                      "endpointAuthorityDigest": endpoint.get("endpointAuthorityDigest"),
                      "endpointPrefixes": origins,
                      "credentialReferences": references},
        })
    else:
        findings.append({"code": "CIRCUIT_PROVIDER_BINDING_ABSENT", "severity": "info",
                         "identity": capability_id,
                         "detail": "no declared effect port; the operation stands alone"})

    def route(kind: str, label: str, a: str, b: str, pointer: str,
              traversable: bool = True) -> None:
        routes.append({
            "id": entity_id(capability_id, kind, a, b),
            "identity": "%s:%s->%s" % (kind, a, b),
            "source": a, "target": b, "kind": kind, "label": label,
            "traversable": traversable,
            "provenance": source(pointer),
            "facts": {"scenarioId": scenario_id},
        })

    route("operation-order", "admits", input_node, port_node, "/input_id")
    if effect_ports:
        # An effect port is bound authority, not a traversed step: it accompanies
        # the wave that reaches its port, exactly as the compiled views declare it.
        route("provider-binding", "bound effect port", provider_node, port_node,
              "/interfaces/portBindings", traversable=False)
    route("operation-result", "resolves", port_node, outcome_node, "/outcome_id")

    cycles = sum(1 for row in closure_rows if row.get("cycle_detected"))
    width = max(box[0] + box[2] for box in boxes.values()) + 40.0
    height = max(box[1] + box[3] for box in boxes.values()) + 60.0

    return {
        "sceneVersion": "circuit-scene.v1",
        "sceneId": "%s/%s" % (capability_id, entity_id(capability_id, "operations")),
        "label": "Execution · %s" % (spine.get("responsibility") or capability_id)[:80],
        "identities": {
            "capabilityId": capability_id,
            "viewId": entity_id(capability_id, "operations"),
            "viewKind": "operations",
            "sourceIdentity": "%s/operations/%s" % (capability_id, scenario_id),
            "scenarioId": scenario_id,
        },
        "traceMode": "ILLUSTRATIVE",
        "graph": {"nodes": nodes, "routes": routes},
        "geometry": {"width": width, "height": height, "engine": "derive-circuit.v1",
                     "boxes": boxes, "overlaps": 0},
        "materials": [],
        # Derived geometry carries no rendered artifact, so selection targets are
        # declared here rather than lifted out of an SVG.
        "hitTargets": [
            {"targetId": n["id"], "entityKind": "node", "entityId": n["id"],
             "accessibleName": "%s: %s" % (n["kind"], n["label"]),
             "keyboardActivable": True} for n in nodes
        ] + [
            {"targetId": r["id"], "entityKind": "route", "entityId": r["id"],
             "accessibleName": "%s: %s" % (r["kind"], r["label"]),
             "keyboardActivable": True} for r in routes
        ],
        "coverage": {
            "nodes": len(nodes), "routes": len(routes),
            "omittedSourceNodes": 0, "omittedSourceEdges": 0,
            "representationComplete": True,
            "nodeKinds": {k: sum(1 for n in nodes if n["kind"] == k)
                          for k in {n["kind"] for n in nodes}},
            "routeKinds": {k: sum(1 for r in routes if r["kind"] == k)
                           for k in {r["kind"] for r in routes}},
            "observability": {
                "instrumented": True,
                "note": "This capability reports scenario observations when invoked; the "
                        "execution mapping places them on these nodes.",
            },
        },
        "topology": {
            "roots": [input_node], "leaves": [outcome_node],
            "weakComponents": 1, "cyclicComponentCount": cycles,
        },
        "provenance": {
            "sourceAuthority": {
                "snapshotId": bundle["authority"].get("snapshotId"),
                "projectionDigest": bundle["authority"].get("projectionDigest"),
                "capabilityDefinitionDigest": spine.get("capability_definition_digest"),
                "scenarioDefinitionDigest": spine.get("scenario_definition_digest"),
                "readPerInvocation": True,
            },
            "ingestedFrom": {"path": "estate authority (read and planned per invocation)",
                             "sha256": hashlib.sha256(
                                 json.dumps(spine, sort_keys=True, default=str)
                                 .encode("utf-8")).hexdigest()},
            "adapter": dict(ADAPTER),
        },
        "findings": findings,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capability", required=True)
    parser.add_argument("--authority", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    authority_path = args.authority or (WORKBENCH / "build" / "invocation-authority"
                                        / (args.capability + ".json"))
    if not authority_path.is_file():
        print("AUTHORITY_UNAVAILABLE: %s\n  run: node tools/read_invocation_authority.mjs"
              % authority_path, file=sys.stderr)
        return 22

    authority = load(authority_path)
    effect_ports = declared_effect_ports(args.capability, authority)

    findings: list = []
    scene = derive(args.capability, authority, effect_ports, findings)

    import jsonschema
    try:
        jsonschema.validate(scene, load(CONTRACT))
    except jsonschema.ValidationError as error:
        print("CIRCUIT_SCENE_INVALID: %s at /%s"
              % (error.message, "/".join(str(p) for p in error.absolute_path)), file=sys.stderr)
        return 22

    out = args.out_dir / args.capability
    out.mkdir(parents=True, exist_ok=True)
    scene_path = out / (scene["identities"]["viewId"] + ".scene.json")
    scene_path.write_text(json.dumps(scene, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")

    # Register the derived view in the catalogue, so a capability with no
    # compiled topology is still selectable alongside the rest of the estate.
    catalogue_path = args.out_dir / "estate-catalogue.json"
    if catalogue_path.is_file():
        catalogue = load(catalogue_path)
        view = {
            "viewId": scene["identities"]["viewId"],
            "viewKind": "operations",
            "label": scene["label"],
            "scenarioId": scene["identities"]["scenarioId"],
            "coverage": {k: scene["coverage"][k] for k in
                         ("nodes", "routes", "omittedSourceNodes")},
            "source": "estate authority (read and planned per invocation)",
            "scene": scene_path.relative_to(WORKBENCH).as_posix(),
            "artifact": None,
            "sha256": sha256_file(scene_path),
            "derived": True,
        }
        existing = next((c for c in catalogue["capabilities"]
                         if c["capabilityId"] == args.capability), None)
        # What may be done with it comes from the publication, not from how its
        # circuit was obtained: deriving a view grants no authority to run it.
        sys.path.insert(0, str(HERE))
        from ingest_capability import published_subjects
        published = published_subjects(
            WORKBENCH / "dependencies" / "lab-publication.binding.json").get(args.capability)
        record = existing or {"capabilityId": args.capability, "views": [],
                              "scenarios": [], "affordances": ["inspect"],
                              "publication": None}
        record["affordances"] = ["inspect"] + (["invoke"] if published else [])
        record["publication"] = published
        record["title"] = scene["label"]
        record["views"] = [v for v in record["views"]
                           if v["viewId"] != view["viewId"]] + [view]
        record["viewKinds"] = {"operations": sum(1 for v in record["views"]
                                                 if v["viewKind"] == "operations")}
        record["scenarios"] = sorted({v["scenarioId"] for v in record["views"]
                                      if v.get("scenarioId")})
        if existing is None:
            catalogue["capabilities"].append(record)
            catalogue["capabilities"].sort(key=lambda c: c["capabilityId"])
            catalogue["ingested"] = len(catalogue["capabilities"])
        catalogue["invocable"] = sum(1 for c in catalogue["capabilities"]
                                     if "invoke" in c["affordances"])
        catalogue_path.write_text(
            json.dumps(catalogue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    receipt_dir = WORKBENCH / "evidence" / "estate"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / ("derived-circuit-%s.receipt.json" % args.capability)).write_text(
        json.dumps({
            "receiptType": "derived-circuit-receipt.v1",
            "derivedAt": now_utc(),
            "capabilityId": args.capability,
            "statement": "Circuit derived from the estate authority that invocation reads and "
                         "plans per invocation, not from a prerendered topology product.",
            "authority": {"path": authority_path.relative_to(WORKBENCH).as_posix()
                          if authority_path.is_relative_to(WORKBENCH) else str(authority_path),
                          "sha256": sha256_file(authority_path)},
            "providerBinding": ({"effectPorts": [
                {"portId": p.get("portId"), "platformCapabilityId": p.get("platformCapabilityId")}
                for p in effect_ports]} if effect_ports else None),
            "scene": {"path": scene_path.relative_to(WORKBENCH).as_posix(),
                      "sha256": sha256_file(scene_path)},
            "coverage": scene["coverage"],
            "findings": findings,
            "notEstablished": [
                "Geometry is laid out by this adapter, not by the estate's Graphviz "
                "renderer; `engine` records that.",
                "`sfx capability reveal` is declared but not yet offered by the estate. When "
                "it is, this adapter should consume its views instead of deriving them.",
            ],
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("  %-44s %d nodes / %d routes (%s)" % (
        args.capability, scene["coverage"]["nodes"], scene["coverage"]["routes"],
        ", ".join("%s:%d" % kv for kv in sorted(scene["coverage"]["nodeKinds"].items()))))
    print("derived        %s" % scene_path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
