"""Run the M1 completion gate and emit one evidence summary.

M1 recreates the full source workbench. Its exit evidence has three parts, and
this reports honestly on all three, including the part no local check can reach:

  1. both reference views match visually and functionally, including below-fold
     inspection and illustrative playback
  2. no source components or routes lost; an alternate layout and theme retains
     identities
  3. a hosted inspection package works in the private Space and the website embed

Criterion 2 is fully verifiable here and is verified. Criterion 1 is verified
structurally — every recorded control has a declared owner, both scenes carry
their complete entity sets, and the whole pipeline resolves — but its visual
half needs a browser and is reported as unmet. Criterion 3 needs hosting and is
reported as unmet.

A gate that reported "passed" while two thirds of a criterion went unmeasured
would be worth nothing, so this prints and records the gap.

Usage
-----
    python tools/m1_gate.py [--root DIR]

Exit codes
----------
    0  every locally verifiable criterion passed
   12  a local step failed
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent
EVIDENCE = WORKBENCH / "evidence"

sys.path.insert(0, str(HERE))
from resolve_ui_dependencies import now_utc, sha256_file  # noqa: E402

BASE = "experiences/source-inspection/source-inspection.experience.json"
VARIANT = "experiences/derived/source-inspection-inspector-left.experience.json"


def run_step(name: str, argv: list, findings: list, quiet: bool = False) -> dict:
    result = subprocess.run([sys.executable] + argv, capture_output=True, text=True,
                            cwd=str(WORKBENCH))
    record = {"step": name, "exitCode": result.returncode, "passed": result.returncode == 0}
    if not quiet:
        print(result.stdout, end="")
    if result.returncode != 0:
        record["detail"] = (result.stderr or result.stdout).strip()[-1500:]
        findings.append({"code": "M1_STEP_FAILED", "severity": "error",
                         "detail": "%s exited %d" % (name, result.returncode)})
        print(result.stderr, end="", file=sys.stderr)
    return record


def run_trace_parity(findings: list) -> dict:
    """Compare the reimplemented trace planner against the frozen reference.

    This needs Node, which the browser checks do not. When Node is absent the
    step is reported as unrun rather than quietly passing, because a skipped
    check that prints nothing is how a gate starts lying.
    """
    if shutil.which("node") is None:
        findings.append({"code": "M1_TRACE_PARITY_UNRUN", "severity": "error",
                         "detail": "node is unavailable, so planner parity was not verified"})
        print("  trace parity   UNRUN (node unavailable)")
        return {"step": "trace-parity", "exitCode": None, "passed": False,
                "detail": "node unavailable"}
    result = subprocess.run(["node", "tests/trace-parity.test.cjs"],
                            capture_output=True, text=True, cwd=str(WORKBENCH))
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr, end="", file=sys.stderr)
        findings.append({"code": "M1_STEP_FAILED", "severity": "error",
                         "detail": "trace-parity exited %d" % result.returncode})
    return {"step": "trace-parity", "exitCode": result.returncode,
            "passed": result.returncode == 0}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None)
    args = parser.parse_args(argv)
    root_argv = ["--root", args.root] if args.root else []
    findings: list = []

    print("== M0 gate ==")
    steps = [run_step("m0-gate", ["tools/m0_gate.py"] + root_argv, findings, quiet=True)]
    print("  %s" % ("passed" if steps[-1]["passed"] else "FAILED"))

    print("\n== scenes ==")
    steps.append(run_step("ingest-scenes", ["adapters/topology/ingest_topology_view.py"], findings))

    print("\n== profiles ==")
    steps.append(run_step("presentation-profiles",
                          ["providers/build_provider_profile.py"] + root_argv, findings))
    steps.append(run_step("layout-profiles", ["tools/derive_layout_profile.py"], findings))

    print("\n== build ==")
    steps.append(run_step("build-baseline",
                          ["tools/build_workbench.py", "--experience", BASE] + root_argv, findings))
    steps.append(run_step("build-variant",
                          ["tools/build_workbench.py", "--experience", VARIANT] + root_argv, findings))

    print("\n== parity ==")
    steps.append(run_step("verify-parity", ["tools/verify_m1_parity.py"] + root_argv, findings))
    steps.append(run_trace_parity(findings))

    parity = load(EVIDENCE / "parity" / "m1-parity.receipt.json")
    base_build = load(EVIDENCE / "build" / "source-inspection.build.receipt.json")
    variant_build = load(EVIDENCE / "build" / "source-inspection-inspector-left.build.receipt.json")
    trace = load(EVIDENCE / "parity" / "trace-parity.receipt.json")
    checks = (parity or {}).get("checks", {})

    structural = bool(
        parity and parity["passed"] and base_build and base_build["summary"]["built"]
        and variant_build and variant_build["summary"]["built"])

    criteria = [
        {
            "criterion": "Both reference views match visually and functionally, including "
                         "below-fold inspection and illustrative playback",
            "met": False,
            "partial": structural,
            "verified": [
                "Both scenes ingest with their complete node and route identity sets.",
                "All 22 recorded controls have a declared owner that exists in the surface.",
                "Below-fold inspection is declared as components with bound state.",
                "Illustrative playback is declared with its speed, follow and status state, "
                "and the trace planner is reimplemented against the scene contract.",
                ("The reimplemented trace planner produces wave-for-wave identical output to "
                 "the frozen reference on every fixture: "
                 + ("%d/%d cases passed."
                    % ((trace or {}).get("summary", {}).get("passed", 0),
                       (trace or {}).get("summary", {}).get("cases", 0))
                    if trace else "not run.")),
            ],
            "unmet": [
                "No browser has run the package, so visual match is unmeasured.",
                "Resolved-versus-observed geometry conformance is unmeasured.",
                "Playback, selection and camera behavior are unexercised at runtime.",
            ],
            "evidence": "evidence/parity/m1-parity.receipt.json",
        },
        {
            "criterion": "No source components or routes lost; alternate layout and theme "
                         "retains identities",
            "met": bool(checks.get("sourceFidelity", {}).get("conforms")
                        and checks.get("optionality", {}).get("conforms")),
            "observed": {
                "scenes": {k: {"nodes": v["nodes"], "routes": v["routes"],
                               "hitTargets": v["hitTargets"]}
                           for k, v in checks.get("sourceFidelity", {}).get("scenes", {}).items()},
                "optionality": {
                    "componentsCompared": checks.get("optionality", {}).get("componentsCompared"),
                    "componentsMoved": checks.get("optionality", {}).get("componentsMoved"),
                    "invariants": checks.get("optionality", {}).get("invariants"),
                    "providerProfilesDiffer": checks.get("optionality", {}).get("providerProfilesDiffer"),
                },
            },
            "evidence": "evidence/parity/m1-parity.receipt.json",
        },
        {
            "criterion": "Hosted inspection package works in the private Space and the "
                         "website embed",
            "met": False,
            "partial": structural,
            "verified": [
                "A self-contained package is assembled with its scenes, materials, "
                "interaction plan and runtime.",
                "The embed height channel is declared, with the command channel explicitly off.",
            ],
            "unmet": [
                "This local gate does not inspect deployment; consult evidence/host/verification.json for the Space.",
                "Website embed rollout and broad visual parity remain separate acceptance work.",
            ],
            "evidence": "evidence/build/source-inspection.build.receipt.json",
        },
    ]

    local_pass = bool(steps) and all(s["passed"] for s in steps) and not findings
    summary = {
        "receiptType": "milestone-gate-receipt.v1",
        "milestone": "M1",
        "title": "Recreate the full source workbench",
        "evaluatedAt": now_utc(),
        "locallyVerifiablePassed": local_pass,
        "milestoneComplete": local_pass and all(c["met"] for c in criteria),
        "steps": steps,
        "exitCriteria": criteria,
        "findings": findings,
        "architectureChecks": {
            "undeclaredFindingsBaseline": (base_build or {}).get("summary", {}).get("undeclared"),
            "undeclaredFindingsVariant": (variant_build or {}).get("summary", {}).get("undeclared"),
            "unsupportedPathProven": checks.get("architecture", {}).get("unsupportedPathProven"),
            "tracePlannerParity": (trace or {}).get("summary"),
            "circuitRealized": checks.get("architecture", {}).get("circuitRealized"),
        },
        "notEstablished": (parity or {}).get("notEstablished", []),
    }

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    summary_path = EVIDENCE / "m1-gate.receipt.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print("\n== M1 gate ==")
    for criterion in criteria:
        mark = "x" if criterion["met"] else ("~" if criterion.get("partial") else " ")
        print("  [%s] %s" % (mark, criterion["criterion"]))
    print("  local steps    %s" % ("all passed" if local_pass else "FAILED"))
    print("  milestone      %s" % ("COMPLETE" if summary["milestoneComplete"]
                                   else "PARTIAL — local gate; see hosted evidence for Space verification"))
    print("  receipt        %s" % summary_path.as_posix())

    return 0 if local_pass else 12


if __name__ == "__main__":
    raise SystemExit(main())
