"""Build one workbench experience from its declarations.

    surface authority ─┐
    presentation       ├─> compose ─> project ─> lower interaction ─> package
    scenes ────────────┘

Nothing in this pipeline branches on a capability name, a view id or a control
id. Composition, projection and interaction lowering are the pinned `sidefx-ui`
capabilities; this tool sequences them, supplies the workbench provider profile
and scene artifacts, and assembles the result.

Findings are checked against the experience's `expectedFindings` register. A
finding the experience declares and explains is a recorded boundary. A finding
it does not declare fails the build, which is what keeps the register honest.

Usage
-----
    python tools/build_workbench.py [--experience PATH] [--root DIR]

Exit codes
----------
    0  built, with only expected findings
    9  a stage failed, or an undeclared finding appeared
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent
DEFAULT_EXPERIENCE = (WORKBENCH / "experiences" / "source-inspection"
                      / "source-inspection.experience.json")
DEFAULT_OUT = WORKBENCH / "build"

sys.path.insert(0, str(HERE))
from resolve_ui_dependencies import now_utc, select_root, sha256_file  # noqa: E402

def resolve_text_refs(node, pack, findings, path="/"):
    """Substitute `textRef` against the pack before composition.

    A static label must exist in the projected HTML before any script runs, so a
    surface cannot wait for the runtime to supply it. Referencing the pack keeps
    one owner for the text while still producing static markup: the surface says
    which text it wants, the pack says what that text is.
    """
    if isinstance(node, dict):
        reference = node.get("textRef")
        if isinstance(reference, str):
            group, _, name = reference.partition(".")
            value = (pack.get(group) or {}).get(name)
            if value is None:
                findings.append({"code": "TEXT_REF_UNDECLARED", "stage": "package",
                                 "severity": "error", "subject": path,
                                 "detail": "surface references %r; the pack has no such entry"
                                           % reference})
            else:
                node = {k: v for k, v in node.items() if k != "textRef"}
                node["label" if "label" in node else "text"] = value
                return node
        return {k: resolve_text_refs(v, pack, findings, path + str(k) + "/")
                for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_text_refs(v, pack, findings, path + str(i) + "/")
                for i, v in enumerate(node)]
    return node


def fill_template(template: str, values: dict, glyphs: dict) -> str:
    """Fill `{name}` from supplied values, falling back to the pack's glyphs."""
    def substitute(match):
        name = match.group(1)
        if name in values:
            return str(values[name])
        return glyphs.get(name, match.group(0))
    return re.sub(r"\{([a-zA-Z0-9_]+)\}", substitute, template)


def option_source_rows(source: str, catalogue: list, estate: list, initial: str) -> list:
    """The data a declared option source draws from.

    A choice says which data names its options; the build supplies the rows. The
    surface therefore declares no capability, scenario or view by name, and the
    markup still ships with its options filled.
    """
    if source == "scenes":
        return None  # labelled in place against the packaged catalogue
    if source == "capabilities":
        return [{"value": c["capabilityId"], "capabilityId": c["capabilityId"],
                 "views": len(c["views"]),
                 "affordance": ("capabilityInvocable" if "invoke" in c["affordances"]
                                else "capabilityInspectOnly")}
                for c in estate]
    if source == "views":
        record = next((c for c in estate if c["capabilityId"] == initial), None)
        return [{"value": v["viewId"], "label_": v["label"],
                 "nodes": v["coverage"]["nodes"], "routes": v["coverage"]["routes"]}
                for v in (record or {}).get("views", [])]
    if source == "scenarios":
        record = next((c for c in estate if c["capabilityId"] == initial), None)
        scenarios = (record or {}).get("scenarios", [])
        return ([{"value": "", "scenarioId": "", "count": len(scenarios), "all": True}]
                + [{"value": s, "scenarioId": s} for s in scenarios])
    return None


def resolve_option_text(node, pack, catalogue, findings, path="/",
                        estate=None, initial=None):
    """Fill each option's label from its declared format and the data at hand.

    Option text is static once the catalogue is known, so it is resolved here
    rather than by the runtime. Leaving it to a script produces a control that is
    blank until the script runs and unusable if it never does.
    """
    if isinstance(node, dict):
        content = node.get("content")
        if isinstance(content, dict) and content.get("optionTextFormat"):
            template = pack["formats"].get(content["optionTextFormat"])
            if template is None:
                findings.append({"code": "TEXT_FORMAT_UNDECLARED", "stage": "package",
                                 "severity": "error", "subject": path,
                                 "detail": "option format %r is not in the pack"
                                           % content["optionTextFormat"]})
            else:
                source = content.get("optionTextSource")
                generated = option_source_rows(source, catalogue, estate or [], initial)
                if generated is not None:
                    content["options"] = generated
                for option in content.get("options", []):
                    values = dict(option)
                    # Per option: a row that needs a different format must not
                    # leave the loop using it for every row after it.
                    row_template = template
                    if source == "views":
                        values["label"] = option.pop("label_", "")
                    elif source == "capabilities":
                        values["affordance"] = pack["messages"].get(
                            option.get("affordance", ""), "")
                    elif source == "scenarios" and option.get("all"):
                        row_template = pack["formats"]["scenarioAll"]
                    elif source == "scenes":
                        entry = next((c for c in catalogue
                                      if c["viewId"] == option["value"]), None)
                        if entry is None:
                            findings.append({
                                "code": "TEXT_OPTION_SOURCE_UNRESOLVED", "stage": "package",
                                "severity": "error", "subject": path,
                                "detail": "no packaged scene for option %r" % option["value"]})
                            continue
                        values.update({"label": entry["label"],
                                       "nodes": entry["coverage"]["nodes"],
                                       "routes": entry["coverage"]["routes"]})
                    option["label"] = fill_template(row_template, values, pack["glyphs"])
                    for key in ("affordance", "all", "count", "capabilityId",
                                "scenarioId", "views", "nodes", "routes", "label_"):
                        option.pop(key, None)
                content = {k: v for k, v in content.items()
                           if k not in ("optionTextFormat", "optionTextSource")}
                node = dict(node, content=content)
        return {k: resolve_option_text(v, pack, catalogue, findings, path + str(k) + "/",
                                       estate, initial)
                for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_option_text(v, pack, catalogue, findings, path + str(i) + "/",
                                    estate, initial)
                for i, v in enumerate(node)]
    return node


def run(argv: list, stage: str, findings: list) -> tuple[int, str]:
    result = subprocess.run([sys.executable] + argv, capture_output=True, text=True,
                            cwd=str(WORKBENCH))
    if result.returncode != 0:
        print((result.stderr or result.stdout).strip()[-1500:], file=sys.stderr)
        findings.append({"code": "BUILD_STAGE_FAILED", "stage": stage, "severity": "error",
                         "detail": (result.stderr or result.stdout).strip()[-1500:]})
    return result.returncode, result.stdout


def collect(receipt_path: Path, stage: str) -> list:
    """Lift findings out of a stage receipt into one comparable shape."""
    if not receipt_path.is_file():
        return []
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    out = []
    for finding in receipt.get("findings", []) or []:
        out.append({
            "code": finding.get("code"),
            "stage": stage,
            "severity": finding.get("severity", "warning"),
            "subject": (finding.get("identity") or finding.get("subject")
                        or finding.get("where") or ""),
            "detail": finding.get("detail") or finding.get("message") or "",
        })
    return out


def reconcile(observed: list, expected: list) -> tuple[list, list]:
    """Split observed findings into declared boundaries and regressions."""
    register = {}
    for entry in expected:
        register.setdefault((entry["code"], entry["stage"]), []).append(entry)

    accounted, undeclared = [], []
    for finding in observed:
        candidates = register.get((finding["code"], finding["stage"]))
        if not candidates:
            undeclared.append(finding)
            continue
        # A subject-specific expectation must match the subject; a general one
        # covers every subject for that code and stage.
        match = None
        for candidate in candidates:
            subject = candidate.get("subject")
            if not subject or subject == finding["subject"] or subject in finding["subject"]:
                match = candidate
                break
        if match is None:
            undeclared.append(finding)
        else:
            accounted.append({**finding, "expectedReason": match["reason"],
                              "resolvedBy": match["resolvedBy"]})
    return accounted, undeclared


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--experience", type=Path, default=DEFAULT_EXPERIENCE)
    parser.add_argument("--root", default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    # Accept a path relative to the workbench as readily as an absolute one.
    args.experience = (args.experience if args.experience.is_absolute()
                       else (WORKBENCH / args.experience)).resolve()
    experience = json.loads(args.experience.read_text(encoding="utf-8"))
    experience_id = experience["experienceId"]
    out_dir = args.out_dir or (DEFAULT_OUT / experience_id)
    staging = out_dir / "stages"
    package = out_dir / "package"
    for directory in (staging, package):
        if not directory.resolve().is_relative_to((WORKBENCH / "build").resolve()):
            raise ValueError("BUILD_OUTPUT_OUTSIDE_WORKSPACE")
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)

    manifest = json.loads((WORKBENCH / experience["uiDependencies"]["manifest"])
                          .read_text(encoding="utf-8"))
    root, root_source = select_root(manifest, args.root)
    compose_workspace = root / manifest["composition"]["folder"]
    findings: list = []
    root_argv = ["--root", args.root] if args.root else []

    print("experience     %s (%s)" % (experience_id, experience["mode"]))
    print("root           %s (%s)" % (root.as_posix(), root_source))

    # --- dependencies, provider profile and scenes ------------------------
    code, _ = run(["tools/resolve_ui_dependencies.py", "--quiet"] + root_argv,
                  "dependencies", findings)
    declaration = WORKBENCH / "evidence" / "dependencies" / "compose-ui-surface.resolved-declaration.json"
    if code != 0 or not declaration.is_file():
        print("dependencies   UNRESOLVED", file=sys.stderr)
        return 9

    text_pack_path = WORKBENCH / experience["declarations"]["textPack"]
    text_pack = json.loads(text_pack_path.read_text(encoding="utf-8"))

    presentation = json.loads(
        (WORKBENCH / experience["declarations"]["presentationProfile"])
        .read_text(encoding="utf-8"))
    profile_id = Path(experience["declarations"]["presentationProfile"]).name.split(".")[0]
    code, _ = run(["providers/build_provider_profile.py", "--profile", profile_id] + root_argv,
                  "provider", findings)
    provider = WORKBENCH / "providers" / "resolved" / (profile_id + ".provider.json")
    if code != 0 or not provider.is_file():
        print("provider       UNRESOLVED", file=sys.stderr)
        return 9

    code, _ = run(["adapters/topology/ingest_topology_view.py"], "scenes", findings)
    if code != 0:
        print("scenes         REFUSED", file=sys.stderr)
        return 9

    # --- compose ----------------------------------------------------------
    surface = WORKBENCH / experience["declarations"]["surface"]
    resolved_surface = json.loads(surface.read_text(encoding="utf-8"))
    estate_path = WORKBENCH / experience["declarations"]["estateCatalogue"]
    estate = json.loads(estate_path.read_text(encoding="utf-8"))

    catalogue = []
    for reference in experience["declarations"]["scenes"]:
        entry = json.loads((WORKBENCH / reference).read_text(encoding="utf-8"))
        catalogue.append({
            "fixtureId": Path(reference).name.replace(".scene.json", ""),
            "viewId": entry["identities"]["viewId"],
            "viewKind": entry["identities"]["viewKind"],
            "label": entry["label"],
            "coverage": {"nodes": entry["coverage"]["nodes"],
                         "routes": entry["coverage"]["routes"],
                         "omittedSourceNodes": entry["coverage"]["omittedSourceNodes"]},
        })

    # Package every capability so the estate is selectable. A view's scene is
    # carried here when it is packaged; otherwise it resolves from the host,
    # which lowers the estate's compiled topology on demand. The estate is far
    # larger than a package, so resolving is the normal path and packaging is
    # the exception — not the other way round.
    scene_binding = json.loads(
        (WORKBENCH / "dependencies/estate-scene.binding.json").read_text(encoding="utf-8"))
    resolver = scene_binding["resolver"]
    estate_scenes = {}
    estate_capabilities = []
    resolved = 0
    for record in estate["capabilities"]:
        views = []
        for view in record["views"]:
            packaged = None
            if view.get("scene") and (WORKBENCH / view["scene"]).is_file():
                packaged = "scenes/%s-%s.scene.json" % (record["capabilityId"], view["viewId"])
                estate_scenes[packaged] = WORKBENCH / view["scene"]
                if view.get("artifact") and (WORKBENCH / view["artifact"]).is_file():
                    estate_scenes["scenes/%s-%s.svg" % (record["capabilityId"], view["viewId"])]                         = WORKBENCH / view["artifact"]
            views.append({
                "viewId": view["viewId"], "viewKind": view["viewKind"],
                "label": view["label"], "scenarioId": view.get("scenarioId"),
                "coverage": view["coverage"], "scene": packaged,
                "derived": view.get("derived", False),
            })
            if packaged is None:
                resolved += 1
        estate_capabilities.append({
            "capabilityId": record["capabilityId"],
            "views": views,
            "scenarios": record.get("scenarios", []),
            "affordances": record["affordances"],
        })

    for target, origin in estate_scenes.items():
        destination = package / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, destination)

    # An estate scene's artifact references its materials by the path the website
    # serves them from. Those are not in this package, so the references are
    # rewritten to the copies beside it and the material presentation keeps
    # working; a reference left pointing at the website would simply 404.
    estate_materials = {}
    for target in list(estate_scenes):
        if not target.endswith(".scene.json"):
            continue
        scene_doc = json.loads((package / target).read_text(encoding="utf-8"))
        artifact = package / target.replace(".scene.json", ".svg")
        if not artifact.is_file():
            continue
        svg_text = artifact.read_text(encoding="utf-8")
        for material in scene_doc.get("materials", []) or []:
            name = material["reference"].rsplit("/", 1)[-1]
            source = WORKBENCH / material["retained"] if material.get("retained") else None
            if source and source.is_file():
                estate_materials[name] = source
                svg_text = svg_text.replace(material["reference"], "../materials/" + name)
        artifact.write_text(svg_text, encoding="utf-8")

    materials_dir = package / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)
    material_count = 0
    for name, origin in estate_materials.items():
        shutil.copyfile(origin, materials_dir / name)
        material_count += 1
    for reference in experience["declarations"]["scenes"]:
        scene = json.loads((WORKBENCH / reference).read_text(encoding="utf-8"))
        for material in scene.get("materials", []):
            source = WORKBENCH / material["retained"]
            if source.is_file():
                shutil.copyfile(source, materials_dir / source.name)
                material_count += 1

    # Prefer a capability that can be both seen and run; fall back to any whose
    # circuit is packaged; only then to the first catalogued.
    def openable(record):
        return any(v["scene"] for v in record["views"])
    initial_capability = next(
        (c["capabilityId"] for c in estate_capabilities
         if openable(c) and "invoke" in c["affordances"]),
        next((c["capabilityId"] for c in estate_capabilities if openable(c)),
             estate_capabilities[0]["capabilityId"] if estate_capabilities else None))

    resolved_surface = resolve_text_refs(resolved_surface, text_pack, findings)
    resolved_surface = resolve_option_text(resolved_surface, text_pack, catalogue, findings,
                                           estate=estate_capabilities,
                                           initial=initial_capability)
    staged_surface = staging / surface.name
    staged_surface.write_text(
        json.dumps(resolved_surface, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    code, output = run([
        str(compose_workspace / manifest["composition"]["module"]),
        "--authority", str(staged_surface),
        "--schema", str(compose_workspace / manifest["composition"]["schemas"]["authority"]),
        "--capability-schema", str(compose_workspace / manifest["composition"]["schemas"]["capability"]),
        "--declaration", str(declaration),
        "--out-dir", str(staging),
    ], "composition", findings)
    surface_id = json.loads(surface.read_text(encoding="utf-8"))["surfaceId"]
    resolved = staging / (surface_id + ".resolved.json")
    if code != 0 or not resolved.is_file():
        print("composition    FAILED", file=sys.stderr)
        return 9
    findings += collect(staging / (surface_id + ".receipt.json"), "composition")

    # --- project ----------------------------------------------------------
    code, _ = run([
        str(root / "sidefx-project-ui-surface" / "project_ui_surface.py"),
        "--resolved", str(resolved), "--provider", str(provider), "--out-dir", str(staging),
    ], "projection", findings)
    html = staging / (surface_id + ".html")
    if code != 0 or not html.is_file():
        print("projection     FAILED", file=sys.stderr)
        return 9
    findings += collect(staging / (surface_id + ".projection.receipt.json"), "projection")

    # --- lower interaction ------------------------------------------------
    code, _ = run([
        str(root / "sidefx-html-interaction" / "realize_html_interaction.py"),
        "--resolved", str(resolved), "--html", str(html), "--out-dir", str(staging),
    ], "interaction", findings)
    interactive = staging / (surface_id + ".interactive.html")
    if code != 0 or not interactive.is_file():
        print("interaction    FAILED", file=sys.stderr)
        return 9
    findings += collect(staging / (surface_id + ".interactive.realization.receipt.json"),
                        "interaction")

    # --- assemble ---------------------------------------------------------
    scenes_dir = package / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    scene_descriptors = []
    for reference in experience["declarations"]["scenes"]:
        scene_path = WORKBENCH / reference
        scene = json.loads(scene_path.read_text(encoding="utf-8"))
        name = scene_path.name.replace(".scene.json", "")
        shutil.copyfile(scene_path, scenes_dir / scene_path.name)
        artifact = WORKBENCH / scene["scene"]["retained"]
        shutil.copyfile(artifact, scenes_dir / artifact.name)
        scene_descriptors.append({
            "fixtureId": name,
            "viewId": scene["identities"]["viewId"],
            "viewKind": scene["identities"]["viewKind"],
            "label": scene["label"],
            "scene": "scenes/" + scene_path.name,
            "artifact": "scenes/" + artifact.name,
            "sha256": sha256_file(scene_path),
            # The option label is formatted from these counts at load, so the
            # catalogue carries the numbers rather than a pre-built string.
            "coverage": {"nodes": scene["coverage"]["nodes"],
                         "routes": scene["coverage"]["routes"],
                         "omittedSourceNodes": scene["coverage"]["omittedSourceNodes"]},
        })

    runtime_source = WORKBENCH / experience["declarations"]["runtime"]
    shutil.copyfile(runtime_source, package / "workbench-runtime.js")
    # Modules the runtime depends on, each separate so it can be checked under
    # Node against the behaviour it is supposed to reproduce. Order matters:
    # every one of these must load before the runtime that consumes it.
    RUNTIME_MODULES = ["text-format.js", "illustrative-trace.js", "overlay-anchor.js",
                       "dialog-lifecycle.js", "focus-containment.js",
                       "overlay-provider.js"]
    for module_name in RUNTIME_MODULES:
        shutil.copyfile(WORKBENCH / "runtime" / module_name, package / module_name)

    config = {
        "experienceId": experience_id,
        "surfaceId": surface_id,
        "initialViewId": scene_descriptors[0]["viewId"] if scene_descriptors else None,
        "text": text_pack,
        "scenes": scene_descriptors,
        "requiredProviderFeatures": experience["requiredProviderFeatures"],
        "traceMode": "ILLUSTRATIVE",
        "embed": {"reportHeight": True, "commandChannel": False},
        "overlay": presentation.get("overlay", {}),
        "estate": {
            "catalogueVersion": estate["catalogueVersion"],
            "available": estate["available"],
            "invocable": estate["invocable"],
            "capabilities": estate_capabilities,
            # Declared, not assumed: the runtime asks the binding where an
            # unpackaged circuit comes from rather than knowing a path.
            "sceneResolver": resolver,
        },
        "initialCapabilityId": initial_capability,
        "evidenceLimit": ("Declared topology. This experience invokes no capability and "
                          "establishes no execution evidence."),
    }

    document = interactive.read_text(encoding="utf-8")
    injection = (
        '<script type="application/json" id="sfx-workbench-config">'
        + json.dumps(config).replace("<", "\\u003c")
        + '</script>\n'
        + "".join('<script src="%s"></script>\n' % name for name in RUNTIME_MODULES)
        + '<script src="workbench-runtime.js"></script>\n'
    )
    if "</body>" not in document:
        findings.append({"code": "PACKAGE_INJECTION_POINT_MISSING", "stage": "package",
                         "severity": "error", "subject": surface_id,
                         "detail": "projected document has no </body>"})
    document = document.replace("</body>", injection + "</body>", 1)

    # The material references inside the scene artifact point at the website's
    # media path. Rewrite them to the packaged copies, by digest-named file.
    for descriptor in scene_descriptors:
        scene = json.loads((scenes_dir / Path(descriptor["scene"]).name).read_text(encoding="utf-8"))
        artifact_path = scenes_dir / Path(descriptor["artifact"]).name
        svg = artifact_path.read_text(encoding="utf-8")
        for material in scene.get("materials", []):
            svg = svg.replace(material["reference"],
                              "materials/" + material["reference"].rsplit("/", 1)[-1])
        artifact_path.write_text(svg, encoding="utf-8")

    index = package / "index.html"
    index.write_text(document, encoding="utf-8")

    accounted, undeclared = reconcile(
        [f for f in findings if f.get("stage") != "package"],
        experience.get("expectedFindings", []))
    blocking = [f for f in findings if f["severity"] == "error"
                and f.get("stage") == "package"] + undeclared

    receipt = {
        "receiptType": "workbench-build-receipt.v1",
        "builtAt": now_utc(),
        "experience": {"path": args.experience.relative_to(WORKBENCH).as_posix(),
                       "sha256": sha256_file(args.experience),
                       "experienceId": experience_id, "mode": experience["mode"]},
        "inputs": {
            "surface": {"path": experience["declarations"]["surface"],
                        "sha256": sha256_file(surface),
                        "textResolved": staged_surface.relative_to(WORKBENCH).as_posix()},
            "provider": {"path": provider.relative_to(WORKBENCH).as_posix(),
                         "sha256": sha256_file(provider)},
            "runtime": {"path": experience["declarations"]["runtime"],
                        "sha256": sha256_file(runtime_source)},
            "estateCatalogue": {"path": experience["declarations"]["estateCatalogue"],
                                "sha256": sha256_file(estate_path),
                                "capabilities": len(estate_capabilities),
                                "packagedScenes": len(estate_scenes)},
            "textPack": {"path": experience["declarations"]["textPack"],
                         "packId": text_pack["packId"], "locale": text_pack["locale"],
                         "sha256": sha256_file(text_pack_path)},
            "resolvedDeclaration": {"path": declaration.relative_to(WORKBENCH).as_posix(),
                                    "sha256": sha256_file(declaration)},
        },
        "stages": {
            "resolved": {"path": resolved.relative_to(WORKBENCH).as_posix(),
                         "sha256": sha256_file(resolved)},
            "projected": {"path": html.relative_to(WORKBENCH).as_posix(),
                          "sha256": sha256_file(html)},
            "interactive": {"path": interactive.relative_to(WORKBENCH).as_posix(),
                            "sha256": sha256_file(interactive)},
        },
        "package": {
            "path": package.relative_to(WORKBENCH).as_posix(),
            "index": {"path": index.relative_to(WORKBENCH).as_posix(),
                      "sha256": sha256_file(index)},
            "scenes": len(scene_descriptors),
            "materials": material_count,
        },
        "findings": {
            "expectedAndAccounted": accounted,
            "undeclared": undeclared,
            "blocking": blocking,
        },
        "summary": {
            "observed": len(findings),
            "accounted": len(accounted),
            "undeclared": len(undeclared),
            "built": not blocking,
        },
        "traceMode": "ILLUSTRATIVE",
        "notEstablished": experience.get("notEstablished", []),
    }

    receipt_dir = WORKBENCH / "evidence" / "build"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / (experience_id + ".build.receipt.json")
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print("composed       %s" % resolved.name)
    print("projected      %s via %s" % (html.name, json.loads(
        provider.read_text(encoding="utf-8"))["providerId"]))
    print("interactive    %s" % interactive.name)
    print("package        %s · %d scenes · %d materials"
          % (package.relative_to(WORKBENCH).as_posix(), len(scene_descriptors), material_count))
    print("findings       %d observed · %d accounted · %d undeclared"
          % (len(findings), len(accounted), len(undeclared)))
    for finding in undeclared:
        print("  UNDECLARED %s [%s] %s %s" % (finding["code"], finding["stage"],
                                              finding["subject"], finding["detail"][:90]),
              file=sys.stderr)
    print("build          %s" % ("OK" if not blocking else "FAILED"))
    print("receipt        %s" % receipt_path.as_posix())

    return 0 if not blocking else 9


if __name__ == "__main__":
    raise SystemExit(main())
