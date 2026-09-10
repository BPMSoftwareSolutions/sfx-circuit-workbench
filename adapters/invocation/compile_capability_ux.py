"""Compile a capability's contracts, ownership and UX into dialog plans.

One compilation path produces both dialogs:

    published subject + pinned input schema + input ownership
                     + capability UX + scene anchors
                                |
                    resolve and check compatibility
                                |
              compile semantic controls, state and actions
                     /                          \\
            input dialog plan          outcome plans by contract/variant

Three sources, three different authorities, and the compiler keeps them apart:

  * the **contract** supplies shape: types, required properties, admissible
    values. It is read from the publication's pinned schema bytes by digest,
    never fetched over the network.
  * the **ownership declaration** supplies permission. A property appearing in
    a schema does not make it editable; only an `editable` ownership entry does.
  * the **UX declaration** supplies presentation. It may group, label, order and
    animate. It may not widen a contract, invent a successful outcome, or make a
    system-owned field editable.

Anything the registry has no entry for stays explicitly unsupported. Unknown
pointers, ambiguous variants and unsupported required controls become
compilation findings *before* the experience is offered for execution, which is
the point of compiling at publish time rather than guessing at runtime.

Usage
-----
    python adapters/invocation/compile_capability_ux.py [--ux ID ...]

Exit codes
----------
    0  every declaration compiled
   13  a declaration was refused
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent.parent
UX_DIR = WORKBENCH / "experiences" / "invoke"
REGISTRY = WORKBENCH / "providers" / "component-registry.v1.json"
CONTRACT = WORKBENCH / "contracts" / "capability-ux.v1.schema.json"
DEFAULT_OUT = WORKBENCH / "experiences" / "invoke" / "compiled"

sys.path.insert(0, str(WORKBENCH / "tools"))
from resolve_ui_dependencies import now_utc, sha256_file  # noqa: E402


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_pointer(document: dict, pointer: str):
    """Walk a JSON pointer through a schema's `properties`, returning the subschema."""
    if pointer in ("", "/"):
        return document
    node = document
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        properties = node.get("properties") if isinstance(node, dict) else None
        if not properties or token not in properties:
            return None
        node = properties[token]
    return node


def required_at(schema: dict, pointer: str) -> bool:
    """Whether the pointer's own parent declares it required."""
    tokens = pointer.lstrip("/").split("/")
    node, name = schema, tokens[-1]
    for token in tokens[:-1]:
        node = (node.get("properties") or {}).get(token)
        if node is None:
            return False
    return name in (node.get("required") or [])


def choose_control(subschema: dict, entry: dict, registry: dict,
                   findings: list, pointer: str, subject: str) -> dict | None:
    """Pick the registered control for a resolved shape, honouring any preference.

    A preference the shape does not support is a finding, never a silent
    substitution: the declaration asked for something the contract cannot mean.
    """
    declared_type = subschema.get("type")
    has_enum = "enum" in subschema
    preference = entry.get("control")

    def matches(control: dict) -> bool:
        accepts = control["accepts"]
        if "ownership" in accepts:
            return False
        wanted = accepts.get("type")
        wanted = [wanted] if isinstance(wanted, str) else (wanted or [])
        if declared_type not in wanted:
            return False
        if accepts.get("enum") and not has_enum:
            return False
        if control.get("rejects", {}).get("enum") and has_enum:
            return False
        if accepts.get("multiline") and preference != control["controlId"]:
            return False
        return True

    candidates = [c for c in registry["inputControls"] if matches(c)]

    # An unsupported shape must stay unsupported rather than degrade to a text box.
    for unsupported in registry["unsupportedInputShapes"]:
        shape = unsupported["shape"]
        if declared_type == shape or (declared_type is None and "oneOf" in subschema):
            findings.append({
                "code": "UX_INPUT_SHAPE_UNSUPPORTED", "severity": "error",
                "subject": subject, "pointer": pointer,
                "detail": "%s: %s" % (shape, unsupported["reason"])})
            return None

    if not candidates:
        findings.append({
            "code": "UX_INPUT_SHAPE_UNSUPPORTED", "severity": "error",
            "subject": subject, "pointer": pointer,
            "detail": "no registered control accepts type %r%s"
                      % (declared_type, " with enum" if has_enum else "")})
        return None

    if preference:
        chosen = [c for c in candidates if c["controlId"] == preference]
        if not chosen:
            findings.append({
                "code": "UX_CONTROL_PREFERENCE_INCOMPATIBLE", "severity": "error",
                "subject": subject, "pointer": pointer,
                "detail": "declaration prefers %r, which does not accept this shape; "
                          "compatible controls are %s"
                          % (preference, ", ".join(c["controlId"] for c in candidates))})
            return None
        return chosen[0]
    return candidates[0]


def compile_input(ux: dict, pilot: dict, registry: dict, findings: list) -> dict:
    """Emit state, components, bindings, validation and actions for the input dialog."""
    subject = ux["subject"]
    schema = pilot["inputSchema"]
    ownership = {i["pointer"]: i for i in pilot["profile"]["inputs"]}
    declared_fields = [f for group in ux["input"].get("groups", []) for f in group["fields"]]

    state, components, bindings, validation = [], [], [], []
    compiled_fields = []

    for entry in declared_fields:
        pointer = entry["pointer"]
        owned = ownership.get(pointer)

        if owned is None:
            findings.append({"code": "UX_POINTER_NOT_DECLARED", "severity": "error",
                             "subject": subject, "pointer": pointer,
                             "detail": "the ownership declaration has no entry for this pointer"})
            continue
        if owned["ownership"] != "editable":
            # This is the rule that stops a declaration from opening a
            # system-owned field by drawing a control for it.
            findings.append({"code": "UX_FIELD_NOT_EDITABLE", "severity": "error",
                             "subject": subject, "pointer": pointer,
                             "detail": "ownership is %r; only an editable field may have a control"
                                       % owned["ownership"]})
            continue

        subschema = resolve_pointer(schema, pointer)
        if subschema is None:
            findings.append({"code": "UX_POINTER_UNRESOLVED", "severity": "error",
                             "subject": subject, "pointer": pointer,
                             "detail": "pointer does not resolve in the pinned input schema"})
            continue

        control = choose_control(subschema, entry, registry, findings, pointer, subject)
        if control is None:
            continue

        missing_ux = [key for key in control.get("requiredUx", [])
                      if not entry.get(key) and not owned.get(key)]
        if missing_ux:
            findings.append({"code": "UX_REQUIRED_METADATA_MISSING", "severity": "error",
                             "subject": subject, "pointer": pointer,
                             "detail": "control %r requires %s"
                                       % (control["controlId"], ", ".join(missing_ux))})
            continue

        # Identities derive from capability, contract and pointer, so rearranging
        # the dialog preserves the draft and any field errors.
        slug = pointer.strip("/").replace("/", "-").lower()
        state_id = "input.%s" % slug
        component_id = "field-%s" % slug

        item = {"stateId": state_id, "semanticType": control["semanticType"],
                "meaning": entry.get("help") or owned.get("label") or pointer,
                "mutability": "mutable"}
        # A schema default may initialise a draft only under the input policy; it
        # is never evidence that a user supplied a value.
        item["initialValue"] = "" if control["semanticType"] in ("text", "selection") else None
        if control["semanticType"] == "boolean":
            item["initialValue"] = None
        state.append(item)

        content = {"label": entry.get("label") or owned.get("label") or pointer}
        if entry.get("placeholder"):
            content["placeholder"] = entry["placeholder"]
        if control["componentType"] == "choice":
            options = subschema.get("enum") or []
            content["options"] = [{"value": v, "label": str(v)} for v in options]

        components.append({
            "id": component_id,
            "componentType": control["componentType"],
            "semanticIdentity": "%s%s" % (subject, pointer),
            "presentationIntent": "dialog-field",
            "content": content,
            "interaction": {"semanticAction": ux["input"]["intents"]["submit"]["actionId"]}
            if control["componentType"] == "action" else {},
        })
        components[-1].pop("interaction", None)

        bindings.append({"bindingId": "bind-%s" % slug, "component": component_id,
                         "state": state_id, "direction": "read-write"})

        rules = []
        is_required = bool(owned.get("required")) or required_at(schema, pointer)
        if is_required:
            rules.append({"kind": "required",
                          "message": "%s is required." % content["label"]})
        for key, kind in (("minLength", "minLength"), ("maxLength", "maxLength"),
                          ("minimum", "minimum"), ("maximum", "maximum")):
            if key in subschema:
                rules.append({"kind": kind, "value": subschema[key]})
        if "pattern" in subschema:
            # `pattern` is the declared rule kind; `matches` is the mechanical op
            # validate-ui-state lowers it to. Emitting the op here would name a
            # kind the taxonomy does not declare.
            rules.append({"kind": "pattern", "value": subschema["pattern"],
                          "message": "%s does not match its declared form." % content["label"]})
        if "enum" in subschema:
            rules.append({"kind": "oneOf", "values": list(subschema["enum"]),
                          "message": "Choose a declared value."})
        if rules:
            validation.append({"validationId": "valid-%s" % slug, "state": state_id,
                               "rules": rules})

        compiled_fields.append({
            "pointer": pointer, "stateId": state_id, "componentId": component_id,
            "control": control["controlId"], "required": is_required,
            "constraints": sorted(r["kind"] for r in rules),
            # Conditional visibility must not bypass required-field validation.
            "visibleWhen": entry.get("visibleWhen"),
        })

    # A retained-example capability selects which fixture runs; the payload
    # itself is server-owned and never leaves the browser.
    example_selector = None
    if ux["input"].get("exampleSelector"):
        options = [{"value": e["id"], "label": e.get("label") or e["id"]}
                   for e in pilot.get("examples", [])]
        if not options:
            findings.append({"code": "UX_EXAMPLE_SELECTOR_EMPTY", "severity": "error",
                             "subject": subject,
                             "detail": "an example selector is declared but the publication "
                                       "retains no examples for this subject"})
        else:
            state.append({"stateId": "input.example", "semanticType": "selection",
                          "meaning": "Which retained example to run.",
                          "mutability": "mutable", "initialValue": options[0]["value"]})
            components.append({
                "id": "field-example", "componentType": "choice",
                "semanticIdentity": "%s#example" % subject,
                "presentationIntent": "dialog-field",
                "content": {"label": ux["input"]["exampleSelector"]["label"],
                            "options": options}})
            bindings.append({"bindingId": "bind-example", "component": "field-example",
                             "state": "input.example", "direction": "read-write"})
            validation.append({"validationId": "valid-example", "state": "input.example",
                               "rules": [{"kind": "required"},
                                         {"kind": "oneOf",
                                          "values": [o["value"] for o in options],
                                          "message": "Choose a retained example."}]})
            example_selector = {"stateId": "input.example", "componentId": "field-example",
                                "options": [o["value"] for o in options]}

    submit, cancel = ux["input"]["intents"]["submit"], ux["input"]["intents"]["cancel"]
    components.append({
        "id": "dialog-submit", "componentType": "action",
        "semanticIdentity": "%s#submit" % subject,
        "presentationIntent": submit.get("presentationIntent", "primary-action"),
        "content": {"label": submit["label"]},
        "interaction": {"semanticAction": submit["actionId"]}})
    components.append({
        "id": "dialog-cancel", "componentType": "action",
        "semanticIdentity": "%s#cancel" % subject,
        "presentationIntent": cancel.get("presentationIntent", "secondary-action"),
        "content": {"label": cancel["label"]},
        "interaction": {"semanticAction": cancel["actionId"]}})

    actions = [
        # Submit requires every editable field to be admissible; Cancel must stay
        # available over an incomplete or invalid draft.
        {"actionId": submit["actionId"], "meaning": "Submit the admitted input for execution.",
         "requiresAdmissible": [f["stateId"] for f in compiled_fields]
                               + ([example_selector["stateId"]] if example_selector else []),
         "scenarioEvent": submit.get("scenarioEvent", "workbench.capability.submitted")},
        {"actionId": cancel["actionId"], "meaning": "Close the dialog without invoking anything.",
         "requiresAdmissible": [],
         "scenarioEvent": cancel.get("scenarioEvent", "workbench.capability.cancelled")},
    ]

    return {"state": state, "components": components, "bindings": bindings,
            "validation": validation, "actions": actions,
            "fields": compiled_fields, "exampleSelector": example_selector,
            "editablePointers": [f["pointer"] for f in compiled_fields]}


def compile_outcomes(ux: dict, pilot: dict, registry: dict, findings: list) -> list:
    """Emit one read-only presentation plan per declared outcome variant."""
    subject = ux["subject"]
    families = {f["family"]: f for f in registry["outcomeFamilies"]}
    unsupported = {f["family"] for f in registry["unsupportedOutcomeFamilies"]}
    plans = []

    seen_matches = set()
    for outcome in ux["outcomes"]:
        components, bindings, state = [], [], []
        match = outcome["match"]
        if outcome['contract'] != pilot['profile']['outcome']['contract'] or match['contractId'] != outcome['contract']:
            findings.append({'code':'UX_OUTCOME_CONTRACT_MISMATCH','severity':'error','subject':subject})
        if match.get('discriminatorPointer') and resolve_pointer(pilot['outcomeSchema'], match['discriminatorPointer']) is None:
            findings.append({'code':'UX_OUTCOME_DISCRIMINATOR_UNKNOWN','severity':'error','subject':subject})
        key = (match["contractId"], match.get("discriminatorPointer"),
               tuple(match.get("discriminatorValues") or []))
        if key in seen_matches:
            findings.append({"code": "UX_OUTCOME_VARIANT_AMBIGUOUS", "severity": "error",
                             "subject": subject,
                             "detail": "two variants match identically: %s" % (key,)})
        seen_matches.add(key)

        for element in outcome["layout"]:
            family = element["family"]
            if family in unsupported:
                findings.append({"code": "UX_OUTCOME_FAMILY_UNSUPPORTED", "severity": "error",
                                 "subject": subject, "detail": family})
                continue
            spec = families.get(family)
            if spec is None:
                findings.append({"code": "UX_OUTCOME_FAMILY_UNKNOWN", "severity": "error",
                                 "subject": subject, "detail": family})
                continue

            pointers = element.get("pointers") or ({"value": element["pointer"]}
                                                   if element.get("pointer") else {})
            paths = list(pointers.values())
            if element.get('itemPath'):
                paths.append(element['itemPath'])
                collection = resolve_pointer(pilot['outcomeSchema'], element['itemPath'])
                if not collection or collection.get('type') != 'array':
                    findings.append({'code':'UX_OUTCOME_COLLECTION_INVALID','severity':'error','subject':subject})
                for field in element.get('fields', []):
                    if not collection or resolve_pointer(collection.get('items', {}), '/' + field.lstrip('/')) is None:
                        findings.append({'code':'UX_OUTCOME_ITEM_FIELD_UNKNOWN','severity':'error','subject':subject,'pointer':field})
            else:
                paths.extend(element.get('fields', []))
            for pointer in paths:
                if not pointer.startswith('/') or resolve_pointer(pilot['outcomeSchema'], pointer) is None:
                    findings.append({'code':'UX_OUTCOME_POINTER_UNKNOWN','severity':'error','subject':subject,'pointer':pointer})
            missing = [p for p in spec.get("requiredPointers", []) if p not in pointers]
            if missing:
                findings.append({"code": "UX_OUTCOME_POINTER_MISSING", "severity": "error",
                                 "subject": subject, "detail": "%s requires %s"
                                                               % (family, ", ".join(missing))})
                continue
            missing_ux = [k for k in spec.get("requiredUx", []) if not element.get(k)]
            if missing_ux:
                findings.append({"code": "UX_OUTCOME_METADATA_MISSING", "severity": "error",
                                 "subject": subject, "detail": "%s requires %s"
                                                               % (family, ", ".join(missing_ux))})
                continue

            state_id = "outcome.%s.%s" % (outcome["outcomeId"], element["elementId"])
            component_id = "outcome-%s-%s" % (outcome["outcomeId"], element["elementId"])
            # Outcome values are read-only testimony: presentation may change,
            # meaning may not.
            state.append({"stateId": state_id, "semanticType": "text",
                          "meaning": element.get("label") or family,
                          "mutability": "fixed", "initialValue": ""})
            content = ({"value": ""} if spec["componentType"] == "value"
                       else {"items": [], "emptyText": element.get("emptyText", "")}
                       if spec["componentType"] == "collection" else {"text": ""})
            components.append({
                "id": component_id, "componentType": spec["componentType"],
                "semanticIdentity": "%s#%s.%s" % (subject, outcome["outcomeId"],
                                                  element["elementId"]),
                "presentationIntent": element.get("presentationIntent", "outcome-" + family),
                "content": content})
            bindings.append({"bindingId": "bind-%s" % component_id.replace("outcome-", "o-"),
                             "component": component_id, "state": state_id, "direction": "read"})

        plans.append({
            "outcomeId": outcome["outcomeId"],
            "contract": outcome["contract"],
            "dialogTitle": outcome.get("dialogTitle", "Capability outcome"),
            "match": match,
            "isNegative": bool(match.get("isNegative")),
            "anchor": outcome["anchor"],
            "state": state, "components": components, "bindings": bindings,
            "elements": [{"elementId": e["elementId"], "family": e["family"],
                          "pointers": e.get("pointers") or ({"value": e["pointer"]}
                                                            if e.get("pointer") else {}),
                          "itemPath": e.get("itemPath"), "fields": e.get("fields"),
                          "label": e.get("label"),
                          # The declared empty state is part of the presentation:
                          # an empty collection must still say what it means.
                          "emptyText": e.get("emptyText"),
                          "dispositionLabels": e.get("dispositionLabels"),
                          "precision": e.get("precision")}
                         for e in outcome["layout"]],
        })
    return plans


def check_pins(ux: dict, pilot: dict, publication: dict, findings: list) -> dict:
    """A pin that no longer matches is a stale publication, reported before execution."""
    pins = ux["pins"]
    observed = {
        "publicationId": publication["publicationId"],
        "inputContract": pilot["profile"]["inputContract"],
        "inputSchemaDigest": pilot["profile"].get("inputSchemaDigest"),
    }
    stale = []
    for key, expected in (("publicationId", pins["publicationId"]),
                          ("inputContract", pins["inputContract"]),
                          ("inputSchemaDigest", pins["inputSchemaDigest"])):
        if observed.get(key) != expected:
            stale.append(key)
            findings.append({
                "code": "UX_PIN_STALE", "severity": "error", "subject": ux["subject"],
                "detail": "%s: declaration pins %r, publication carries %r"
                                  % (key, expected, observed.get(key))})
    expected_outcomes = {pilot["profile"]["outcome"]["contract"]: pilot["profile"]["outcome"]["schemaDigest"]}
    if pins.get("outcomeSchemaDigests") != expected_outcomes:
        stale.append("outcomeSchemaDigests")
        findings.append({"code": "UX_PIN_STALE", "severity": "error", "subject": ux["subject"],
                         "detail": "outcome schemas differ from the publication"})
    if pins.get("providerInputBindingDigest") != pilot["profile"].get("providerInputBindingDigest"):
        stale.append("providerInputBindingDigest")
        findings.append({"code": "UX_PIN_STALE", "severity": "error", "subject": ux["subject"],
                         "detail": "provider binding differs from the publication"})
    return {"observed": observed, "stale": stale, "current": not stale}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--publication", type=Path, default=None)
    parser.add_argument("--ux", action="append", default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    binding = load(WORKBENCH / "dependencies" / "lab-publication.binding.json")
    publication_path = args.publication or Path(binding["publication"]["default"])
    if not publication_path.is_file():
        print("PUBLICATION_UNRESOLVED: %s" % publication_path, file=sys.stderr)
        return 13

    publication = load(publication_path)
    registry = load(REGISTRY)
    schema = load(CONTRACT)
    import jsonschema

    by_subject = {p["profile"]["subject"]: p for p in publication["pilots"]}
    paths = sorted(UX_DIR.glob("*.ux.json"))
    if args.ux:
        wanted = set(args.ux)
        paths = [p for p in paths if load(p)["uxId"] in wanted]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    results, refused = [], 0

    for path in paths:
        ux = load(path)
        findings: list = []
        try:
            jsonschema.validate(ux, schema)
        except jsonschema.ValidationError as error:
            findings.append({"code": "UX_DECLARATION_INVALID", "severity": "error",
                             "subject": ux.get("subject", path.name),
                             "detail": "%s at /%s" % (error.message,
                                                      "/".join(str(p) for p in error.absolute_path))})
            results.append({"uxId": ux.get("uxId"), "findings": findings, "compiled": False})
            refused += 1
            print("  %-34s REFUSED (declaration invalid)" % ux.get("uxId"))
            continue

        pilot = by_subject.get(ux["subject"])
        if pilot is None:
            findings.append({"code": "UX_SUBJECT_NOT_PUBLISHED", "severity": "error",
                             "subject": ux["subject"],
                             "detail": "the publication does not carry this subject"})
            results.append({"uxId": ux["uxId"], "findings": findings, "compiled": False})
            refused += 1
            print("  %-34s REFUSED (subject not published)" % ux["uxId"])
            continue

        pins = check_pins(ux, pilot, publication, findings)
        compiled_input = compile_input(ux, pilot, registry, findings)
        outcome_plans = compile_outcomes(ux, pilot, registry, findings)

        errors = [f for f in findings if f["severity"] == "error"]
        plan = {
            "planVersion": "capability-dialog-plan.v1",
            "uxId": ux["uxId"],
            "subject": ux["subject"],
            "compiledAt": now_utc(),
            "pins": dict(ux["pins"], observed=pins["observed"], current=pins["current"]),
            "input": {
                "dialogTitle": ux["input"]["dialogTitle"],
                "description": ux["input"].get("description"),
                "anchor": ux["input"]["anchor"],
                **{k: compiled_input[k] for k in
                   ("state", "components", "bindings", "validation", "actions",
                    "fields", "exampleSelector", "editablePointers")},
            },
            "outcomes": outcome_plans,
            "delivery": ux.get("delivery", {}),
            "motion": ux.get("motion", {"open": "expand-from-anchor",
                                        "close": "collapse-to-anchor",
                                        "reducedMotionFallback": "none"}),
            "registry": {"path": REGISTRY.relative_to(WORKBENCH).as_posix(),
                         "sha256": sha256_file(REGISTRY),
                         "version": registry["registryVersion"]},
            "findings": findings,
            "executionBoundary": {
                "browserMaySubmit": ["publicationId", "subject", "editableValues", "exampleSelection"],
                "browserMayNotSubmit": ["canonical envelope", "namespace override",
                                        "fixed-field override", "provider inventory",
                                        "endpoint selection"],
                "note": "The server resolves the published subject and constructs canonical "
                        "input. This plan describes a dialog, and grants nothing.",
            },
        }

        if errors:
            refused += 1
        else:
            target = args.out_dir / (ux["uxId"] + ".dialog-plan.json")
            target.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            plan["output"] = target.relative_to(WORKBENCH).as_posix()

        results.append({
            "uxId": ux["uxId"], "subject": ux["subject"],
            "compiled": not errors,
            "pinsCurrent": pins["current"],
            "fields": len(compiled_input["fields"]),
            "exampleSelector": bool(compiled_input["exampleSelector"]),
            "outcomeVariants": len(outcome_plans),
            "actions": len(compiled_input["actions"]),
            "findings": findings,
            "output": plan.get("output"),
        })

        print("  %-34s %s · %d fields%s · %d outcome variants" % (
            ux["uxId"], "compiled" if not errors else "REFUSED",
            len(compiled_input["fields"]),
            " + example selector" if compiled_input["exampleSelector"] else "",
            len(outcome_plans)))
        for finding in errors[:6]:
            print("    %s: %s" % (finding["code"], finding["detail"]), file=sys.stderr)

    receipt = {
        "receiptType": "capability-ux-compilation-receipt.v1",
        "compiledAt": now_utc(),
        "publication": {"path": publication_path.as_posix(),
                        "publicationId": publication["publicationId"],
                        "compiler": publication.get("compiler")},
        "registry": {"version": registry["registryVersion"], "sha256": sha256_file(REGISTRY)},
        "summary": {"declarations": len(results), "compiled": len(results) - refused,
                    "refused": refused},
        "declarations": results,
        "notEstablished": [
            "No capability was invoked. These are dialog plans, not executions.",
            "The publication supplies application policy; it does not grant managed admission.",
        ],
    }
    receipt_dir = WORKBENCH / "evidence" / "invoke"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "capability-ux-compilation.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print("compilation    %d/%d compiled" % (len(results) - refused, len(results)))
    print("receipt        %s" % receipt_path.as_posix())
    return 0 if refused == 0 and results else 13


if __name__ == "__main__":
    raise SystemExit(main())
