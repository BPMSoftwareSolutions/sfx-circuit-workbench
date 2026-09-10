"""Verify what M1 can verify without a browser, and say what it cannot.

Four checks, each of which can fail:

  source fidelity   every scene carries the exact node and route identity sets
                    the frozen baseline recorded, with nothing silently omitted
  control parity    every control in the frozen behavior record has a declared
                    owner, and every named owner exists in the surface
  optionality       the layout and theme variant resolves to different geometry
                    while its component, state, action and scenario identity
                    receipts stay identical to the baseline's
  architecture      the circuit component's required provider features are
                    declared and realized, and a provider lacking the circuit
                    produces a visible unsupported result rather than an image

What this cannot establish is recorded rather than implied: no browser runs
here, so visual parity, resolved-versus-observed geometry conformance and
cross-engine interaction parity remain unmeasured.

Usage
-----
    python tools/verify_m1_parity.py [--root DIR]

Exit codes
----------
    0  every check passed
   11  at least one check failed
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent

sys.path.insert(0, str(HERE))
from resolve_ui_dependencies import now_utc, select_root, sha256_file  # noqa: E402
from derive_layout_profile import identity_sets, walk_containers  # noqa: E402


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def check_source_fidelity(findings: list) -> dict:
    """Node and route identity sets must survive ingestion exactly."""
    manifest = load(WORKBENCH / "fixtures" / "baseline" / "baseline-manifest.json")
    scenes = {}
    for record in manifest["artifacts"]["views"]:
        fixture_id = record["fixtureId"]
        scene_path = WORKBENCH / "fixtures" / "scenes" / (fixture_id + ".scene.json")
        if not scene_path.is_file():
            findings.append({"code": "PARITY_SCENE_MISSING", "severity": "error",
                             "detail": fixture_id})
            continue
        scene = load(scene_path)
        observed = record["observed"]

        node_ids = {n["id"] for n in scene["graph"]["nodes"]}
        route_ids = {r["id"] for r in scene["graph"]["routes"]}
        identities = {n["identity"] for n in scene["graph"]["nodes"]}

        checks = {
            "nodeCount": len(node_ids) == observed["nodes"],
            "routeCount": len(route_ids) == observed["edges"],
            "omissionsPreserved": (scene["coverage"]["omittedSourceNodes"] == observed["omittedSourceNodes"]
                                   and scene["coverage"]["omittedSourceEdges"] == observed["omittedSourceEdges"]),
            "identitiesDistinct": len(identities) == len(node_ids),
            "geometryComplete": all(n["id"] in scene["geometry"]["boxes"]
                                    for n in scene["graph"]["nodes"]),
            "everyEntitySelectable": (
                {t["entityId"] for t in scene["hitTargets"]} >= node_ids | route_ids),
            "sourceDigestsRetained": all(
                (n.get("source") or {}).get("sha256") for n in scene["graph"]["nodes"]),
            "traceModeIllustrative": scene["traceMode"] == "ILLUSTRATIVE",
            "representationSeparateFromObservability": (
                scene["coverage"]["representationComplete"] is True
                and scene["coverage"]["observability"]["instrumented"] is False),
        }
        for name, passed in checks.items():
            if not passed:
                findings.append({"code": "PARITY_SOURCE_FIDELITY", "severity": "error",
                                 "detail": "%s: %s" % (fixture_id, name)})
        scenes[fixture_id] = {
            "nodes": len(node_ids), "routes": len(route_ids),
            "hitTargets": len(scene["hitTargets"]), "checks": checks,
            "conforms": all(checks.values()),
        }
    return {"scenes": scenes, "conforms": all(s["conforms"] for s in scenes.values()) and bool(scenes)}


def check_control_parity(findings: list) -> dict:
    """Every recorded control needs a declared owner that actually exists."""
    record = load(WORKBENCH / "fixtures" / "baseline" / "control-behavior.json")
    surface = load(WORKBENCH / "experiences" / "source-inspection" / "source-inspection.surface.json")
    mapping = load(WORKBENCH / "experiences" / "source-inspection" / "control-parity.map.json")

    declared_components = set()
    for container in walk_containers(surface["surface"]["rootContainer"]):
        for component in container.get("components", []) or []:
            declared_components.add(component["id"])
    declared_actions = {a["actionId"] for a in surface.get("actions", [])}
    declared_state = {s["stateId"] for s in surface.get("state", [])}

    recorded = {c["id"] for c in record["controls"]}
    mapped = {entry["control"] for entry in mapping["controls"]}

    for missing in sorted(recorded - mapped):
        findings.append({"code": "PARITY_CONTROL_UNOWNED", "severity": "error",
                         "detail": "recorded control %r has no declared owner" % missing})
    for extra in sorted(mapped - recorded):
        findings.append({"code": "PARITY_CONTROL_UNKNOWN", "severity": "error",
                         "detail": "map names control %r which the record does not contain" % extra})

    for entry in mapping["controls"]:
        for component in entry["components"]:
            if component not in declared_components:
                findings.append({"code": "PARITY_MAP_BROKEN", "severity": "error",
                                 "detail": "%s -> component %s is not declared"
                                           % (entry["control"], component)})
        for action in entry["actions"]:
            if action not in declared_actions:
                findings.append({"code": "PARITY_MAP_BROKEN", "severity": "error",
                                 "detail": "%s -> action %s is not declared"
                                           % (entry["control"], action)})
        for state in entry["state"]:
            if state not in declared_state:
                findings.append({"code": "PARITY_MAP_BROKEN", "severity": "error",
                                 "detail": "%s -> state %s is not declared"
                                           % (entry["control"], state)})

    mapped_components = {c for e in mapping["controls"] for c in e["components"]}
    declared_additions = {a["component"] for a in mapping.get("additions", [])}
    unexplained = declared_components - mapped_components - declared_additions
    for component in sorted(unexplained):
        findings.append({"code": "PARITY_COMPONENT_UNEXPLAINED", "severity": "error",
                         "detail": "%s is declared but neither maps to a recorded control "
                                   "nor is listed as a deliberate addition" % component})

    return {
        "recordedControls": len(recorded),
        "mappedControls": len(mapped),
        "declaredComponents": len(declared_components),
        "declaredActions": len(declared_actions),
        "deliberateAdditions": sorted(declared_additions),
        "conforms": not any(f["code"].startswith("PARITY_CONTROL")
                            or f["code"].startswith("PARITY_MAP")
                            or f["code"] == "PARITY_COMPONENT_UNEXPLAINED" for f in findings),
    }


def check_optionality(findings: list) -> dict:
    """Geometry must move; identity receipts must not."""
    base = WORKBENCH / "build" / "source-inspection" / "stages" / "source-inspection.resolved.json"
    variant = (WORKBENCH / "build" / "source-inspection-inspector-left" / "stages"
               / "source-inspection-inspector-left.resolved.json")
    if not base.is_file() or not variant.is_file():
        findings.append({"code": "OPTIONALITY_BUILD_MISSING", "severity": "error",
                         "detail": "build both experiences before verifying optionality"})
        return {"conforms": False}

    def receipts(resolved: dict) -> dict:
        regions, components = {}, {}
        def walk(node):
            if isinstance(node, dict):
                if "regionPath" in node and "content" in node:
                    regions[node["regionPath"]] = node["content"]
                if "componentId" in node:
                    components[node["componentId"]] = {
                        "componentType": node.get("componentType"),
                        "semanticIdentity": node.get("semanticIdentity"),
                        "residency": (node.get("residency") or {}).get("targetContainer"),
                        "region": (node.get("residency") or {}).get("region"),
                    }
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(resolved)
        return {"regions": regions, "components": components}

    left, right = receipts(load(base)), receipts(load(variant))

    identity_same = (sorted(left["components"]) == sorted(right["components"]))
    semantics_same = all(
        left["components"][cid]["semanticIdentity"] == right["components"][cid]["semanticIdentity"]
        and left["components"][cid]["componentType"] == right["components"][cid]["componentType"]
        for cid in left["components"] if cid in right["components"])

    moved = [cid for cid in left["components"]
             if cid in right["components"]
             and left["components"][cid]["region"] != right["components"][cid]["region"]]

    surface_base = load(WORKBENCH / "experiences" / "source-inspection" / "source-inspection.surface.json")
    surface_variant = load(WORKBENCH / "experiences" / "derived"
                           / "source-inspection-inspector-left.surface.json")
    sets_base, sets_variant = identity_sets(surface_base), identity_sets(surface_variant)
    invariants = {k: sets_base[k] == sets_variant[k] for k in sets_base}

    providers = {
        "base": load(WORKBENCH / "evidence" / "build" / "source-inspection.build.receipt.json")
                ["inputs"]["provider"]["sha256"],
        "variant": load(WORKBENCH / "evidence" / "build"
                        / "source-inspection-inspector-left.build.receipt.json")
                   ["inputs"]["provider"]["sha256"],
    }

    if not identity_same:
        findings.append({"code": "OPTIONALITY_COMPONENT_SET_CHANGED", "severity": "error",
                         "detail": "the variant declares a different component set"})
    if not semantics_same:
        findings.append({"code": "OPTIONALITY_SEMANTICS_CHANGED", "severity": "error",
                         "detail": "a component's type or semantic identity changed"})
    if not moved:
        findings.append({"code": "OPTIONALITY_GEOMETRY_UNCHANGED", "severity": "error",
                         "detail": "no component moved, so this proves nothing"})
    if providers["base"] == providers["variant"]:
        findings.append({"code": "OPTIONALITY_THEME_UNCHANGED", "severity": "error",
                         "detail": "both experiences used the same provider profile"})
    for name, held in invariants.items():
        if not held:
            findings.append({"code": "OPTIONALITY_INVARIANT_BROKEN", "severity": "error",
                             "detail": name})

    return {
        "componentsCompared": len(left["components"]),
        "componentsMoved": len(moved),
        "identitySetPreserved": identity_same,
        "semanticsPreserved": semantics_same,
        "invariants": invariants,
        "providerProfilesDiffer": providers["base"] != providers["variant"],
        "conforms": (identity_same and semantics_same and bool(moved)
                     and providers["base"] != providers["variant"]
                     and all(invariants.values())),
    }


def check_architecture(manifest: dict, root_argument, findings: list) -> dict:
    """Required features declared and realized; the unsupported path visible."""
    experience = load(WORKBENCH / "experiences" / "source-inspection"
                      / "source-inspection.experience.json")
    profile = load(WORKBENCH / "profiles" / "workbench-dark.presentation.json")
    surface = load(WORKBENCH / "experiences" / "source-inspection" / "source-inspection.surface.json")

    circuit = None
    for container in walk_containers(surface["surface"]["rootContainer"]):
        for component in container.get("components", []) or []:
            if component["componentType"] == "circuit":
                circuit = component
    required = set(experience["requiredProviderFeatures"])
    declared_on_component = set((circuit or {}).get("interaction", {})
                                .get("requiredProviderFeatures", []))
    declared_on_profile = set(profile.get("requiredProviderFeatures", []))

    if declared_on_component != required or declared_on_profile != required:
        findings.append({"code": "ARCHITECTURE_FEATURE_SET_DISAGREEMENT", "severity": "error",
                         "detail": "experience, component and provider disagree on required features"})

    build = load(WORKBENCH / "evidence" / "build" / "source-inspection.build.receipt.json")
    projection = load(WORKBENCH / "build" / "source-inspection" / "stages"
                      / "source-inspection.projection.receipt.json")
    # Realizable is not realized: require a counted realization of the circuit.
    realized = projection.get("realizationSummary", {}).get("byComponentType", {})
    circuit_realized = realized.get("circuit", 0) >= 1

    # The refusal path is proven, not assumed: project the same resolved surface
    # through a provider that has never heard of a circuit.
    root, _ = select_root(manifest, root_argument)
    resolved = WORKBENCH / "build" / "source-inspection" / "stages" / "source-inspection.resolved.json"
    refusal = {"attempted": False}
    if resolved.is_file():
        with tempfile.TemporaryDirectory(prefix="sfx-m1-refusal-") as staging:
            result = subprocess.run([
                sys.executable,
                str(root / "sidefx-project-ui-surface" / "project_ui_surface.py"),
                "--resolved", str(resolved),
                "--provider", str(root / "sidefx-project-ui-surface" / "providers" / "html.provider.json"),
                "--out-dir", staging,
            ], capture_output=True, text=True)
            receipt_path = Path(staging) / "source-inspection.projection.receipt.json"
            codes = []
            if receipt_path.is_file():
                codes = [f.get("code") for f in load(receipt_path).get("findings", [])]
            refusal = {
                "attempted": True,
                "providerId": "html-absolute",
                "raised": "UNREALIZED_COMPONENT_TYPE" in codes,
                "exitCode": result.returncode,
            }
            if not refusal["raised"]:
                findings.append({"code": "ARCHITECTURE_UNSUPPORTED_PATH_SILENT", "severity": "error",
                                 "detail": "a provider without the circuit did not refuse it"})

    if not circuit_realized:
        findings.append({"code": "ARCHITECTURE_CIRCUIT_UNREALIZED", "severity": "error",
                         "detail": "the workbench provider did not realize the circuit"})
    if build["summary"]["undeclared"]:
        findings.append({"code": "ARCHITECTURE_UNDECLARED_FINDING", "severity": "error",
                         "detail": "%d undeclared findings" % build["summary"]["undeclared"]})

    return {
        "requiredFeatures": sorted(required),
        "featureSetsAgree": declared_on_component == required == declared_on_profile,
        "circuitRealized": circuit_realized,
        "unsupportedPathProven": refusal,
        "undeclaredFindings": build["summary"]["undeclared"],
        "conforms": (declared_on_component == required == declared_on_profile
                     and circuit_realized and refusal.get("raised", False)
                     and not build["summary"]["undeclared"]),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None)
    args = parser.parse_args(argv)

    manifest = load(WORKBENCH / "dependencies" / "ui-dependencies.manifest.json")
    findings: list = []

    checks = {
        "sourceFidelity": check_source_fidelity(findings),
        "controlParity": check_control_parity(findings),
        "optionality": check_optionality(findings),
        "architecture": check_architecture(manifest, args.root, findings),
    }

    passed = all(check.get("conforms") for check in checks.values())
    receipt = {
        "receiptType": "m1-parity-receipt.v1",
        "verifiedAt": now_utc(),
        "passed": passed,
        "checks": checks,
        "findings": findings,
        "notEstablished": [
            "No browser was run. Visual parity against the reference screenshot is unmeasured.",
            "Resolved-versus-observed geometry conformance is unmeasured; the foundation's "
            "0.5 px tolerance has not been applied to this surface.",
            "Interaction parity across Chromium, Firefox and WebKit is unmeasured.",
            "The captured 15% and 25% camera states remain derivations, not measurements.",
            "No hosted package exists; the Space and website embed are unverified.",
        ],
    }

    receipt_dir = WORKBENCH / "evidence" / "parity"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "m1-parity.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    for name, check in checks.items():
        print("  [%s] %s" % ("x" if check.get("conforms") else " ", name))
    for finding in findings:
        print("    %s: %s" % (finding["code"], finding["detail"]), file=sys.stderr)
    print("parity         %s" % ("PASSED" if passed else "FAILED"))
    print("receipt        %s" % receipt_path.as_posix())
    return 0 if passed else 11


if __name__ == "__main__":
    raise SystemExit(main())
