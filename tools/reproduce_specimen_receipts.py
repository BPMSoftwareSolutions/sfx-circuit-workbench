"""Reproduce the retained sidefx-ui specimen products against the relocated closure.

M0 asks for the existing specimen receipts to be reproduced against the packaged
dependency closure. Byte identity is unattainable and is not the right criterion:
the resolved product and its receipt embed absolute input paths and a composition
timestamp, and relocating the workspace necessarily changes those fields.

So this tool composes each pinned specimen through the resolved declaration and
compares the result to the retained product field by field. Every difference is
classified against the manifest's `permittedDeltaFields`. A permitted delta is a
recorded relocation or timestamp fact. Anything else is a divergence and fails.

The invariants that must hold exactly are the ones that carry meaning: every
realization digest, every capability version, every semantic authority digest,
all resolved geometry and interaction evidence, and all findings.

Usage
-----
    python tools/reproduce_specimen_receipts.py [--root DIR] [--specimen ID ...]

Exit codes
----------
    0  every specimen reproduced with only permitted deltas
    4  at least one specimen diverged, or could not be composed
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent
DEFAULT_MANIFEST = WORKBENCH / "dependencies" / "ui-dependencies.manifest.json"
DEFAULT_OUT = WORKBENCH / "evidence" / "dependencies"

sys.path.insert(0, str(HERE))
from resolve_ui_dependencies import (  # noqa: E402
    emit_resolved_declaration, now_utc, select_root, sha256_file,
)


def flatten(document, prefix="") -> dict:
    """Flatten a JSON document into JSON-pointer-ish paths for field comparison."""
    flat: dict = {}
    if isinstance(document, dict):
        for key, value in document.items():
            flat.update(flatten(value, prefix + "/" + str(key)))
    elif isinstance(document, list):
        for index, value in enumerate(document):
            flat.update(flatten(value, prefix + "/" + str(index)))
    else:
        flat[prefix] = document
    return flat


def permitted(path: str, patterns: list) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def compare(retained: dict, produced: dict, patterns: list) -> dict:
    """Field-level comparison, splitting differences into permitted and divergent."""
    left, right = flatten(retained), flatten(produced)
    permitted_deltas, divergences = [], []

    for path in sorted(set(left) | set(right)):
        if path not in left:
            divergences.append({"path": path, "kind": "ADDED", "produced": right[path]})
            continue
        if path not in right:
            divergences.append({"path": path, "kind": "REMOVED", "retained": left[path]})
            continue
        if left[path] == right[path]:
            continue
        entry = {"path": path, "retained": left[path], "produced": right[path]}
        (permitted_deltas if permitted(path, patterns) else divergences).append(entry)

    return {
        "comparedFields": len(set(left) | set(right)),
        "identicalFields": sum(1 for p in set(left) & set(right) if left[p] == right[p]),
        "permittedDeltas": permitted_deltas,
        "divergences": divergences,
        "conforms": not divergences,
    }


def compose(root: Path, manifest: dict, declaration: Path, authority: Path,
            out_dir: Path) -> tuple[int, str]:
    composition = manifest["composition"]
    workspace = root / composition["folder"]
    result = subprocess.run(
        [sys.executable, str(workspace / composition["module"]),
         "--authority", str(authority),
         "--schema", str(workspace / composition["schemas"]["authority"]),
         "--capability-schema", str(workspace / composition["schemas"]["capability"]),
         "--declaration", str(declaration),
         "--out-dir", str(out_dir)],
        capture_output=True, text=True,
    )
    return result.returncode, (result.stderr or result.stdout).strip()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--root", default=None)
    parser.add_argument("--specimen", action="append", default=None,
                        help="Reproduce only these surface ids. Repeatable.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    root, root_source = select_root(manifest, args.root)
    if not root.is_dir():
        print("DEPENDENCY_ROOT_UNRESOLVED: " + root.as_posix(), file=sys.stderr)
        return 4

    args.out_dir.mkdir(parents=True, exist_ok=True)
    declaration = emit_resolved_declaration(root, manifest, args.out_dir)
    patterns = manifest["reproductionPolicy"]["permittedDeltaFields"]
    workspace = root / manifest["composition"]["folder"]

    specimens = manifest["specimens"]
    if args.specimen:
        wanted = set(args.specimen)
        specimens = [s for s in specimens if s["surfaceId"] in wanted]

    results = []
    with tempfile.TemporaryDirectory(prefix="sfx-m0-reproduce-") as staging:
        staging_dir = Path(staging)
        for specimen in specimens:
            surface_id = specimen["surfaceId"]
            authority = workspace / specimen["authority"]
            record: dict = {"surfaceId": surface_id, "authority": authority.as_posix()}

            code, message = compose(root, manifest, declaration, authority, staging_dir)
            if code != 0:
                record.update({"composed": False, "exitCode": code, "detail": message,
                               "conforms": False})
                results.append(record)
                continue
            record["composed"] = True

            for product, retained_key in (("resolved", "retainedResolved"),
                                          ("receipt", "retainedReceipt")):
                retained_path = workspace / specimen[retained_key]
                produced_path = staging_dir / ("%s.%s.json" % (surface_id, product))
                if not retained_path.is_file() or not produced_path.is_file():
                    record[product] = {"conforms": False,
                                       "detail": "missing retained or produced product"}
                    continue
                record[product] = compare(
                    json.loads(retained_path.read_text(encoding="utf-8")),
                    json.loads(produced_path.read_text(encoding="utf-8")),
                    patterns,
                )
                record[product]["retained"] = {"path": retained_path.as_posix(),
                                               "sha256": sha256_file(retained_path)}

            record["conforms"] = all(
                record.get(product, {}).get("conforms") for product in ("resolved", "receipt")
            )
            results.append(record)

    conforming = sum(1 for r in results if r["conforms"])
    receipt = {
        "receiptType": "specimen-reproduction-receipt.v1",
        "statement": ("Retained sidefx-ui specimen products recomposed against the "
                      "relocated dependency closure and compared field by field."),
        "reproducedAt": now_utc(),
        "criterion": manifest["reproductionPolicy"]["criterion"],
        "byteIdenticalResolvedProduct": manifest["reproductionPolicy"]["byteIdenticalResolvedProduct"],
        "root": {"path": root.as_posix(), "selectedFrom": root_source},
        "resolvedDeclaration": {"path": declaration.as_posix(),
                                "sha256": sha256_file(declaration)},
        "summary": {
            "specimens": len(results),
            "conformant": conforming,
            "divergent": len(results) - conforming,
            "totalDivergences": sum(
                len(r.get(p, {}).get("divergences", []))
                for r in results for p in ("resolved", "receipt")
            ),
        },
        "specimens": results,
    }

    receipt_path = args.out_dir / "specimen-reproduction.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print("root           %s (%s)" % (root.as_posix(), root_source))
    print("criterion      %s" % receipt["criterion"])
    for record in results:
        if not record["composed"]:
            print("  %-22s COMPOSE FAILED (exit %s)" % (record["surfaceId"], record["exitCode"]))
            continue
        deltas = sum(len(record[p]["permittedDeltas"]) for p in ("resolved", "receipt"))
        divergent = sum(len(record[p]["divergences"]) for p in ("resolved", "receipt"))
        fields = sum(record[p]["comparedFields"] for p in ("resolved", "receipt"))
        print("  %-22s %s  %d fields · %d permitted deltas · %d divergences" % (
            record["surfaceId"], "OK " if record["conforms"] else "FAIL",
            fields, deltas, divergent))
    print("reproduction   %d/%d conformant" % (conforming, len(results)))
    print("receipt        %s" % receipt_path.as_posix())

    return 0 if conforming == len(results) and results else 4


if __name__ == "__main__":
    raise SystemExit(main())
