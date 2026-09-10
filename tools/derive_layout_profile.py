"""Derive a surface variant from a layout profile, and prove nothing else moved.

The plan reserves surface, container and layout declarations as the change point
for relocating a region, and requires that doing so preserve component, state and
action identity. This applies a layout profile to its basis surface and then
verifies exactly that: the derived surface must declare the same component ids,
semantic identities, state ids, binding targets, validation rules, action ids and
scenario events as its basis. Only geometry may differ.

If the derived surface differs in any declared invariant set, the derivation is
refused. That refusal is the proof: a layout profile that could quietly change
an identity would make the separation claim worthless.

Usage
-----
    python tools/derive_layout_profile.py [--profile ID ...]

Exit codes
----------
    0  every profile derived with its invariants intact
   10  a derivation was refused
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent
PROFILES = WORKBENCH / "profiles"
DEFAULT_OUT = WORKBENCH / "experiences" / "derived"

sys.path.insert(0, str(HERE))
from resolve_ui_dependencies import now_utc, sha256_file  # noqa: E402


def walk_containers(container: dict):
    """Yield every container in the tree, parents before children."""
    yield container
    layout = container.get("layout")
    if not layout:
        return
    children = layout.get("tracks") or layout.get("areas") or layout.get("children") or []
    for entry in children:
        child = entry.get("container") if isinstance(entry, dict) else None
        if child:
            yield from walk_containers(child)


def find_container(root: dict, container_id: str) -> dict | None:
    for container in walk_containers(root):
        if container.get("id") == container_id:
            return container
    return None


def entries_of(layout: dict) -> list:
    for key in ("tracks", "areas", "children"):
        if key in layout:
            return layout[key]
    return []


def apply_operation(root: dict, operation: dict, findings: list) -> None:
    container = find_container(root, operation["container"])
    if container is None or not container.get("layout"):
        findings.append({"code": "LAYOUT_TARGET_UNRESOLVED", "severity": "error",
                         "detail": "no laid-out container %r" % operation["container"]})
        return
    entries = entries_of(container["layout"])

    if operation["kind"] == "reorder-tracks":
        by_id = {}
        for entry in entries:
            child = entry.get("container")
            if child:
                by_id[child["id"]] = entry
        if set(by_id) != set(operation["order"]):
            findings.append({
                "code": "LAYOUT_REORDER_INCOMPLETE", "severity": "error",
                "detail": "%s: order %s does not cover children %s"
                          % (operation["container"], sorted(operation["order"]), sorted(by_id))})
            return
        entries[:] = [by_id[child_id] for child_id in operation["order"]]

    elif operation["kind"] == "resize-track":
        for entry in entries:
            child = entry.get("container")
            if child and child["id"] == operation["child"]:
                if "size" not in entry:
                    findings.append({"code": "LAYOUT_RESIZE_UNSUPPORTED", "severity": "error",
                                     "detail": "%s is not a sized track" % operation["child"]})
                    return
                entry["size"] = operation["size"]
                return
        findings.append({"code": "LAYOUT_RESIZE_TARGET_MISSING", "severity": "error",
                         "detail": "%s has no child %s" % (operation["container"],
                                                           operation["child"])})
    else:
        findings.append({"code": "LAYOUT_OPERATION_UNSUPPORTED", "severity": "error",
                         "detail": operation["kind"]})


def identity_sets(surface: dict) -> dict:
    components, identities = [], []
    for container in walk_containers(surface["surface"]["rootContainer"]):
        for component in container.get("components", []) or []:
            components.append(component["id"])
            if component.get("semanticIdentity"):
                identities.append(component["semanticIdentity"])
    return {
        "componentIds": sorted(components),
        "semanticIdentities": sorted(identities),
        "stateIds": sorted(s["stateId"] for s in surface.get("state", [])),
        "bindingTargets": sorted("%s->%s" % (b["component"], b["state"])
                                 for b in surface.get("bindings", [])),
        "validationRules": sorted(
            "%s:%s" % (v["state"], r["kind"])
            for v in surface.get("validation", []) for r in v["rules"]),
        "actionIds": sorted(a["actionId"] for a in surface.get("actions", [])),
        "scenarioEvents": sorted(a.get("scenarioEvent", "")
                                 for a in surface.get("actions", [])),
    }


def emit_experience(profile: dict, basis_surface: Path, derived_surface: Path,
                    out_dir: Path) -> dict | None:
    """Derive the variant's experience declaration alongside its surface.

    The variant is generated output, so its experience declaration is generated
    too. Authoring it by hand would leave a file in a generated directory that
    a clean rebuild could not reproduce.

    Its expected-findings register is carried over unchanged on purpose: moving
    a region and changing a theme must not introduce a new finding, so reusing
    the basis register is what makes the build fail if one appears.
    """
    basis_experience = basis_surface.parent / (basis_surface.name.replace(
        ".surface.json", ".experience.json"))
    if not basis_experience.is_file():
        return None

    experience = json.loads(basis_experience.read_text(encoding="utf-8"))
    experience["experienceId"] = profile["surfaceId"]
    experience["title"] = experience["title"] + " · " + profile["title"]
    experience["note"] = (
        "Generated from layout profile " + profile["profileId"] + ". Same experience and the "
        "same declarations, changed only at the two change points the plan reserves: a layout "
        "profile that moves regions, and a presentation profile that changes the theme. The "
        "expected findings are inherited unchanged, because relocating a region and changing a "
        "theme must not produce a new one.")
    experience["declarations"]["surface"] = derived_surface.relative_to(WORKBENCH).as_posix()
    experience["declarations"]["presentationProfile"] = (
        "profiles/" + profile["presentationProfile"] + ".presentation.json")
    experience["declarations"]["layoutProfile"] = (
        "profiles/" + profile["profileId"] + ".layout.json")

    target = out_dir / (profile["surfaceId"] + ".experience.json")
    target.write_text(json.dumps(experience, indent=2) + "\n", encoding="utf-8")
    return {"path": target.relative_to(WORKBENCH).as_posix(), "sha256": sha256_file(target)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", action="append", default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    paths = sorted(PROFILES.glob("*.layout.json"))
    if args.profile:
        wanted = set(args.profile)
        paths = [p for p in paths
                 if json.loads(p.read_text(encoding="utf-8"))["profileId"] in wanted]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    results, failed = [], 0

    for path in paths:
        profile = json.loads(path.read_text(encoding="utf-8"))
        findings: list = []
        basis_path = WORKBENCH / profile["basisSurface"]
        basis = json.loads(basis_path.read_text(encoding="utf-8"))
        derived = json.loads(json.dumps(basis))
        derived["surfaceId"] = profile["surfaceId"]
        derived["title"] = basis["title"] + " · " + profile["title"]
        derived["note"] = (basis["note"] + " Derived by layout profile "
                           + profile["profileId"] + ": " + profile["note"])

        for operation in profile["operations"]:
            apply_operation(derived["surface"]["rootContainer"], operation, findings)

        before, after = identity_sets(basis), identity_sets(derived)
        preserved = {}
        for invariant in profile["invariants"]:
            same = before.get(invariant) == after.get(invariant)
            preserved[invariant] = same
            if not same:
                findings.append({"code": "LAYOUT_PROFILE_CHANGED_IDENTITY", "severity": "error",
                                 "detail": "%s differs between basis and derived surface"
                                           % invariant})

        errors = [f for f in findings if f["severity"] == "error"]
        record = {
            "profileId": profile["profileId"],
            "profile": {"path": path.relative_to(WORKBENCH).as_posix(),
                        "sha256": sha256_file(path)},
            "basis": {"path": profile["basisSurface"], "sha256": sha256_file(basis_path),
                      "surfaceId": basis["surfaceId"]},
            "derivedSurfaceId": profile["surfaceId"],
            "presentationProfile": profile["presentationProfile"],
            "operations": len(profile["operations"]),
            "invariantsPreserved": preserved,
            "findings": findings,
            "derived": None,
        }

        if not errors:
            target = args.out_dir / (profile["surfaceId"] + ".surface.json")
            target.write_text(json.dumps(derived, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            record["derived"] = {"path": target.relative_to(WORKBENCH).as_posix(),
                                 "sha256": sha256_file(target)}
            record["experience"] = emit_experience(profile, basis_path, target, args.out_dir)
        else:
            failed += 1

        results.append(record)
        print("  %-22s %s · %d operations · %d/%d invariants preserved" % (
            profile["profileId"],
            record["derived"]["path"] if record["derived"] else "REFUSED",
            len(profile["operations"]),
            sum(1 for v in preserved.values() if v), len(preserved)))
        for finding in errors:
            print("    %s: %s" % (finding["code"], finding["detail"]), file=sys.stderr)

    receipt = {
        "receiptType": "layout-profile-receipt.v1",
        "derivedAt": now_utc(),
        "statement": ("A layout profile may move and resize regions. It may not change a "
                      "component, state, binding, validation or action identity."),
        "summary": {"profiles": len(results), "derived": len(results) - failed,
                    "refused": failed},
        "profiles": results,
    }
    receipt_dir = WORKBENCH / "evidence" / "profiles"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "layout-profiles.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print("profiles       %d/%d derived" % (len(results) - failed, len(results)))
    print("receipt        %s" % receipt_path.as_posix())
    return 0 if failed == 0 and results else 10


if __name__ == "__main__":
    raise SystemExit(main())
