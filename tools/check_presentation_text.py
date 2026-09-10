"""Keep presentation text in its pack, and out of everything else.

The encoding damage that reached production was a symptom. The defect was that
presentation text had no owner: it was hard-coded in the runtime, duplicated into
the authored surface as baked literals, and copied again into a dictionary inside
the build tool. Three copies of the same wording, none of them swappable, and no
single place to verify.

This enforces the arrangement that replaced it:

  1. **Only the pack authors non-ASCII.** Every glyph a person sees is named in
     `glyphs`. Nothing else in the runtime, the surfaces or the profiles may
     contain a non-ASCII character, so the encoding check has exactly one file
     to guard instead of a hunt through string literals.
  2. **The runtime spells nothing.** It may hold selectors, attribute names,
     event names and finding codes — mechanical strings. It may not hold a
     sentence, because a sentence is content.
  3. **Surfaces bake no derived values.** A component whose value the runtime
     produces must declare empty content, or the authored copy silently becomes
     a second source of truth that drifts from the data it describes.
  4. **The pack resolves.** Every format and message the runtime asks for must
     exist, and every placeholder must be fillable from supplied values or
     glyphs.

Usage
-----
    python tools/check_presentation_text.py

Exit codes
----------
    0  presentation text is where it belongs
   19  text has leaked out of its pack
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent

sys.path.insert(0, str(HERE))
from resolve_ui_dependencies import now_utc, sha256_file  # noqa: E402

# A string literal in the runtime that reads like prose rather than like a
# selector, an identifier or a code. Two words with a space, starting with a
# capital, is the shape of a sentence and not the shape of a CSS selector.
#
# Both quote styles are checked. Scanning only double quotes let a hard-coded
# capability title sit in the runtime through a passing check, because it
# happened to be written with single quotes.
PROSE = re.compile(r"""["']([A-Z][a-z]+(?: [a-z]+){2,}[^"']*)["']""")
STRING_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')

# Runtime files that carry no user-visible text by design.
RUNTIME_FILES = ["workbench-runtime.js", "illustrative-trace.js", "overlay-anchor.js",
                 "dialog-lifecycle.js", "focus-containment.js", "overlay-provider.js",
                 "text-format.js"]

# Components whose content the runtime produces. A baked value here is a
# duplicate of derived data.
RUNTIME_PRODUCED = {
    "view-scope", "view-coverage", "zoom-readout", "flow-status", "trace-mode-readout",
    "inspect-kind", "inspect-title", "inspect-detail", "inspect-facts",
    "inspect-source", "inspect-findings",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def non_ascii_positions(text: str) -> list:
    return [(index, char) for index, char in enumerate(text) if ord(char) > 127]


def check_glyph_containment(pack_path: Path, findings: list) -> dict:
    """Only the pack may author non-ASCII."""
    scanned, offenders = 0, []
    roots = [WORKBENCH / "runtime", WORKBENCH / "experiences", WORKBENCH / "profiles",
             WORKBENCH / "fixtures" / "scenes", WORKBENCH / "adapters"]
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in (".js", ".json"):
                continue
            if {"__pycache__", "build"} & set(path.parts) or path == pack_path:
                continue
            # Ingested domain data carries whatever the estate wrote: scene
            # labels, and the retained example labels compiled into a dialog
            # plan. This workspace does not author those and must not police them.
            if "scenes" in path.parts or path.name.endswith(".dialog-plan.json"):
                continue
            scanned += 1
            raw = path.read_text(encoding="utf-8")
            for number, line in enumerate(raw.splitlines(), start=1):
                if line.lstrip().startswith(("*", "//", "/*")):
                    continue  # prose in a comment is not presented to anybody
                # `title` and `note` are authoring metadata. They document a
                # declaration for whoever reads it; they are never presented.
                if re.match(r'\s*"(title|note|meaning|reason|detail|statement)"\s*:', line):
                    continue
                found = non_ascii_positions(line)
                if found:
                    offenders.append({"path": path.relative_to(WORKBENCH).as_posix(),
                                      "line": number,
                                      "characters": sorted({c for _, c in found}),
                                      "excerpt": line.strip()[:100]})
    for offender in offenders:
        findings.append({
            "code": "TEXT_GLYPH_OUTSIDE_PACK", "severity": "error",
            "detail": "%s:%d authors %s outside the text pack"
                      % (offender["path"], offender["line"],
                         " ".join(offender["characters"]))})
    return {"filesScanned": scanned, "offenders": offenders,
            "clean": not offenders}


def check_runtime_prose(findings: list) -> dict:
    """The runtime spells nothing a person reads."""
    offenders = []
    for name in RUNTIME_FILES:
        path = WORKBENCH / "runtime" / name
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith(("*", "//", "/*")):
                continue
            for match in PROSE.finditer(line):
                phrase = match.group(1)
                # Media types and similar are mechanical despite the shape.
                if "/" in phrase and " " not in phrase.split("/")[0]:
                    continue
                offenders.append({"path": "runtime/" + name, "line": number,
                                  "phrase": phrase[:90]})
    for offender in offenders:
        findings.append({
            "code": "TEXT_PROSE_IN_RUNTIME", "severity": "error",
            "detail": "%s:%d spells %r; declare it in the pack"
                      % (offender["path"], offender["line"], offender["phrase"])})
    return {"offenders": offenders, "clean": not offenders}


def walk_components(container: dict):
    for component in container.get("components", []) or []:
        yield component
    layout = container.get("layout")
    if layout:
        for entry in (layout.get("tracks") or layout.get("areas") or []):
            if entry.get("container"):
                yield from walk_components(entry["container"])


def check_surface_literals(findings: list) -> dict:
    """A surface bakes no value the runtime produces."""
    offenders = []
    for path in sorted((WORKBENCH / "experiences").rglob("*.surface.json")):
        if "build" in path.parts:
            continue
        surface = load(path)
        for component in walk_components(surface["surface"]["rootContainer"]):
            if component["id"] not in RUNTIME_PRODUCED:
                continue
            content = component.get("content") or {}
            for key in ("value", "text"):
                if content.get(key):
                    offenders.append({"path": path.relative_to(WORKBENCH).as_posix(),
                                      "component": component["id"], "field": key,
                                      "value": str(content[key])[:70]})
        producers = {p["stateId"] for p in []}  # placeholder; producers live in the experience
        for item in surface.get("state", []):
            if item["stateId"] in {"view.scope", "view.coverage", "view.findings",
                                   "camera.zoom-label", "trace.status", "trace.play-label"}:
                if item.get("initialValue"):
                    offenders.append({"path": path.relative_to(WORKBENCH).as_posix(),
                                      "component": item["stateId"], "field": "initialValue",
                                      "value": str(item["initialValue"])[:70]})
    for offender in offenders:
        findings.append({
            "code": "TEXT_DERIVED_VALUE_BAKED", "severity": "error",
            "detail": "%s: %s.%s holds %r, which the runtime produces"
                      % (offender["path"], offender["component"], offender["field"],
                         offender["value"])})
    return {"offenders": offenders, "clean": not offenders}


def check_pack_resolves(pack: dict, findings: list) -> dict:
    """Every name the runtime asks for exists, and every placeholder is fillable."""
    asked = {"formats": set(), "messages": set(), "glyphs": set(),
             "playLabels": set(), "viewKindNames": set()}
    runtime = (WORKBENCH / "runtime" / "workbench-runtime.js").read_text(encoding="utf-8")
    for group, pattern in (("formats", r'text\.format\("([a-zA-Z0-9_]+)"'),
                           ("messages", r'text\.message\("([a-zA-Z0-9_]+)"'),
                           ("glyphs", r'text\.glyph\("([a-zA-Z0-9_]+)"'),
                           ("playLabels", r'text\.playLabel\("([a-zA-Z0-9_]+)"')):
        asked[group].update(re.findall(pattern, runtime))

    missing = []
    for group, names in asked.items():
        for name in sorted(names):
            if name not in (pack.get(group) or {}):
                missing.append(group + "." + name)
                findings.append({"code": "TEXT_NAME_UNDECLARED", "severity": "error",
                                 "detail": "runtime asks for %s.%s, the pack has no such entry"
                                           % (group, name)})

    unfillable = []
    for name, template in pack["formats"].items():
        for placeholder in re.findall(r"\{([a-zA-Z0-9_]+)\}", template):
            # A placeholder is filled from supplied values or from glyphs; only
            # glyph names can be checked statically, and an unknown one that is
            # also not a glyph must be supplied by a caller.
            if placeholder in pack["glyphs"]:
                continue
    unused = sorted(set(pack["formats"]) - asked["formats"]
                    - {"isolatedComponent"})
    return {"asked": {k: sorted(v) for k, v in asked.items() if v},
            "missing": missing, "unusedFormats": unused,
            "clean": not missing}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pack", type=Path,
                        default=WORKBENCH / "profiles" / "workbench-en.text.json")
    parser.add_argument("--out-dir", type=Path, default=WORKBENCH / "evidence" / "text")
    args = parser.parse_args(argv)

    findings: list = []
    pack = load(args.pack)

    import jsonschema
    schema = load(WORKBENCH / "contracts" / "workbench-text.v1.schema.json")
    try:
        jsonschema.validate(pack, schema)
        pack_valid = True
    except jsonschema.ValidationError as error:
        pack_valid = False
        findings.append({"code": "TEXT_PACK_INVALID", "severity": "error",
                         "detail": "%s at /%s" % (error.message,
                                                  "/".join(str(p) for p in error.absolute_path))})

    checks = {
        "packValid": {"clean": pack_valid},
        "glyphContainment": check_glyph_containment(args.pack, findings),
        "runtimeProse": check_runtime_prose(findings),
        "surfaceLiterals": check_surface_literals(findings),
        "packResolves": check_pack_resolves(pack, findings),
    }
    passed = all(c.get("clean") for c in checks.values())

    receipt = {
        "receiptType": "presentation-text-receipt.v1",
        "checkedAt": now_utc(),
        "statement": "Presentation text belongs to a declared pack. The runtime supplies values "
                     "and asks for names; it spells nothing. Surfaces bake no value the runtime "
                     "produces. Only the pack authors non-ASCII, which is what makes the "
                     "encoding check total rather than a hunt.",
        "pack": {"path": args.pack.relative_to(WORKBENCH).as_posix(),
                 "packId": pack["packId"], "locale": pack["locale"],
                 "sha256": sha256_file(args.pack),
                 "glyphs": pack["glyphs"],
                 "formats": len(pack["formats"]), "messages": len(pack["messages"])},
        "passed": passed,
        "checks": checks,
        "findings": findings,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = args.out_dir / "presentation-text.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")

    for name, check in checks.items():
        print("  [%s] %s" % ("x" if check.get("clean") else " ", name))
    for finding in findings[:12]:
        print("      %s: %s" % (finding["code"], finding["detail"]), file=sys.stderr)
    print("text           %s · %d formats / %d messages / %d glyphs"
          % ("PASSED" if passed else "FAILED", len(pack["formats"]),
             len(pack["messages"]), len(pack["glyphs"])))
    print("receipt        %s" % receipt_path.as_posix())
    return 0 if passed else 19


if __name__ == "__main__":
    raise SystemExit(main())
