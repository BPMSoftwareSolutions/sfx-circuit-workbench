"""Ingest real estate capabilities into the workbench's scene contract.

The workbench previously packaged two scenes from one reference capability and
four synthetic "Run capability" scenes standing in for the invocable ones. That
inverted the point: the capabilities you can actually run had no circuit, and the
circuit you could see could not be run.

This reads the compiled estate topology — the same products the website serves —
and lowers every capability's views into `circuit-scene.v1`. Nothing is
fabricated: node identities, route kinds, geometry, materials and source digests
all come from the estate's own compiler.

Each capability is recorded with what can be done to it:

    inspect   every capability with compiled topology
    invoke    additionally, those the publication declares a subject for

That distinction is real and enforced elsewhere: the command boundary refuses an
unpublished subject. The catalogue states it so the interface can offer the input
node on the capabilities where pressing it will actually reach the estate.

Usage
-----
    python adapters/estate/ingest_capability.py [--capability ID ...] [--all]

Exit codes
----------
    0  every requested capability ingested
   20  a capability was missing, malformed or lost entities during ingestion
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent.parent
CONTRACT = WORKBENCH / "contracts" / "circuit-scene.v1.schema.json"
DEFAULT_OUT = WORKBENCH / "fixtures" / "estate"

sys.path.insert(0, str(WORKBENCH / "tools"))
sys.path.insert(0, str(WORKBENCH / "adapters" / "topology"))
from resolve_ui_dependencies import now_utc, sha256_file  # noqa: E402
from ingest_topology_view import (  # noqa: E402
    build_scene, read_payload,
)

ADAPTER = {"name": "ingest-capability", "version": "1.0.0"}

DEFAULT_PRODUCTS = "C:/lab/repos/sfx-platform/public/media/library/outputs/estate-topology"
APPROVED_PREFIXES = ["/media/library/outputs/estate-topology/textures/"]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def products_root(argument: str | None) -> Path:
    if argument:
        return Path(argument).resolve()
    return Path(os.environ.get("SFX_ESTATE_TOPOLOGY_PRODUCTS", DEFAULT_PRODUCTS)).resolve()


def published_subjects(binding_path: Path) -> dict:
    """Which capabilities the publication makes invocable, and under what pins."""
    binding = load(binding_path)
    publication_path = Path(os.environ.get(
        binding["publication"]["environmentVariable"], binding["publication"]["default"]))
    if not publication_path.is_file():
        return {}
    publication = load(publication_path)
    return {
        pilot["profile"]["subject"]: {
            "publicationId": publication["publicationId"],
            "namespace": pilot["profile"].get("namespace"),
            "inputContract": pilot["profile"].get("inputContract"),
            "scenarioId": (pilot.get("authority") or {}).get("scenarioId"),
            "examples": [e["id"] for e in pilot.get("examples", [])],
            "editablePointers": [i["pointer"] for i in pilot["profile"]["inputs"]
                                 if i["ownership"] == "editable"],
        }
        for pilot in publication["pilots"]
    }


def retain_materials(scene: dict, textures: Path, out_dir: Path, findings: list) -> list:
    """Copy the content-addressed materials a scene references, once each."""
    retained = []
    materials_dir = out_dir / "textures"
    materials_dir.mkdir(parents=True, exist_ok=True)
    for material in scene.get("materials", []):
        name = material["reference"].rsplit("/", 1)[-1]
        source = textures / name
        target = materials_dir / name
        if not source.is_file():
            findings.append({"code": "ESTATE_MATERIAL_MISSING", "severity": "error",
                             "detail": material["reference"]})
            continue
        if not target.is_file():
            target.write_bytes(source.read_bytes())
        material["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        material["retained"] = target.relative_to(WORKBENCH).as_posix()
        retained.append(material)
    return retained


def ingest_capability(capability_id: str, root: Path, out_dir: Path,
                      invocable: dict, schema: dict, findings: list,
                      retain: bool = False) -> dict | None:
    """Catalogue a capability's views, retaining their scenes only when asked.

    The catalogue is what the workbench needs to offer a capability: identity,
    views, scenario list, coverage and what may be done with it. That is a
    fraction of a megabyte for the whole estate. The scenes themselves are large
    and already exist as a product, so they are resolved when a view is opened
    rather than copied here.
    """
    capability_dir = root / capability_id
    catalog_path = capability_dir / "catalog.js"
    if not catalog_path.is_file():
        findings.append({"code": "ESTATE_CAPABILITY_UNAVAILABLE", "severity": "error",
                         "detail": capability_id})
        return None

    catalog = read_payload(catalog_path)
    scenes_dir = out_dir / capability_id
    if retain:
        scenes_dir.mkdir(parents=True, exist_ok=True)
    import jsonschema

    views, kinds = [], {}
    for summary in catalog["views"]:
        source_path = capability_dir / (summary["id"] + ".js")
        if not source_path.is_file():
            findings.append({"code": "ESTATE_VIEW_MISSING", "severity": "error",
                             "detail": "%s/%s" % (capability_id, summary["id"])})
            continue
        payload = read_payload(source_path)
        scene = build_scene(payload, {"scenarioId": summary.get("scenarioId")},
                            capability_id, source_path, {}, APPROVED_PREFIXES)
        # build_scene reports unretained materials against an empty index; the
        # estate adapter retains them itself, so those findings are replaced.
        scene["findings"] = [f for f in scene["findings"]
                             if f["code"] != "SCENE_MATERIAL_UNRETAINED"]
        scene["materials"] = [] if not retain else retain_materials(
            {"materials": [{"reference": r} for r in sorted({
                m for m in re.findall(r'href="([^"]+)"', payload.get("svg", ""))
                if m.startswith(APPROVED_PREFIXES[0])})]},
            root / "textures", scenes_dir, findings)

        svg_path = scenes_dir / (summary["id"] + ".svg")
        if retain:
            svg_path.write_text(payload.get("svg", ""), encoding="utf-8")
            scene["scene"]["retained"] = svg_path.relative_to(WORKBENCH).as_posix()
        else:
            scene["scene"]["retained"] = None

        errors = [f for f in scene["findings"] if f["severity"] == "error"]
        try:
            jsonschema.validate(scene, schema)
        except jsonschema.ValidationError as error:
            errors.append({"code": "ESTATE_SCENE_INVALID", "severity": "error",
                           "detail": error.message})
            findings.append(errors[-1])
        if errors:
            findings.extend(e for e in errors if e not in findings)
            continue

        scene_path = scenes_dir / (summary["id"] + ".scene.json")
        if retain:
            scene_path.write_text(json.dumps(scene, indent=2, ensure_ascii=False) + "\n",
                                  encoding="utf-8")
        kinds[scene["identities"]["viewKind"]] = kinds.get(scene["identities"]["viewKind"], 0) + 1
        views.append({
            "viewId": summary["id"],
            "viewKind": scene["identities"]["viewKind"],
            "label": scene["label"],
            "scenarioId": summary.get("scenarioId"),
            "coverage": {"nodes": scene["coverage"]["nodes"],
                         "routes": scene["coverage"]["routes"],
                         "omittedSourceNodes": scene["coverage"]["omittedSourceNodes"]},
            # Where this view is resolved from when it is opened. The source is
            # the estate's own product; nothing is copied to catalogue it.
            "source": (capability_dir / (summary["id"] + ".js")).as_posix(),
            # Report what is actually on disk, not what this run happened to
            # write: cataloguing the estate must not erase a working set that a
            # previous retaining run left behind.
            "scene": (scene_path.relative_to(WORKBENCH).as_posix()
                      if scene_path.is_file() else None),
            "artifact": (svg_path.relative_to(WORKBENCH).as_posix()
                         if svg_path.is_file() else None),
            "sha256": (sha256_file(scene_path) if scene_path.is_file() else None),
        })

    if not views:
        return None

    published = invocable.get(capability_id)
    return {
        "capabilityId": capability_id,
        "title": catalog.get("views", [{}])[0].get("label", capability_id),
        "source": catalog.get("source"),
        "views": views,
        "viewKinds": kinds,
        "scenarios": sorted({v["scenarioId"] for v in views if v.get("scenarioId")}),
        # What a person may actually do with this capability, stated rather than
        # discovered by pressing a control that then refuses.
        "affordances": ["inspect"] + (["invoke"] if published else []),
        "publication": published,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--products", default=None)
    parser.add_argument("--capability", action="append", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--retain-scenes", action="store_true",
                        help="Also write each view's scene and materials to disk. Off by "
                             "default: the estate's circuits are already a served product "
                             "and copying 217 capabilities costs 227 MB for nothing.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--binding", type=Path,
                        default=WORKBENCH / "dependencies" / "lab-publication.binding.json")
    args = parser.parse_args(argv)

    root = products_root(args.products)
    if not root.is_dir():
        print("ESTATE_PRODUCTS_UNRESOLVED: %s" % root, file=sys.stderr)
        return 20

    invocable = published_subjects(args.binding)
    schema = load(CONTRACT)
    findings: list = []

    available = sorted(p.name for p in root.iterdir()
                       if p.is_dir() and (p / "catalog.js").is_file())
    if args.all:
        wanted = available
    elif args.capability:
        wanted = args.capability
    else:
        # Default to what can actually be run, plus the reference capability.
        wanted = sorted(set(invocable) & set(available))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    catalogue, failed = [], 0
    for capability_id in wanted:
        record = ingest_capability(capability_id, root, args.out_dir, invocable,
                                   schema, findings, retain=args.retain_scenes)
        if record is None:
            failed += 1
            print("  %-44s REFUSED" % capability_id)
            continue
        catalogue.append(record)
        print("  %-44s %d views %-28s %s" % (
            capability_id, len(record["views"]),
            "(" + ", ".join("%s:%d" % kv for kv in sorted(record["viewKinds"].items())) + ")",
            "invocable" if "invoke" in record["affordances"] else "inspect only"))

    index_path = args.out_dir / "estate-catalogue.json"
    index_path.write_text(json.dumps({
        "catalogueVersion": "estate-catalogue.v1",
        "ingestedAt": now_utc(),
        "products": root.as_posix(),
        "adapter": ADAPTER,
        "available": len(available),
        "ingested": len(catalogue),
        "invocable": sum(1 for c in catalogue if "invoke" in c["affordances"]),
        "capabilities": catalogue,
        "findings": findings,
        "traceMode": "ILLUSTRATIVE",
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    receipt_dir = WORKBENCH / "evidence" / "estate"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / "estate-ingestion.receipt.json").write_text(json.dumps({
        "receiptType": "estate-ingestion-receipt.v1",
        "ingestedAt": now_utc(),
        "products": root.as_posix(),
        "summary": {"available": len(available), "requested": len(wanted),
                    "ingested": len(catalogue), "refused": failed,
                    "invocable": sum(1 for c in catalogue if "invoke" in c["affordances"]),
                    "views": sum(len(c["views"]) for c in catalogue)},
        "catalogue": index_path.relative_to(WORKBENCH).as_posix(),
        "findings": findings,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("estate         %d/%d ingested, %d invocable, %d views"
          % (len(catalogue), len(wanted),
             sum(1 for c in catalogue if "invoke" in c["affordances"]),
             sum(len(c["views"]) for c in catalogue)))
    print("catalogue      %s" % index_path.as_posix())
    return 0 if failed == 0 and catalogue else 20


if __name__ == "__main__":
    raise SystemExit(main())
