"""The workbench's side of the execution port.

An admissible action becomes a `workbench-command.v1`; this resolves it against
the publication, constructs the canonical input the way the server would, and
either admits it or refuses with a distinct reason.

The rule that shapes everything here: **a browser request must not gain
arbitrary subject, namespace, endpoint, fixed-field or fixture-inventory
control.** So this adapter never reads a namespace, an endpoint or a payload out
of the command. It reads a subject and pointer-keyed values, looks up what the
publication says those pointers are, and assembles the rest itself.

Refusals stay distinct on purpose. A stale publication, an undeclared pointer, a
non-editable pointer, an impermissible example and an inadmissible value are
five different facts, and collapsing them into one error would lose the only
information that tells a user what to do next.

This admits and assembles. It does not execute: crossing that boundary needs the
remote command service, and nothing here contacts it. `--emit-canonical` shows
what *would* be sent, which is what makes the assembly reviewable.

Usage
-----
    python adapters/invocation/invocation_adapter.py --command FILE [--emit-canonical]
    python adapters/invocation/invocation_adapter.py --self-check

Exit codes
----------
    0  admitted (or the self-check passed)
    3  refused
   14  the command itself was malformed
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent.parent
CONTRACT = WORKBENCH / "contracts" / "workbench-command.v1.schema.json"
ACCEPTANCE = WORKBENCH / "contracts" / "run-acceptance.v1.schema.json"

sys.path.insert(0, str(WORKBENCH / "tools"))
from resolve_ui_dependencies import now_utc  # noqa: E402


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def refuse(request_id: str, code: str, message: str, pointer: str = None,
           detail: str = None) -> dict:
    refusal = {"code": code, "message": message}
    if pointer:
        refusal["pointer"] = pointer
    if detail:
        refusal["detail"] = detail
    return {"acceptanceVersion": "run-acceptance.v1", "requestId": request_id,
            "disposition": "REFUSED", "refusal": refusal, "acceptedAt": now_utc()}


def set_pointer(document: dict, pointer: str, value) -> None:
    tokens = [t.replace("~1", "/").replace("~0", "~") for t in pointer.lstrip("/").split("/")]
    node = document
    for token in tokens[:-1]:
        node = node.setdefault(token, {})
    node[tokens[-1]] = value


def resolve_schema_pointer(schema: dict, pointer: str):
    node = schema
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        properties = node.get("properties") if isinstance(node, dict) else None
        if not properties or token not in properties:
            return None
        node = properties[token]
    return node


def admissible(value, subschema: dict, pointer: str, required: bool) -> str | None:
    """Re-check a value against its contract. The UI's disabled attribute is a
    drawing of a decision, never the decision itself."""
    if value is None or value == "":
        return "%s is required." % pointer if required else None
    declared = subschema.get("type")
    if declared == "string":
        if not isinstance(value, str):
            return "%s must be a string." % pointer
        if "minLength" in subschema and len(value) < subschema["minLength"]:
            return "%s is shorter than %d." % (pointer, subschema["minLength"])
        if "maxLength" in subschema and len(value) > subschema["maxLength"]:
            return "%s is longer than %d." % (pointer, subschema["maxLength"])
        if "pattern" in subschema and not re.search(subschema["pattern"], value):
            return "%s does not match %s." % (pointer, subschema["pattern"])
    elif declared in ("number", "integer"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "%s must be a number." % pointer
        if "minimum" in subschema and value < subschema["minimum"]:
            return "%s is below %s." % (pointer, subschema["minimum"])
        if "maximum" in subschema and value > subschema["maximum"]:
            return "%s is above %s." % (pointer, subschema["maximum"])
    elif declared == "boolean" and not isinstance(value, bool):
        return "%s must be a boolean." % pointer
    if "enum" in subschema and value not in subschema["enum"]:
        return "%s is not a declared value." % pointer
    return None


class InvocationAdapter:
    """Resolves and admits commands against one publication.

    `runs` is the deduplication store, standing in for the durable
    request-to-run association the service must persist *before* any external
    effect. Holding it in memory is honest for a local check and is exactly what
    must become durable before retries or resumable execution are offered.
    """

    def __init__(self, publication: dict):
        self.publication = publication
        self.by_subject = {p["profile"]["subject"]: p for p in publication["pilots"]}
        self.runs: dict = {}

    def admit(self, command: dict) -> dict:
        request_id = command.get("requestId", "")

        pilot = self.by_subject.get(command["subject"])
        if pilot is None:
            return refuse(request_id, "SUBJECT_NOT_PUBLISHED",
                          "That capability is not published.",
                          detail=command["subject"])

        if command["publicationId"] != self.publication["publicationId"]:
            return refuse(request_id, "PUBLICATION_STALE",
                          "This request was composed against a different publication.",
                          detail="command %s, service %s"
                                 % (command["publicationId"][:23],
                                    self.publication["publicationId"][:23]))

        profile = pilot["profile"]
        ownership = {i["pointer"]: i for i in profile["inputs"]}
        editable = {p for p, i in ownership.items() if i["ownership"] == "editable"}
        supplied = command.get("editableValues") or {}

        # Fixed-field forgery, and pointers that simply are not declared, are two
        # different refusals. Neither is ever ignored.
        for pointer in supplied:
            entry = ownership.get(pointer)
            if entry is None:
                return refuse(request_id, "POINTER_NOT_DECLARED",
                              "That field is not part of this capability's input.",
                              pointer=pointer)
            if entry["ownership"] != "editable":
                return refuse(request_id, "POINTER_NOT_EDITABLE",
                              "That field is owned by the service and cannot be supplied.",
                              pointer=pointer,
                              detail="ownership is %r" % entry["ownership"])

        examples = {e["id"] for e in pilot.get("examples", [])}
        selection = command.get("exampleSelection")
        if selection is not None and selection not in examples:
            return refuse(request_id, "EXAMPLE_NOT_PERMITTED",
                          "That example is not retained for this capability.",
                          detail=selection)
        if examples and selection is None and not editable:
            return refuse(request_id, "INPUT_INADMISSIBLE",
                          "This capability runs a retained example; none was selected.")

        schema = pilot["inputSchema"]
        for pointer in sorted(editable):
            subschema = resolve_schema_pointer(schema, pointer)
            if subschema is None:
                return refuse(request_id, "POINTER_NOT_DECLARED",
                              "That field does not resolve in the pinned contract.",
                              pointer=pointer)
            entry = ownership[pointer]
            required = bool(entry.get("required"))
            message = admissible(supplied.get(pointer), subschema, pointer, required)
            if message:
                return refuse(request_id, "INPUT_INADMISSIBLE", message, pointer=pointer)

        # A repeat of a known requestId reconciles; it never executes again.
        if request_id in self.runs:
            existing = self.runs[request_id]
            return dict(existing, deduplicated=True, acceptedAt=now_utc())

        canonical = self.assemble(pilot, supplied, selection)
        run_id = str(uuid.uuid4())
        acceptance = {
            "acceptanceVersion": "run-acceptance.v1",
            "requestId": request_id,
            "disposition": "ADMITTED",
            "runId": run_id,
            "deduplicated": False,
            "selection": {
                "subject": profile["subject"],
                "namespace": profile.get("namespace"),
                "scenarioId": (pilot.get("authority") or {}).get("scenarioId"),
                "publicationId": self.publication["publicationId"],
                "authorityPins": {
                    k: v for k, v in ((pilot.get("authority") or {}).get("identity") or {}).items()
                },
            },
            "acceptedAt": now_utc(),
        }
        # Persist the association before any external effect would begin.
        self.runs[request_id] = acceptance
        self._canonical = canonical
        return acceptance

    def assemble(self, pilot: dict, supplied: dict, selection: str | None) -> dict:
        """Construct canonical input the way the server does: from declarations.

        Fixed values come from the ownership declaration, derived objects are
        assembled from their declared children, and system-bound values come from
        the selected retained fixture. None of them come from the browser.
        """
        profile = pilot["profile"]
        ownership = profile["inputs"]
        canonical: dict = {}

        example = None
        if selection is not None:
            example = next((e for e in pilot.get("examples", []) if e["id"] == selection), None)

        for entry in ownership:
            pointer, kind = entry["pointer"], entry["ownership"]
            if kind == "fixed":
                set_pointer(canonical, pointer, entry["value"])
            elif kind == "editable":
                if pointer in supplied:
                    set_pointer(canonical, pointer, supplied[pointer])
            elif kind == "derived":
                # An object assembled from its declared children; the children
                # write themselves in above and below this entry.
                if pointer.strip("/") not in canonical:
                    set_pointer(canonical, pointer, {})
            elif kind == "system-bound":
                if example is not None:
                    value = example.get("input", {})
                    for token in pointer.lstrip("/").split("/"):
                        value = value.get(token) if isinstance(value, dict) else None
                    if value is not None:
                        set_pointer(canonical, pointer, value)
        return canonical


def self_check() -> int:
    """Exercise the refusals directly, because a boundary nobody attacks is a claim."""
    binding = load(WORKBENCH / "dependencies" / "lab-publication.binding.json")
    publication = load(Path(binding["publication"]["default"]))
    adapter = InvocationAdapter(publication)
    pid = publication["publicationId"]

    def command(**overrides):
        base = {"commandVersion": "workbench-command.v1",
                "requestId": overrides.pop("requestId", str(uuid.uuid4())),
                "publicationId": pid,
                "subject": "resolve-equity-market-price-evidence",
                "editableValues": {"/payload/symbol": "AAPL", "/payload/region": "US"}}
        base.update(overrides)
        return base

    cases = [
        ("valid live-finance command is admitted", command(), "ADMITTED", None),
        ("unpublished subject", command(subject="drop-all-tables"),
         "REFUSED", "SUBJECT_NOT_PUBLISHED"),
        ("stale publication", command(publicationId="sha256:" + "0" * 64),
         "REFUSED", "PUBLICATION_STALE"),
        ("fixed-field forgery", command(editableValues={
            "/payload/symbol": "AAPL", "/payload/region": "US",
            "/contractId": "something-else.v1"}),
         "REFUSED", "POINTER_NOT_EDITABLE"),
        ("undeclared pointer", command(editableValues={
            "/payload/symbol": "AAPL", "/payload/region": "US",
            "/payload/injected": True}),
         "REFUSED", "POINTER_NOT_DECLARED"),
        ("pattern violation", command(editableValues={
            "/payload/symbol": "aapl; drop", "/payload/region": "US"}),
         "REFUSED", "INPUT_INADMISSIBLE"),
        ("enum violation", command(editableValues={
            "/payload/symbol": "AAPL", "/payload/region": "GB"}),
         "REFUSED", "INPUT_INADMISSIBLE"),
        ("missing required field", command(editableValues={"/payload/region": "US"}),
         "REFUSED", "INPUT_INADMISSIBLE"),
        ("too-long symbol", command(editableValues={
            "/payload/symbol": "A" * 25, "/payload/region": "US"}),
         "REFUSED", "INPUT_INADMISSIBLE"),
        ("fixture inventory forgery on a fixture capability",
         command(subject="resolve-sidefx-eligible-providers",
                 editableValues={"/providerBindings": [{"forged": True}]},
                 exampleSelection="hold-admitted-providers-that-declare-no-target"),
         "REFUSED", "POINTER_NOT_EDITABLE"),
        ("impermissible example",
         command(subject="resolve-sidefx-eligible-providers", editableValues={},
                 exampleSelection="not-a-retained-example"),
         "REFUSED", "EXAMPLE_NOT_PERMITTED"),
        ("no example chosen for a fixture capability",
         command(subject="resolve-sidefx-eligible-providers", editableValues={}),
         "REFUSED", "INPUT_INADMISSIBLE"),
        ("hello world needs no editable value",
         command(subject="say-hello-world", editableValues={}), "ADMITTED", None),
        ("greeting requires a name",
         command(subject="greet-by-name", editableValues={}),
         "REFUSED", "INPUT_INADMISSIBLE"),
        ("greeting accepts a name",
         command(subject="greet-by-name", editableValues={"/payload/name": "Ada"}),
         "ADMITTED", None),
    ]

    import jsonschema
    command_schema = load(CONTRACT)
    acceptance_schema = load(ACCEPTANCE)

    results, failures = [], 0
    for label, payload, expected_disposition, expected_code in cases:
        schema_valid, schema_detail = True, None
        try:
            jsonschema.validate(payload, command_schema)
        except jsonschema.ValidationError as error:
            schema_valid, schema_detail = False, error.message

        acceptance = adapter.admit(payload)
        try:
            jsonschema.validate(acceptance, acceptance_schema)
            acceptance_valid = True
        except jsonschema.ValidationError as error:
            acceptance_valid, schema_detail = False, error.message

        code = (acceptance.get("refusal") or {}).get("code")
        passed = (acceptance["disposition"] == expected_disposition
                  and code == expected_code and acceptance_valid)
        if not passed:
            failures += 1
        results.append({"case": label, "expected": expected_disposition,
                        "expectedCode": expected_code,
                        "observed": acceptance["disposition"], "observedCode": code,
                        "commandSchemaValid": schema_valid,
                        "acceptanceSchemaValid": acceptance_valid,
                        "passed": passed, "detail": schema_detail})
        print("  %s %-52s %s%s" % ("ok  " if passed else "FAIL", label,
                                   acceptance["disposition"],
                                   " / " + code if code else ""))

    # A shape the contract has no room for cannot even be expressed.
    forged = {"commandVersion": "workbench-command.v1", "requestId": "r" * 12,
              "publicationId": pid, "subject": "say-hello-world",
              "namespace": "sidefx:internal", "endpoint": "http://elsewhere/commands"}
    try:
        jsonschema.validate(forged, command_schema)
        rejected = False
    except jsonschema.ValidationError:
        rejected = True
    if not rejected:
        failures += 1
    results.append({"case": "namespace and endpoint have no place in the command shape",
                    "passed": rejected, "expected": "schema rejection"})
    print("  %s namespace and endpoint have no place in the command shape"
          % ("ok  " if rejected else "FAIL"))

    # Deduplication: the same requestId must reconcile, never execute twice.
    repeated = command(requestId="repeat-request-0001")
    first = adapter.admit(repeated)
    second = adapter.admit(dict(repeated))
    dedup = (first.get("runId") == second.get("runId")
             and second.get("deduplicated") is True
             and first.get("deduplicated") is False)
    if not dedup:
        failures += 1
    results.append({"case": "a repeated requestId reconciles to the same run",
                    "passed": dedup, "runId": first.get("runId")})
    print("  %s a repeated requestId reconciles to the same run"
          % ("ok  " if dedup else "FAIL"))

    receipt = {
        "receiptType": "invocation-boundary-receipt.v1",
        "checkedAt": now_utc(),
        "statement": "The workbench command boundary is exercised against forgery, stale "
                     "publications, undeclared and non-editable pointers, impermissible "
                     "examples, contract violations and duplicate submission.",
        "publicationId": pid,
        "summary": {"cases": len(results),
                    "passed": len(results) - failures, "failed": failures},
        "cases": results,
        "notEstablished": [
            "Nothing was executed. This admits and assembles; it does not contact the service.",
            "The deduplication store is in memory. Durable request-to-run association is "
            "required before retries or resumable execution may be offered.",
            "Server-side authentication, authorization and validation remain independent and "
            "authoritative; nothing here substitutes for them.",
        ],
    }
    receipt_dir = WORKBENCH / "evidence" / "invoke"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "invocation-boundary.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print("boundary       %d/%d cases passed" % (len(results) - failures, len(results)))
    print("receipt        %s" % receipt_path.as_posix())
    return 0 if failures == 0 else 3


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--command", type=Path, default=None)
    parser.add_argument("--emit-canonical", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check or not args.command:
        return self_check()

    binding = load(WORKBENCH / "dependencies" / "lab-publication.binding.json")
    publication = load(Path(binding["publication"]["default"]))
    adapter = InvocationAdapter(publication)

    import jsonschema
    command = load(args.command)
    try:
        jsonschema.validate(command, load(CONTRACT))
    except jsonschema.ValidationError as error:
        print("COMMAND_MALFORMED: %s" % error.message, file=sys.stderr)
        return 14

    acceptance = adapter.admit(command)
    print(json.dumps(acceptance, indent=2))
    if args.emit_canonical and acceptance["disposition"] == "ADMITTED":
        print("\ncanonical input the server would construct:")
        print(json.dumps(adapter._canonical, indent=2))
    return 0 if acceptance["disposition"] == "ADMITTED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
