"""Resolve a workbench presentation profile into a provider profile.

A presentation profile declares only what changes: palette, base styles, role
and intent styles, and any component realization the workbench adds. It is
merged over the pinned `sidefx-ui` HTML provider, which stays unmodified.

This is the change point the plan reserves for colour, material, typography and
density. Swapping the profile must alter realization only — it may not add or
remove a component, change a semantic identity, or move a region. The build
enforces that: a profile whose merge would change the realizable component set
beyond its own declared additions is refused.

Usage
-----
    python providers/build_provider_profile.py [--profile ID ...]

Exit codes
----------
    0  every profile resolved
    8  a profile was missing, malformed, or attempted a disallowed change
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent
PROFILES = WORKBENCH / "profiles"
DEFAULT_OUT = WORKBENCH / "providers" / "resolved"

sys.path.insert(0, str(WORKBENCH / "tools"))
from resolve_ui_dependencies import now_utc, select_root, sha256_file  # noqa: E402

# Keys a presentation profile may contribute. Anything else is a change of a
# different kind and belongs at a different change point.
MERGEABLE = {"baseStyles", "outlineStyles", "roleStyles", "intentStyles", "components"}
REPLACED = {"baseStyles", "outlineStyles"}


def resolve_basis(manifest: dict, root_argument: str | None, profile: dict) -> Path:
    root, _ = select_root(manifest, root_argument)
    return root / profile["basis"]["path"]


def merge(base: dict, profile: dict, findings: list) -> dict:
    resolved = json.loads(json.dumps(base))
    resolved["providerId"] = profile["providerId"]
    resolved["title"] = profile.get("title", base.get("title"))
    resolved["derivedFrom"] = {
        "providerId": base["providerId"],
        "presentationProfile": profile["profileId"],
    }

    for key in MERGEABLE:
        contribution = profile.get(key)
        if contribution is None:
            continue
        if key in REPLACED:
            resolved[key] = contribution
        else:
            merged = dict(resolved.get(key) or {})
            merged.update(contribution)
            resolved[key] = merged

    # A presentation profile adds realizations; it must not remove or redefine
    # one the basis already provides, which would be a semantic change wearing
    # a presentation profile's clothes.
    added = set((profile.get("components") or {}))
    redefined = added & set(base.get("components") or {})
    if redefined:
        findings.append({
            "code": "PRESENTATION_PROFILE_REDEFINES_REALIZATION",
            "severity": "error",
            "detail": "profile redefines basis realizations: " + ", ".join(sorted(redefined)),
        })
    missing = set(base.get("components") or {}) - set(resolved.get("components") or {})
    if missing:
        findings.append({
            "code": "PRESENTATION_PROFILE_REMOVES_REALIZATION",
            "severity": "error",
            "detail": "profile removes basis realizations: " + ", ".join(sorted(missing)),
        })
    return resolved


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path,
                        default=WORKBENCH / "dependencies" / "ui-dependencies.manifest.json")
    parser.add_argument("--root", default=None)
    parser.add_argument("--profile", action="append", default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    paths = sorted(PROFILES.glob("*.presentation.json"))
    if args.profile:
        wanted = set(args.profile)
        paths = [p for p in paths if json.loads(p.read_text(encoding="utf-8"))["profileId"] in wanted]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    results, failed = [], 0

    for path in paths:
        profile = json.loads(path.read_text(encoding="utf-8"))
        findings: list = []
        basis_path = resolve_basis(manifest, args.root, profile)

        if not basis_path.is_file():
            findings.append({"code": "PRESENTATION_BASIS_UNRESOLVED", "severity": "error",
                             "detail": basis_path.as_posix()})
            base = {}
        else:
            base = json.loads(basis_path.read_text(encoding="utf-8"))
            if base.get("providerId") != profile["basis"]["providerId"]:
                findings.append({"code": "PRESENTATION_BASIS_IDENTITY_MISMATCH",
                                 "severity": "error",
                                 "detail": "basis declares %r" % base.get("providerId")})

        resolved = merge(base, profile, findings) if base else {}
        errors = [f for f in findings if f["severity"] == "error"]

        record = {
            "profileId": profile["profileId"],
            "profile": {"path": path.relative_to(WORKBENCH).as_posix(),
                        "sha256": sha256_file(path)},
            "basis": ({"path": basis_path.as_posix(), "sha256": sha256_file(basis_path)}
                      if basis_path.is_file() else {"path": basis_path.as_posix()}),
            "providerId": profile["providerId"],
            "findings": findings,
            "resolved": None,
        }

        if not errors:
            target = args.out_dir / (profile["profileId"] + ".provider.json")
            target.write_text(json.dumps(resolved, indent=2) + "\n", encoding="utf-8")
            record["resolved"] = {"path": target.relative_to(WORKBENCH).as_posix(),
                                  "sha256": sha256_file(target)}
            record["realizableTypes"] = sorted(resolved.get("components") or {})
            record["addedTypes"] = sorted(set(profile.get("components") or {}))
        else:
            failed += 1

        results.append(record)
        print("  %-32s %s%s" % (
            profile["profileId"],
            "-> " + record["resolved"]["path"] if record["resolved"] else "REFUSED",
            "" if not record.get("addedTypes") else "  (+%s)" % ", ".join(record["addedTypes"])))
        for finding in errors:
            print("    %s: %s" % (finding["code"], finding["detail"]), file=sys.stderr)

    receipt = {
        "receiptType": "presentation-profile-receipt.v1",
        "resolvedAt": now_utc(),
        "summary": {"profiles": len(results), "resolved": len(results) - failed,
                    "refused": failed},
        "profiles": results,
    }
    receipt_dir = WORKBENCH / "evidence" / "providers"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "presentation-profiles.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print("profiles       %d/%d resolved" % (len(results) - failed, len(results)))
    print("receipt        %s" % receipt_path.as_posix())
    return 0 if failed == 0 and results else 8


if __name__ == "__main__":
    raise SystemExit(main())
