"""Freeze the reference workbench: copy the captured inputs and digest them.

M0 asks for the two captured views to be retained with their source, catalog,
SVG and material inputs and digests, and for the source counts and control
behavior to be recorded. This tool performs the retention and emits the
baseline manifest that says exactly what M1 is recreating.

Every frozen artifact is copied byte for byte and digested. Each view payload
is checked against its declared expectation: identity, kind, node and edge
counts and omission counts must match, or the freeze fails. The embedded SVG is
screened for scripts, event handlers and unapproved external references before
it may be retained as a scene artifact.

Usage
-----
    python tools/freeze_baseline.py [--products DIR] [--template DIR] [--verify]

`--verify` re-checks an existing freeze against its manifest without copying.

Exit codes
----------
    0  frozen (or verified) with every expectation met
    5  an expectation, screening rule or digest check failed
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent
BASELINE = WORKBENCH / "fixtures" / "baseline"
DEFAULT_SOURCES = BASELINE / "baseline-sources.json"
DEFAULT_OUT = WORKBENCH / "evidence" / "baseline"

sys.path.insert(0, str(HERE))
from resolve_ui_dependencies import now_utc, sha256_file  # noqa: E402

SCRIPT_PATTERN = re.compile(r"<\s*script", re.IGNORECASE)
HANDLER_PATTERN = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
HREF_PATTERN = re.compile(r'(?:xlink:)?href\s*=\s*"([^"]+)"', re.IGNORECASE)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def select_root(declared: dict, argument: str | None) -> tuple[Path, str]:
    if argument:
        return Path(argument).resolve(), "argument"
    from_env = os.environ.get(declared["environmentVariable"])
    if from_env:
        return Path(from_env).resolve(), "environment:" + declared["environmentVariable"]
    return Path(declared["default"]).resolve(), "declaration-default"


def read_payload(path: Path) -> dict:
    """Read a `window.X = {...};` product file as data, without executing it."""
    text = path.read_text(encoding="utf-8")
    body = text[text.index("=") + 1:].strip()
    if body.endswith(";"):
        body = body[:-1]
    return json.loads(body)


def screen_svg(svg: str, policy: dict, findings: list, fixture_id: str) -> list:
    """Reject scripts, handlers and unapproved external references; return references."""
    if policy.get("rejectScripts") and SCRIPT_PATTERN.search(svg):
        findings.append({"code": "SCENE_SCRIPT_REJECTED", "severity": "error",
                         "fixtureId": fixture_id, "detail": "script element in scene SVG"})
    if policy.get("rejectEventHandlers") and HANDLER_PATTERN.search(svg):
        findings.append({"code": "SCENE_EVENT_HANDLER_REJECTED", "severity": "error",
                         "fixtureId": fixture_id, "detail": "inline event handler in scene SVG"})

    references = sorted(set(HREF_PATTERN.findall(svg)))
    approved = policy.get("approvedReferencePrefixes", [])
    external = []
    for reference in references:
        if reference.startswith("#"):
            continue
        if any(reference.startswith(prefix) for prefix in approved):
            external.append(reference)
            continue
        if policy.get("rejectUnapprovedExternalReferences"):
            findings.append({"code": "SCENE_UNAPPROVED_REFERENCE", "severity": "error",
                             "fixtureId": fixture_id, "detail": reference})
    return external


def check_expectations(payload: dict, view: dict, findings: list) -> None:
    expected, analytics = view["expected"], payload.get("analytics", {})
    fixture_id = view["fixtureId"]

    if payload.get("id") != view["viewId"]:
        findings.append({"code": "BASELINE_IDENTITY_MISMATCH", "severity": "error",
                         "fixtureId": fixture_id,
                         "detail": "payload declares id %r" % payload.get("id")})

    observed = {
        "label": payload.get("label"),
        "kind": payload.get("kind"),
        "nodes": analytics.get("nodes"),
        "edges": analytics.get("edges"),
        "omittedSourceNodes": analytics.get("omittedSourceNodes"),
        "omittedSourceEdges": analytics.get("omittedSourceEdges"),
    }
    for key, want in expected.items():
        if observed.get(key) != want:
            findings.append({"code": "BASELINE_EXPECTATION_UNMET", "severity": "error",
                             "fixtureId": fixture_id,
                             "detail": "%s: expected %r, observed %r" % (key, want, observed.get(key))})

    # The counts the strip displays must agree with the arrays actually retained.
    if len(payload.get("nodes", [])) != analytics.get("nodes"):
        findings.append({"code": "BASELINE_NODE_COUNT_INCONSISTENT", "severity": "error",
                         "fixtureId": fixture_id,
                         "detail": "%d node records for analytics.nodes %r"
                                   % (len(payload.get("nodes", [])), analytics.get("nodes"))})
    if len(payload.get("edges", [])) != analytics.get("edges"):
        findings.append({"code": "BASELINE_EDGE_COUNT_INCONSISTENT", "severity": "error",
                         "fixtureId": fixture_id,
                         "detail": "%d edge records for analytics.edges %r"
                                   % (len(payload.get("edges", [])), analytics.get("edges"))})

    boxes = (payload.get("layout") or {}).get("boxes") or {}
    missing = [n["id"] for n in payload.get("nodes", []) if n["id"] not in boxes]
    if missing:
        findings.append({"code": "BASELINE_GEOMETRY_MISSING", "severity": "error",
                         "fixtureId": fixture_id,
                         "detail": "%d nodes without a layout box" % len(missing)})


def freeze_file(source: Path, target: Path) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return {
        "source": source.as_posix(),
        "retained": target.relative_to(WORKBENCH).as_posix(),
        "sha256": sha256_file(target),
        "bytes": target.stat().st_size,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--products", default=None)
    parser.add_argument("--template", default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--verify", action="store_true",
                        help="Re-digest the existing freeze against its manifest.")
    args = parser.parse_args(argv)

    declaration = json.loads(args.sources.read_text(encoding="utf-8"))
    findings: list = []

    if args.verify:
        return verify(declaration, args.out_dir)

    products_root, products_source = select_root(declaration["roots"]["products"], args.products)
    template_root, template_source = select_root(declaration["roots"]["template"], args.template)
    capability = declaration["capability"]
    capability_dir = products_root / capability["capabilityId"]
    retained_root = BASELINE / capability["capabilityId"]

    if retained_root.exists():
        shutil.rmtree(retained_root)

    artifacts: dict = {"catalog": None, "views": [], "template": [], "materials": []}

    catalog_source = capability_dir / capability["catalog"]
    if not catalog_source.is_file():
        findings.append({"code": "BASELINE_CATALOG_MISSING", "severity": "error",
                         "detail": catalog_source.as_posix()})
    else:
        artifacts["catalog"] = freeze_file(catalog_source, retained_root / capability["catalog"])
        catalog = read_payload(catalog_source)
        if catalog.get("capabilityId") != capability["capabilityId"]:
            findings.append({"code": "BASELINE_CATALOG_IDENTITY_MISMATCH", "severity": "error",
                             "detail": "catalog declares %r" % catalog.get("capabilityId")})

    materials: set = set()
    for view in declaration["views"]:
        source = capability_dir / (view["viewId"] + ".js")
        record: dict = {"fixtureId": view["fixtureId"], "viewId": view["viewId"],
                        "capture": view["capture"]}
        if not source.is_file():
            findings.append({"code": "BASELINE_VIEW_MISSING", "severity": "error",
                             "fixtureId": view["fixtureId"], "detail": source.as_posix()})
            artifacts["views"].append(record)
            continue

        record["artifact"] = freeze_file(source, retained_root / (view["viewId"] + ".js"))
        payload = read_payload(source)
        check_expectations(payload, view, findings)

        references = screen_svg(payload.get("svg", ""), declaration["ingestionPolicy"],
                                findings, view["fixtureId"])
        materials.update(references)

        analytics = payload.get("analytics", {})
        record["observed"] = {
            "label": payload.get("label"),
            "identity": payload.get("identity"),
            "kind": payload.get("kind"),
            "nodes": analytics.get("nodes"),
            "edges": analytics.get("edges"),
            "omittedSourceNodes": analytics.get("omittedSourceNodes"),
            "omittedSourceEdges": analytics.get("omittedSourceEdges"),
            "nodeKinds": analytics.get("nodeKinds"),
            "routeKinds": analytics.get("routeKinds"),
            "weakComponents": analytics.get("weakComponents"),
            "cyclicComponentCount": len(analytics.get("cyclicComponents") or []),
            "roots": analytics.get("roots"),
            "leaves": analytics.get("leaves"),
            "findings": payload.get("findings"),
            "layout": {
                "width": (payload.get("layout") or {}).get("width"),
                "height": (payload.get("layout") or {}).get("height"),
                "boxes": len((payload.get("layout") or {}).get("boxes") or {}),
            },
            "svgBytes": len(payload.get("svg", "")),
            "materialReferences": len(references),
        }
        record["sourceAuthority"] = payload.get("source")

        # The captured zoom is a recorded observation; the loader's own default is derived.
        layout = payload.get("layout") or {}
        if layout.get("width") and layout.get("height"):
            fitted = min(1.0, (1005 - 40) / layout["width"], (630 - 40) / layout["height"])
            record["derivedCamera"] = {
                "fittedScaleAt1005x630": round(fitted, 4),
                "fittedPercent": round(fitted * 100),
                "belowLoaderThreshold": fitted < 0.35,
                "loadDefaultScale": 0.7 if fitted < 0.35 else round(fitted, 4),
                "note": ("The loader raises any fitted scale below 0.35 to 0.70, so the "
                         "captured zoom is not necessarily the load default."),
                "requiresMeasurement": True,
            }
        artifacts["views"].append(record)

    textures_root = products_root / declaration["materials"]["folder"]
    for reference in sorted(materials):
        name = reference.rsplit("/", 1)[-1]
        source = textures_root / name
        if not source.is_file():
            findings.append({"code": "BASELINE_MATERIAL_MISSING", "severity": "error",
                             "detail": reference})
            continue
        record = freeze_file(source, retained_root / "textures" / name)
        record["reference"] = reference
        artifacts["materials"].append(record)

    for name in declaration["templateFiles"]:
        source = template_root / name
        if not source.is_file():
            findings.append({"code": "BASELINE_TEMPLATE_MISSING", "severity": "error",
                             "detail": source.as_posix()})
            continue
        artifacts["template"].append(freeze_file(source, BASELINE / "viewer" / name))

    errors = sum(1 for f in findings if f["severity"] == "error")
    manifest = {
        "manifestVersion": "workbench-baseline-manifest.v1",
        "statement": ("Exactly what the recreated workbench is being measured against. "
                      "Every artifact here is retained byte for byte and digested."),
        "frozenAt": now_utc(),
        "sources": {
            "declaration": {"path": args.sources.resolve().as_posix(),
                            "sha256": sha256_file(args.sources)},
            "products": {"path": products_root.as_posix(), "selectedFrom": products_source},
            "template": {"path": template_root.as_posix(), "selectedFrom": template_source},
        },
        "capability": capability,
        "controlBehavior": {
            "path": (BASELINE / "control-behavior.json").relative_to(WORKBENCH).as_posix(),
            "sha256": sha256_file(BASELINE / "control-behavior.json"),
        },
        "referenceScreenshot": screenshot_record(),
        "artifacts": artifacts,
        "summary": {
            "views": len(artifacts["views"]),
            "materials": len(artifacts["materials"]),
            "templateFiles": len(artifacts["template"]),
            "errors": errors,
            "frozen": errors == 0,
        },
        "findings": findings,
        "traceMode": "ILLUSTRATIVE",
        "evidenceLimit": ("These fixtures are declared topology. They establish no execution "
                          "evidence, and no capability was invoked to produce them."),
    }

    manifest_path = BASELINE / "baseline-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = args.out_dir / "baseline-freeze.receipt.json"
    receipt_path.write_text(json.dumps({
        "receiptType": "baseline-freeze-receipt.v1",
        "frozenAt": manifest["frozenAt"],
        "manifest": {"path": manifest_path.as_posix(), "sha256": sha256_file(manifest_path)},
        "summary": manifest["summary"],
        "findings": findings,
    }, indent=2) + "\n", encoding="utf-8")

    print("products       %s (%s)" % (products_root.as_posix(), products_source))
    print("template       %s (%s)" % (template_root.as_posix(), template_source))
    for record in artifacts["views"]:
        observed = record.get("observed")
        if not observed:
            print("  %-20s UNAVAILABLE" % record["fixtureId"])
            continue
        print("  %-20s %s · %d components / %d routes · %d omitted · %d materials" % (
            record["fixtureId"], observed["kind"], observed["nodes"], observed["edges"],
            observed["omittedSourceNodes"], observed["materialReferences"]))
    print("materials      %d frozen" % len(artifacts["materials"]))
    print("template       %d files frozen" % len(artifacts["template"]))
    for finding in findings:
        print("  %s: %s" % (finding["code"], finding["detail"]), file=sys.stderr)
    print("baseline       %s (%d error findings)"
          % ("frozen" if errors == 0 else "REFUSED", errors))
    print("manifest       %s" % manifest_path.as_posix())
    print("receipt        %s" % receipt_path.as_posix())

    return 0 if errors == 0 else 5


def screenshot_record() -> dict | None:
    path = WORKBENCH / "docs" / "assets" / "current-circuit-workbench.png"
    if not path.is_file():
        return None
    return {
        "path": path.relative_to(WORKBENCH).as_posix(),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "viewport": {"width": 1220, "height": 827},
    }


def verify(declaration: dict, out_dir: Path) -> int:
    """Re-digest every retained artifact against the manifest that recorded it."""
    manifest_path = BASELINE / "baseline-manifest.json"
    if not manifest_path.is_file():
        print("BASELINE_MANIFEST_MISSING: %s" % manifest_path.as_posix(), file=sys.stderr)
        return 5
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    checked, drift = 0, []
    records = ([manifest["artifacts"]["catalog"]] if manifest["artifacts"]["catalog"] else [])
    records += [v["artifact"] for v in manifest["artifacts"]["views"] if v.get("artifact")]
    records += manifest["artifacts"]["materials"] + manifest["artifacts"]["template"]
    if manifest.get("referenceScreenshot"):
        records.append(manifest["referenceScreenshot"])
    if manifest.get("controlBehavior"):
        records.append(manifest["controlBehavior"])

    for record in records:
        path = WORKBENCH / (record.get("retained") or record["path"])
        checked += 1
        if not path.is_file():
            drift.append({"code": "BASELINE_ARTIFACT_MISSING", "path": path.as_posix()})
        elif sha256_file(path) != record["sha256"]:
            drift.append({"code": "BASELINE_ARTIFACT_DIGEST_DRIFT", "path": path.as_posix()})

    out_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = out_dir / "baseline-verification.receipt.json"
    receipt_path.write_text(json.dumps({
        "receiptType": "baseline-verification-receipt.v1",
        "verifiedAt": now_utc(),
        "manifest": {"path": manifest_path.as_posix(), "sha256": sha256_file(manifest_path)},
        "summary": {"checked": checked, "drift": len(drift), "intact": not drift},
        "findings": drift,
    }, indent=2) + "\n", encoding="utf-8")

    for entry in drift:
        print("  %s: %s" % (entry["code"], entry["path"]), file=sys.stderr)
    print("verification   %d artifacts checked, %d drift" % (checked, len(drift)))
    print("receipt        %s" % receipt_path.as_posix())
    return 0 if not drift else 5


if __name__ == "__main__":
    raise SystemExit(main())
