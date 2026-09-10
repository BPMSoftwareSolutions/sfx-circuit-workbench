"""Bind an actual returned outcome to its declared presentation plan.

The service validates the outcome against the permitted contract and retains its
run, occurrence and authority identity. This binds that validated result to the
read-only components the UX compiler emitted, so the browser receives resolved
values and supported component instructions rather than a second contract
interpreter.

Three rules do the real work:

  * a variant is matched by **validated contract identity and discriminator**,
    never guessed from which fields happen to be present
  * a shape that matches no declared variant produces a visible contract finding
    and authorised diagnostic detail — never a plausible-looking success card
  * **missing, null and zero stay distinct.** A pointer that is absent, a pointer
    whose value is null, and a pointer whose value is 0 are three different
    facts, and a presentation that flattened them would be lying about the data

Delivery status is separate from capability output throughout. A refusal or an
unknown execution state uses the workbench's delivery presentation; it is never
dressed up as something the capability returned.

Usage
-----
    python adapters/invocation/present_outcome.py --self-check

Exit codes
----------
    0  the self-check passed
   15  a presentation check failed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent.parent
COMPILED = WORKBENCH / "experiences" / "invoke" / "compiled"

sys.path.insert(0, str(WORKBENCH / "tools"))
from resolve_ui_dependencies import now_utc  # noqa: E402

MISSING = object()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_pointer(document, pointer: str):
    """Return the value at a pointer, or MISSING. Null is a value, not an absence."""
    if pointer in ("", "/"):
        return document
    node = document
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            try:
                node = node[int(token)]
                continue
            except (ValueError, IndexError):
                return MISSING
        if not isinstance(node, dict) or token not in node:
            return MISSING
        node = node[token]
    return node


def describe(value) -> dict:
    """Say what a value *is*, so the renderer never has to infer it."""
    if value is MISSING:
        return {"presence": "missing", "value": None}
    if value is None:
        return {"presence": "null", "value": None}
    return {"presence": "present", "value": value}


def match_variant(plan: dict, outcome: dict, findings: list) -> dict | None:
    """Identity first, discriminator second. Never field-sniffing."""
    contract_id = outcome.get("contractId")
    candidates = [o for o in plan["outcomes"] if o["match"]["contractId"] == contract_id]
    if not candidates:
        findings.append({
            "code": "OUTCOME_CONTRACT_UNDECLARED", "severity": "error",
            "detail": "returned contractId %r matches no declared variant" % contract_id})
        return None

    discriminated = []
    for candidate in candidates:
        pointer = candidate["match"].get("discriminatorPointer")
        if not pointer:
            discriminated.append(candidate)
            continue
        value = read_pointer(outcome, pointer)
        if value is not MISSING and value in (candidate["match"].get("discriminatorValues") or []):
            discriminated.append(candidate)

    if not discriminated:
        findings.append({
            "code": "OUTCOME_VARIANT_UNMATCHED", "severity": "error",
            "detail": "contract %r matched, but no declared discriminator value did"
                      % contract_id})
        return None
    if len(discriminated) > 1:
        findings.append({
            "code": "OUTCOME_VARIANT_AMBIGUOUS", "severity": "error",
            "detail": "%d variants matched: %s"
                      % (len(discriminated), ", ".join(v["outcomeId"] for v in discriminated))})
        return None
    return discriminated[0]


def bind_element(element: dict, outcome: dict, findings: list) -> dict:
    """Resolve one declared element's pointers against the actual outcome."""
    family = element["family"]
    bound = {"elementId": element["elementId"], "family": family,
             "label": element.get("label")}

    if family in ("collection", "findings"):
        items = read_pointer(outcome, element["itemPath"])
        if items is MISSING:
            bound["items"] = []
            bound["presence"] = "missing"
        elif not isinstance(items, list):
            findings.append({"code": "OUTCOME_COLLECTION_NOT_A_LIST", "severity": "error",
                             "detail": "%s at %s" % (element["elementId"], element["itemPath"])})
            bound["items"] = []
            bound["presence"] = "invalid"
        else:
            bound["items"] = items
            bound["presence"] = "present"
        bound["count"] = len(bound["items"])
        bound["empty"] = bound["count"] == 0
        bound["emptyText"] = element.get("emptyText")
        return bound

    if family == "record":
        bound["fields"] = [
            dict(describe(read_pointer(outcome, pointer)), pointer=pointer)
            for pointer in (element.get("fields") or [])]
        return bound

    pointers = element.get("pointers") or {}
    resolved = {name: describe(read_pointer(outcome, pointer))
                for name, pointer in pointers.items()}
    bound["values"] = {name: dict(entry, pointer=pointers[name])
                       for name, entry in resolved.items()}

    if family == "disposition":
        actual = resolved.get("value", {}).get("value")
        labels = element.get("dispositionLabels") or {}
        if actual is not None and actual not in labels:
            # A disposition the declaration never anticipated is shown as itself,
            # with a finding. It is not relabelled into something reassuring.
            findings.append({"code": "OUTCOME_DISPOSITION_UNLABELLED", "severity": "warning",
                             "detail": "no declared label for disposition %r" % actual})
        bound["label"] = labels.get(actual, actual)
        bound["disposition"] = actual

    if family == "metric":
        amount = resolved.get("amount", {})
        bound["precision"] = element.get("precision")
        # Zero is a number. Missing is not.
        bound["hasValue"] = amount.get("presence") == "present"

    return bound


def present(plan: dict, outcome: dict) -> dict:
    findings: list = []
    variant = match_variant(plan, outcome, findings)
    if variant is None:
        return {
            "presentationVersion": "outcome-presentation.v1",
            "uxId": plan["uxId"], "subject": plan["subject"],
            "state": "OUTCOME_UNPRESENTABLE",
            "dialogTitle": "Outcome could not be presented",
            "diagnostic": {"returnedContractId": outcome.get("contractId"),
                           "declaredVariants": [o["match"] for o in plan["outcomes"]]},
            "elements": [], "findings": findings,
            "note": "A returned shape that matches no declared variant is reported as a "
                    "contract finding. No success presentation is fabricated.",
        }

    return {
        "presentationVersion": "outcome-presentation.v1",
        "uxId": plan["uxId"], "subject": plan["subject"],
        "state": "PRESENTED",
        "outcomeId": variant["outcomeId"],
        "dialogTitle": variant["dialogTitle"],
        "isNegative": variant["isNegative"],
        "anchor": variant["anchor"],
        "elements": [bind_element(e, outcome, findings) for e in variant["elements"]],
        "findings": findings,
    }


def delivery_view(plan: dict, acceptance: dict) -> dict:
    """A refusal or an uncertain delivery is a workbench state, not capability output."""
    disposition = acceptance["disposition"]
    delivery = plan.get("delivery", {})
    if disposition == "REFUSED":
        refusal = acceptance.get("refusal", {})
        return {"presentationVersion": "outcome-presentation.v1", "state": "REFUSED",
                "dialogTitle": delivery.get("refusedTitle", "Request refused"),
                "refusal": refusal, "draftPreserved": True,
                "note": "The draft is preserved and nothing was invoked."}
    if disposition == "UNCERTAIN":
        return {"presentationVersion": "outcome-presentation.v1", "state": "UNCERTAIN",
                "dialogTitle": delivery.get("uncertainTitle", "Delivery uncertain"),
                "guidance": delivery.get("uncertainGuidance"),
                "recoverable": True, "automaticRetry": False,
                "note": "The request is retained for reconciliation. It is not resubmitted."}
    return {"presentationVersion": "outcome-presentation.v1", "state": "ADMITTED",
            "runId": acceptance.get("runId")}


def self_check() -> int:
    results, failures = [], 0

    def check(label, condition, detail=None):
        nonlocal failures
        if not condition:
            failures += 1
        results.append({"case": label, "passed": bool(condition), "detail": detail})
        print("  %s %s" % ("ok  " if condition else "FAIL", label))

    # The retained live-finance result is real returned data, not a mock.
    retained = load(Path(r"C:\lab\repos\sfx-embody\evidence\hugging-face-live-finance"
                         r"\cli-result.json"))
    actual_outcome = retained["result"]["outcome"]
    finance = load(COMPILED / "live-finance.dialog-plan.json")

    presented = present(finance, actual_outcome)
    check("the retained live-finance outcome matches its declared variant",
          presented["state"] == "PRESENTED" and presented["outcomeId"] == "price")

    by_id = {e["elementId"]: e for e in presented["elements"]}
    price = by_id["price"]["values"]["amount"]
    check("observed price binds to the actual returned value",
          price["presence"] == "present" and price["value"] == 315.34, price)
    check("currency is bound from its own declared pointer",
          by_id["price"]["values"]["currency"]["value"] == "USD")
    check("market time is bound separately from retrieval",
          by_id["price"]["values"]["timestamp"]["value"] == 1788984001)
    check("domain disposition carries its declared label",
          by_id["disposition"]["label"] == "Canonical price evidence resolved")
    check("provider testimony is presented as evidence, not as the result",
          by_id["provider"]["values"]["providerId"]["value"]
          == "rapidapi/davethebeast/yahoo-finance166")
    check("quote record preserves each field with its own presence",
          all(f["presence"] == "present" for f in by_id["quote"]["fields"]))

    # Missing, null and zero must stay three different facts.
    probe = {"contractId": "equity-market-price-evidence.v1",
             "disposition": "EQUITY_MARKET_PRICE_EVIDENCE_RESOLVED",
             "payload": {"symbol": "ZERO", "region": "US", "observedPrice": 0,
                         "currency": None, "observedMarketTime": 1},
             "providerTestimony": {}}
    probed = {e["elementId"]: e for e in present(finance, probe)["elements"]}
    check("zero is a present value, not an absence",
          probed["price"]["values"]["amount"]["presence"] == "present"
          and probed["price"]["values"]["amount"]["value"] == 0
          and probed["price"]["hasValue"] is True)
    check("null is distinct from missing",
          probed["price"]["values"]["currency"]["presence"] == "null")
    check("an absent pointer reports as missing",
          probed["quote"]["fields"][2]["presence"] == "missing",
          probed["quote"]["fields"][2])

    # A negative domain outcome has its own declared layout.
    providers = load(COMPILED / "provider-resolution.dialog-plan.json")
    negative = present(providers, {
        "contractId": "sidefx-semantic-provider-resolution.v1",
        "disposition": "NOT_OBSERVABLE", "consideredCount": 1, "eligibleCount": 0, "providers": [], "findings": [
            {"code": "NO_TARGET_DECLARED", "identity": "sda-json-authority-ingestion-port.v1"}],
        "snapshotDigest": "sha256:x", "resolutionDigest": "sha256:y"})
    check("a negative domain outcome selects its own declared variant",
          negative["state"] == "PRESENTED"
          and negative["outcomeId"] == "not-observable" and negative["isNegative"] is True)
    negative_by_id = {e['elementId']: e for e in negative['elements']}
    check("negative outcomes preserve counts without changing their domain disposition",
          negative['isNegative'] is True
          and negative_by_id['eligible']['values']['amount']['value'] == 0
          and negative_by_id['disposition']['values']['value']['value'] == 'NOT_OBSERVABLE')

    positive = present(providers, {
        "contractId": "sidefx-semantic-provider-resolution.v1",
        "disposition": "PROVIDERS_RESOLVED", "consideredCount": 4, "eligibleCount": 0,
        "providers": [], "findings": [], "snapshotDigest": "sha256:x",
        "resolutionDigest": "sha256:y"})
    positive_by_id = {e["elementId"]: e for e in positive["elements"]}
    check("the positive variant is selected by its own discriminator",
          positive["outcomeId"] == "resolved")
    check("an eligible count of zero is presented, not hidden",
          positive_by_id["eligible"]["values"]["amount"]["value"] == 0
          and positive_by_id["eligible"]["hasValue"] is True)
    check("an empty collection reports its declared empty text",
          positive_by_id["providers"]["empty"] is True
          and positive_by_id["providers"]["emptyText"]
          == "No providers were reported in this fixture.")

    # A shape matching no declared variant must never become a success card.
    unpresentable = present(finance, {"contractId": "something-else.v1", "payload": {}})
    check("an undeclared contract is refused, not rendered",
          unpresentable["state"] == "OUTCOME_UNPRESENTABLE"
          and unpresentable["elements"] == []
          and any(f["code"] == "OUTCOME_CONTRACT_UNDECLARED"
                  for f in unpresentable["findings"]))

    unmatched = present(finance, {
        "contractId": "equity-market-price-evidence.v1",
        "disposition": "SOMETHING_UNDECLARED", "payload": {}})
    check("a right contract with an undeclared disposition is refused, not rendered",
          unmatched["state"] == "OUTCOME_UNPRESENTABLE"
          and any(f["code"] == "OUTCOME_VARIANT_UNMATCHED" for f in unmatched["findings"]))

    # Delivery states stay separate from capability output.
    refused = delivery_view(finance, {
        "disposition": "REFUSED",
        "refusal": {"code": "PROVIDER_UNAVAILABLE", "message": "The provider is unavailable."}})
    check("a refusal is a delivery state and preserves the draft",
          refused["state"] == "REFUSED" and refused["draftPreserved"] is True
          and "elements" not in refused)
    uncertain = delivery_view(finance, {"disposition": "UNCERTAIN"})
    check("an uncertain delivery is recoverable and never auto-resubmitted",
          uncertain["state"] == "UNCERTAIN" and uncertain["automaticRetry"] is False)

    receipt = {
        "receiptType": "outcome-presentation-receipt.v1",
        "checkedAt": now_utc(),
        "statement": "Declared outcome plans are bound to actual returned data, including the "
                     "retained live-finance result, and checked for variant matching, negative "
                     "outcomes, unpresentable shapes, delivery states and the distinctness of "
                     "missing, null and zero.",
        "retainedEvidence": {
            "path": "sfx-embody/evidence/hugging-face-live-finance/cli-result.json",
            "executionId": retained["result"]["executionId"],
            "note": "One retained execution. Read as data; nothing was re-executed.",
        },
        "summary": {"cases": len(results), "passed": len(results) - failures,
                    "failed": failures},
        "cases": results,
        "notEstablished": [
            "No capability was invoked. The outcome used here is retained evidence from an "
            "earlier run, read as data.",
            "A live price must be compared to its own retained provider testimony; a previous "
            "run's price is not an expected constant.",
        ],
    }
    receipt_dir = WORKBENCH / "evidence" / "invoke"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "outcome-presentation.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print("presentation   %d/%d cases passed" % (len(results) - failures, len(results)))
    print("receipt        %s" % receipt_path.as_posix())
    return 0 if failures == 0 else 15


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-check", action="store_true")
    parser.parse_args(argv)
    return self_check()


if __name__ == "__main__":
    raise SystemExit(main())
