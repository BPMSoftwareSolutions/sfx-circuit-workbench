"""Turn a compiled dialog plan into a real composed surface.

A plan that never becomes geometry proves nothing, so this emits a
`ui-surface.v1` authority document per dialog and composes it through the same
pinned `sidefx-ui` circuits the workbench shell uses. The input dialog and every
outcome variant go through one path — the shared compilation the plan calls for,
rather than a form page for one and a result panel for the other.

The surface is laid out from the compiled fields, so rearranging or relabelling
a dialog changes only this geometry: component and state identities were fixed
by the compiler from the capability, contract and field pointer, and survive it.

Usage
-----
    python adapters/invocation/build_dialog_surface.py [--ux ID ...]

Exit codes
----------
    0  every dialog composed
   16  a dialog failed to compose, or produced an error finding
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent.parent
COMPILED = WORKBENCH / "experiences" / "invoke" / "compiled"
DEFAULT_OUT = WORKBENCH / "build" / "invoke"

sys.path.insert(0, str(WORKBENCH / "tools"))
from resolve_ui_dependencies import now_utc, select_root, sha256_file  # noqa: E402

DIALOG_WIDTH = 560
FIELD_HEIGHT = 98
HEADER_HEIGHT = 84
FOOTER_HEIGHT = 64
ELEMENT_HEIGHT = 64
GROUP_GAP = 12
GROUP_PADDING = 8


def group_height(count: int) -> int:
    """Height a stacked group needs: its tracks, the gaps between them, and its
    own vertical padding. Omitting the gaps is what produces TRACK_OVERFLOW."""
    if count <= 0:
        return 0
    return (count * ELEMENT_HEIGHT + (count - 1) * GROUP_GAP + GROUP_PADDING * 2)


def field_group_height(count: int) -> int:
    if count <= 0:
        return 0
    return (count * FIELD_HEIGHT + (count - 1) * GROUP_GAP + GROUP_PADDING * 2)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def slot(container_id: str, component: dict) -> dict:
    return {"id": container_id, "components": [component]}


def stack(container_id: str, role: str, tracks: list, gap: int = 12,
          padding=None, direction: str = "column") -> dict:
    container = {"id": container_id, "role": role,
                 "layout": {"kind": "stack", "direction": direction, "gap": gap,
                            "crossAlign": "stretch", "tracks": tracks}}
    if padding is not None:
        container["presentation"] = {"padding": padding}
    return container


def input_surface(plan: dict) -> dict:
    """Compose the generated input dialog: title, fields, then Cancel and Submit."""
    fields = plan["input"]["components"]
    field_components = [c for c in fields if c["id"].startswith("field-")]
    feedback_bindings = []
    field_tracks = []
    for component in field_components:
        binding = next(b for b in plan["input"]["bindings"] if b['component'] == component['id'])
        feedback_id = component['id'] + '-feedback'
        feedback_bindings.append({'bindingId': 'bind-' + feedback_id, 'component': feedback_id,
                                  'state': binding['state'], 'aspect': 'validation', 'direction': 'read'})
        field_tracks.append({'size': f'{FIELD_HEIGHT}px', 'container': stack('slot-' + component['id'], 'dialog-field', [
            {'size': '72px', 'container': slot('control-' + component['id'], component)},
            {'size': '22px', 'container': slot('feedback-' + component['id'], {
                'id': feedback_id, 'componentType': 'feedback', 'semanticIdentity': component['semanticIdentity'] + '#validation',
                'presentationIntent': 'field-feedback', 'content': {'text': '', 'liveRegion': 'polite'}})}], gap=4)})
    submit = next(c for c in fields if c["id"] == "dialog-submit")
    cancel = next(c for c in fields if c["id"] == "dialog-cancel")

    tracks = [{
        "size": "%dpx" % HEADER_HEIGHT,
        "container": stack("dialog-header", "dialog-title", [
            {"size": "18px", "container": slot("dialog-eyebrow", {
                "id": "dialog-context", "componentType": "label",
                "semanticIdentity": "%s#context" % plan["subject"],
                "presentationIntent": "section-label",
                "content": {"text": plan["subject"].upper()}})},
            {"size": "1fr", "container": slot("dialog-heading", {
                "id": "dialog-title", "componentType": "heading",
                "semanticIdentity": "%s#title" % plan["subject"],
                "presentationIntent": "card-title",
                "content": {"text": plan["input"]["dialogTitle"]}})},
        ], gap=6, padding=[16, 20])}]

    if plan["input"].get("description"):
        tracks.append({"size": "44px", "container": stack(
            "dialog-description", "dialog-description",
            [{"size": "1fr", "container": slot("dialog-description-slot", {
                "id": "dialog-description", "componentType": "text",
                "semanticIdentity": "%s#description" % plan["subject"],
                "presentationIntent": "body",
                "content": {"text": plan["input"]["description"]}})}],
            padding=[0, 20])})

    if field_components:
        tracks.append({
            "size": "%dpx" % field_group_height(len(field_components)),
            "container": stack("dialog-fields", "dialog-field-group", field_tracks,
                gap=GROUP_GAP, padding=[GROUP_PADDING, 20])})

    tracks.append({
        "size": "%dpx" % FOOTER_HEIGHT,
        "container": stack("dialog-footer", "dialog-actions", [
            {"size": "1fr", "container": {"id": "footer-gap", "role": "spacer",
                                          "components": [{
                                              "id": "dialog-status",
                                              "componentType": "feedback",
                                              "semanticIdentity": "%s#status" % plan["subject"],
                                              "presentationIntent": "live-status",
                                              "content": {"text": "", "liveRegion": "polite"}}]}},
            {"size": "110px", "container": slot("slot-cancel", cancel)},
            {"size": "120px", "container": slot("slot-submit", submit)},
        ], gap=10, padding=[12, 20], direction="row")})

    height = sum(int(t["size"].rstrip("px")) for t in tracks if t["size"].endswith("px"))
    return {
        "surfaceId": "dialog-input-%s" % plan["uxId"],
        "title": "%s · %s" % (plan["input"]["dialogTitle"], plan["subject"]),
        "note": "Generated from the capability's input contract, its input ownership "
                "declaration and its capability UX. Component and state identities derive "
                "from the capability, contract and field pointer, so rearranging this dialog "
                "preserves the draft and any field errors.",
        "surface": {
            "profile": "window", "dimensions": {"width": DIALOG_WIDTH, "height": height},
            "coordinateSpace": "top-left-down", "overflow": "clip",
            "interactionProfile": "pointer",
            "rootContainer": stack("dialog", "capability-input-dialog", tracks, gap=0),
        },
        "state": plan["input"]["state"],
        "bindings": plan["input"]["bindings"] + feedback_bindings,
        "validation": plan["input"]["validation"],
        "actions": plan["input"]["actions"],
    }


def outcome_surface(plan: dict, variant: dict) -> dict:
    """Compose one generated outcome dialog for a declared variant."""
    components = variant["components"]
    heights = []
    for element in variant['elements']:
        family = element['family']
        heights.append(130 if family == 'metric' else 220 if family in ('collection', 'findings') else
                       max(96, 40 + 30 * len(element.get('fields') or element.get('pointers') or {}))
                       if family in ('record', 'evidence') else 88)
    tracks = [{
        "size": "%dpx" % HEADER_HEIGHT,
        "container": stack("outcome-header", "dialog-title", [
            {"size": "18px", "container": slot("outcome-eyebrow", {
                "id": "outcome-context", "componentType": "label",
                "semanticIdentity": "%s#%s.context" % (plan["subject"], variant["outcomeId"]),
                "presentationIntent": "section-label",
                "content": {"text": variant["contract"].upper()}})},
            {"size": "1fr", "container": slot("outcome-heading", {
                "id": "outcome-title", "componentType": "heading",
                "semanticIdentity": "%s#%s.title" % (plan["subject"], variant["outcomeId"]),
                "presentationIntent": "card-title",
                "content": {"text": variant["dialogTitle"]}})},
        ], gap=6, padding=[16, 20])}]

    if components:
        tracks.append({
            "size": "%dpx" % (sum(heights) + (len(components)-1)*GROUP_GAP + GROUP_PADDING*2),
            "container": stack("outcome-elements", "outcome-layout", [
                {"size": "%dpx" % height,
                 "container": slot("slot-" + component["id"], component)}
                for component, height in zip(components, heights)],
                gap=GROUP_GAP, padding=[GROUP_PADDING, 20])})

    tracks.append({
        "size": "56px",
        "container": stack("outcome-evidence", "evidence-note", [
            {"size": "1fr", "container": slot("outcome-evidence-slot", {
                "id": "outcome-evidence-note", "componentType": "text",
                "semanticIdentity": "%s#%s.evidence-limit" % (plan["subject"],
                                                              variant["outcomeId"]),
                "presentationIntent": "fine-print",
                "content": {"text": "This outcome is retained testimony for its run. "
                                    "Its presentation may change; its meaning may not."}})}],
            padding=[8, 20])})
    tracks.append({'size': '60px', 'container': stack('outcome-footer', 'dialog-actions', [
        {'size': '1fr', 'container': {'id': 'outcome-spacer'}},
        {'size': '120px', 'container': slot('outcome-close-slot', {'id': 'outcome-close', 'componentType': 'action',
            'semanticIdentity': plan['subject'] + '#dismiss-outcome', 'presentationIntent': 'secondary-action',
            'content': {'label': 'Close'}, 'interaction': {'semanticAction': 'dismiss-outcome'}})}
    ], direction='row', padding=[10,20])})

    height = sum(int(t["size"].rstrip("px")) for t in tracks if t["size"].endswith("px"))
    return {
        "surfaceId": "dialog-outcome-%s-%s" % (plan["uxId"], variant["outcomeId"]),
        "title": "%s · %s" % (variant["dialogTitle"], variant["outcomeId"]),
        "note": "Generated from the permitted outcome contract and the declared outcome UX. "
                "Every component is read-only: an outcome is testimony, and a user may change "
                "how it is presented but not what it means."
                + (" This is a declared negative domain outcome and has its own layout."
                   if variant["isNegative"] else ""),
        "surface": {
            "profile": "window", "dimensions": {"width": DIALOG_WIDTH, "height": height},
            "coordinateSpace": "top-left-down", "overflow": "clip",
            "interactionProfile": "read-only",
            "rootContainer": stack("outcome-dialog", "capability-outcome-dialog", tracks, gap=0),
        },
        "state": variant["state"],
        "bindings": variant["bindings"],
        'actions': [{'actionId': 'dismiss-outcome', 'meaning': 'Close this result and retain its evidence.',
                     'requiresAdmissible': [], 'scenarioEvent': 'workbench.outcome.dismissed'}],
    }


def compose(surface_path: Path, manifest: dict, root: Path, declaration: Path,
            out_dir: Path) -> tuple[int, list]:
    composition = manifest["composition"]
    workspace = root / composition["folder"]
    result = subprocess.run([
        sys.executable, str(workspace / composition["module"]),
        "--authority", str(surface_path),
        "--schema", str(workspace / composition["schemas"]["authority"]),
        "--capability-schema", str(workspace / composition["schemas"]["capability"]),
        "--declaration", str(declaration),
        "--out-dir", str(out_dir),
    ], capture_output=True, text=True)
    surface_id = load(surface_path)["surfaceId"]
    receipt = out_dir / (surface_id + ".receipt.json")
    findings = []
    if receipt.is_file():
        findings = load(receipt).get("findings", []) or []
    if result.returncode != 0:
        findings.append({"code": "DIALOG_COMPOSITION_FAILED", "severity": "error",
                         "detail": (result.stderr or result.stdout).strip()[-600:]})
    return result.returncode, findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None)
    parser.add_argument("--ux", action="append", default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    manifest = load(WORKBENCH / "dependencies" / "ui-dependencies.manifest.json")
    root, _ = select_root(manifest, args.root)
    declaration = (WORKBENCH / "evidence" / "dependencies"
                   / "compose-ui-surface.resolved-declaration.json")
    if not declaration.is_file():
        print("DEPENDENCY_DECLARATION_MISSING: run tools/resolve_ui_dependencies.py first",
              file=sys.stderr)
        return 16

    surfaces_dir = args.out_dir / "surfaces"
    staging = args.out_dir / "stages"
    for directory in (surfaces_dir, staging):
        if directory.exists():
            if not directory.resolve().is_relative_to((WORKBENCH / 'build').resolve()):
                raise ValueError('DIALOG_OUTPUT_OUTSIDE_WORKSPACE')
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)

    paths = sorted(COMPILED.glob("*.dialog-plan.json"))
    if args.ux:
        wanted = set(args.ux)
        paths = [p for p in paths if load(p)["uxId"] in wanted]

    dialogs, failures = [], 0
    for path in paths:
        plan = load(path)
        for kind, surface in ([("input", input_surface(plan))]
                              + [("outcome", outcome_surface(plan, v))
                                 for v in plan["outcomes"]]):
            surface_path = surfaces_dir / (surface["surfaceId"] + ".surface.json")
            surface_path.write_text(json.dumps(surface, indent=2) + "\n", encoding="utf-8")
            code, findings = compose(surface_path, manifest, root, declaration, staging)
            errors = [f for f in findings if f.get("severity") == "error"]
            resolved = staging / (surface["surfaceId"] + ".resolved.json")
            ok = code == 0 and not errors
            if not ok:
                failures += 1

            record = {
                "uxId": plan["uxId"], "kind": kind, "surfaceId": surface["surfaceId"],
                "surface": surface_path.relative_to(WORKBENCH).as_posix(),
                "components": sum(len(c.get("components", []))
                                  for c in [surface["surface"]["rootContainer"]])
                              or None,
                "state": len(surface.get("state", [])),
                "bindings": len(surface.get("bindings", [])),
                "validation": len(surface.get("validation", [])),
                "actions": len(surface.get("actions", [])),
                "composed": ok,
                "errorFindings": len(errors),
                "warningFindings": len(findings) - len(errors),
            }
            if resolved.is_file():
                record["resolved"] = {"path": resolved.relative_to(WORKBENCH).as_posix(),
                                      "sha256": sha256_file(resolved)}
                document = load(resolved)
                record["resolvedExtent"] = document.get("surface", {}).get("resolved")
            dialogs.append(record)

            print("  %-42s %-7s %s · %d state / %d bindings / %d actions" % (
                surface["surfaceId"], kind,
                "composed" if ok else "FAILED", record["state"],
                record["bindings"], record["actions"]))
            for finding in errors[:4]:
                print("    %s: %s" % (finding.get("code"), finding.get("detail")),
                      file=sys.stderr)

    receipt = {
        "receiptType": "dialog-composition-receipt.v1",
        "composedAt": now_utc(),
        "statement": "Every compiled dialog plan is composed through the pinned sidefx-ui "
                     "circuits. Input and outcome dialogs use the same path.",
        "summary": {"dialogs": len(dialogs), "composed": len(dialogs) - failures,
                    "failed": failures,
                    "inputs": sum(1 for d in dialogs if d["kind"] == "input"),
                    "outcomes": sum(1 for d in dialogs if d["kind"] == "outcome")},
        "dialogs": dialogs,
        "notEstablished": [
            "No browser has rendered these dialogs; expansion and collapse are unexercised.",
            "No capability was invoked from them.",
        ],
    }
    receipt_dir = WORKBENCH / "evidence" / "invoke"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "dialog-composition.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print("dialogs        %d/%d composed" % (len(dialogs) - failures, len(dialogs)))
    print("receipt        %s" % receipt_path.as_posix())
    return 0 if failures == 0 and dialogs else 16


if __name__ == "__main__":
    raise SystemExit(main())
