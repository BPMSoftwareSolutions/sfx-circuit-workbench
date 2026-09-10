"""Resolve the pinned sidefx-ui capability closure from a relocatable root.

The composition declaration inside `sidefx-compose-ui-surface/capability.json`
names its constituents by absolute pre-move path (`C:/lab/sidefx-ui-surface`),
while the inspected folders now live under `C:/lab/sidefx-ui/`. That workspace
describes itself as unmanaged provisional material, so this workbench does not
edit it. Instead the composer accepts `--declaration`, and this tool emits a
resolved declaration whose `constituentWorkspaces` point at the real locations.

Capability identity is the authority. The folder name and the root are location
facts. Every resolved constituent is verified: the declaration must exist, must
declare the expected capability id and version, and its realization module and
semantic authority must be present and digested.

Usage
-----
    python tools/resolve_ui_dependencies.py [--root DIR] [--out-dir DIR]

Exit codes
----------
    0  every pinned capability resolved and verified
    3  one or more capabilities unresolved or mismatched
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent
DEFAULT_MANIFEST = WORKBENCH / "dependencies" / "ui-dependencies.manifest.json"
DEFAULT_OUT = WORKBENCH / "evidence" / "dependencies"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def now_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def select_root(manifest: dict, argument: str | None) -> tuple[Path, str]:
    """Resolution order: --root, then the manifest's environment variable, then default."""
    declared = manifest["root"]
    if argument:
        return Path(argument).resolve(), "argument"
    from_env = os.environ.get(declared["environmentVariable"])
    if from_env:
        return Path(from_env).resolve(), "environment:" + declared["environmentVariable"]
    return Path(declared["default"]).resolve(), "manifest-default"


def resolve_one(root: Path, pin: dict, findings: list) -> dict | None:
    """Resolve and verify a single pinned capability workspace."""
    capability_id = pin["capabilityId"]
    workspace = root / pin["folder"]
    record: dict = {
        "capabilityId": capability_id,
        "declaredFolder": pin["folder"],
        "workspace": workspace.as_posix(),
        "expectedVersion": pin.get("version"),
    }

    declaration_path = workspace / "capability.json"
    if not declaration_path.is_file():
        findings.append({
            "code": "DEPENDENCY_WORKSPACE_UNRESOLVED",
            "severity": "error",
            "capabilityId": capability_id,
            "detail": "no capability.json at " + declaration_path.as_posix(),
        })
        return None

    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    record["declaration"] = {
        "path": declaration_path.as_posix(),
        "sha256": sha256_file(declaration_path),
    }

    # Identity is the authority; a workspace that moved must still declare itself.
    if declaration.get("capabilityId") != capability_id:
        findings.append({
            "code": "DEPENDENCY_IDENTITY_MISMATCH",
            "severity": "error",
            "capabilityId": capability_id,
            "detail": "workspace declares %r" % declaration.get("capabilityId"),
        })
        return None

    observed_version = declaration.get("version")
    record["observedVersion"] = observed_version
    if pin.get("version") and observed_version != pin["version"]:
        findings.append({
            "code": "DEPENDENCY_VERSION_DRIFT",
            "severity": "error",
            "capabilityId": capability_id,
            "detail": "pinned %s, workspace declares %s" % (pin["version"], observed_version),
        })
        return None

    module_name = pin.get("module") or (declaration.get("realizationBinding") or {}).get("module")
    if module_name:
        module_path = workspace / module_name
        if not module_path.is_file():
            findings.append({
                "code": "DEPENDENCY_REALIZATION_MISSING",
                "severity": "error",
                "capabilityId": capability_id,
                "detail": "no realization module at " + module_path.as_posix(),
            })
            return None
        record["realization"] = {
            "module": module_path.as_posix(),
            "sha256": sha256_file(module_path),
        }

    if pin.get("semanticAuthority"):
        authority_path = workspace / pin["semanticAuthority"]
        if not authority_path.is_file():
            findings.append({
                "code": "DEPENDENCY_SEMANTIC_AUTHORITY_MISSING",
                "severity": "error",
                "capabilityId": capability_id,
                "detail": "no semantic authority at " + authority_path.as_posix(),
            })
            return None
        record["semanticAuthority"] = {
            "path": authority_path.as_posix(),
            "sha256": sha256_file(authority_path),
        }

    return record


def emit_resolved_declaration(root: Path, manifest: dict, out_dir: Path) -> Path:
    """Write the composition declaration with relocated constituent workspaces.

    The source declaration is copied and only its location fields are rewritten.
    Nothing in `sidefx-compose-ui-surface` is modified.
    """
    composition = manifest["composition"]
    source = root / composition["folder"] / "capability.json"
    declaration = json.loads(source.read_text(encoding="utf-8"))

    declaration["workspace"] = (root / composition["folder"]).as_posix()
    declaration["constituentWorkspaces"] = {
        pin["capabilityId"]: (root / pin["folder"]).as_posix()
        for pin in manifest["constituents"]
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "compose-ui-surface.resolved-declaration.json"
    target.write_text(json.dumps(declaration, indent=2) + "\n", encoding="utf-8")
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--root", default=None,
                        help="sidefx-ui root; overrides SIDEFX_UI_ROOT and the manifest default.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    root, root_source = select_root(manifest, args.root)

    findings: list = []
    if not root.is_dir():
        findings.append({
            "code": "DEPENDENCY_ROOT_UNRESOLVED",
            "severity": "error",
            "detail": "root does not exist: " + root.as_posix(),
        })

    resolved: dict = {"composition": None, "constituents": [], "downstream": []}
    if root.is_dir():
        resolved["composition"] = resolve_one(root, manifest["composition"], findings)
        for pin in manifest["constituents"]:
            record = resolve_one(root, pin, findings)
            if record is not None:
                record["layer"] = pin.get("layer")
                resolved["constituents"].append(record)
        for pin in manifest["downstream"]:
            record = resolve_one(root, pin, findings)
            if record is not None:
                record["role"] = pin.get("role")
                resolved["downstream"].append(record)

    expected = 1 + len(manifest["constituents"]) + len(manifest["downstream"])
    observed = ((1 if resolved["composition"] else 0)
                + len(resolved["constituents"]) + len(resolved["downstream"]))

    declaration_path = None
    if not findings:
        declaration_path = emit_resolved_declaration(root, manifest, args.out_dir)

    receipt = {
        "receiptType": "ui-dependency-resolution-receipt.v1",
        "resolvedAt": now_utc(),
        "manifest": {
            "path": args.manifest.resolve().as_posix(),
            "sha256": sha256_file(args.manifest),
            "version": manifest["manifestVersion"],
        },
        "root": {"path": root.as_posix(), "selectedFrom": root_source},
        "summary": {
            "expected": expected,
            "resolved": observed,
            "errors": sum(1 for f in findings if f["severity"] == "error"),
            "clean": not findings and observed == expected,
        },
        "resolvedDeclaration": (
            {"path": declaration_path.as_posix(), "sha256": sha256_file(declaration_path)}
            if declaration_path else None
        ),
        "capabilities": resolved,
        "findings": findings,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = args.out_dir / "ui-dependency-resolution.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    if not args.quiet:
        print("root           %s (%s)" % (root.as_posix(), root_source))
        print("manifest       %s" % manifest["manifestVersion"])
        for record in [resolved["composition"]] + resolved["constituents"] + resolved["downstream"]:
            if not record:
                continue
            print("  %-26s %-7s %s" % (
                record["capabilityId"], record.get("observedVersion", "-"),
                record["declaredFolder"]))
        for finding in findings:
            print("  %s: %s %s" % (finding["code"], finding.get("capabilityId", ""),
                                   finding["detail"]), file=sys.stderr)
        print("resolution     %d/%d resolved, %d error findings"
              % (observed, expected, receipt["summary"]["errors"]))
        if declaration_path:
            print("declaration    %s" % declaration_path.as_posix())
        print("receipt        %s" % receipt_path.as_posix())

    return 0 if receipt["summary"]["clean"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
