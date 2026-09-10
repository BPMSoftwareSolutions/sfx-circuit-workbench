"""Run the M0 completion gate and emit one evidence summary.

M0 exits on three things: clean dependency resolution, the existing specimen
receipts reproduced against the packaged closure, and a baseline manifest that
identifies exactly what is being recreated. This runs all of them in order,
validates both declarations against their contracts first, and refuses the gate
if any step fails.

Usage
-----
    python tools/m0_gate.py [--root DIR]

Exit codes
----------
    0  M0 gate passed
    6  at least one gate step failed
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent
CONTRACTS = WORKBENCH / "contracts"
EVIDENCE = WORKBENCH / "evidence"

sys.path.insert(0, str(HERE))
from resolve_ui_dependencies import now_utc, sha256_file  # noqa: E402


def validate_declaration(document: Path, schema: Path, findings: list) -> dict:
    """Validate one authored declaration against its contract."""
    record = {"declaration": document.relative_to(WORKBENCH).as_posix(),
              "schema": schema.name, "valid": False}
    try:
        import jsonschema
    except ImportError:
        record["detail"] = "jsonschema unavailable; declaration not validated"
        findings.append({"code": "CONTRACT_VALIDATION_UNAVAILABLE", "severity": "error",
                         "detail": record["detail"]})
        return record

    try:
        jsonschema.validate(
            json.loads(document.read_text(encoding="utf-8")),
            json.loads(schema.read_text(encoding="utf-8")),
        )
    except jsonschema.ValidationError as error:
        record["detail"] = "%s at /%s" % (error.message,
                                          "/".join(str(p) for p in error.absolute_path))
        findings.append({"code": "DECLARATION_INVALID", "severity": "error",
                         "detail": "%s: %s" % (document.name, record["detail"])})
        return record

    record["valid"] = True
    record["sha256"] = sha256_file(document)
    return record


def run_step(name: str, argv: list, findings: list) -> dict:
    result = subprocess.run([sys.executable] + argv, capture_output=True, text=True,
                            cwd=str(WORKBENCH))
    record = {"step": name, "exitCode": result.returncode, "passed": result.returncode == 0}
    if result.returncode != 0:
        record["detail"] = (result.stderr or result.stdout).strip()[-2000:]
        findings.append({"code": "M0_STEP_FAILED", "severity": "error",
                         "detail": "%s exited %d" % (name, result.returncode)})
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, help="sidefx-ui root override.")
    args = parser.parse_args(argv)

    root_argv = ["--root", args.root] if args.root else []
    findings: list = []

    print("== contracts ==")
    declarations = [
        validate_declaration(WORKBENCH / "dependencies" / "ui-dependencies.manifest.json",
                             CONTRACTS / "ui-dependency-manifest.v1.schema.json", findings),
        validate_declaration(WORKBENCH / "fixtures" / "baseline" / "baseline-sources.json",
                             CONTRACTS / "workbench-baseline-sources.v1.schema.json", findings),
    ]
    for record in declarations:
        print("  %-46s %s" % (record["declaration"],
                              "valid" if record["valid"] else "INVALID: " + record.get("detail", "")))

    steps = []
    if all(r["valid"] for r in declarations):
        print("\n== dependency resolution ==")
        steps.append(run_step("resolve-ui-dependencies",
                              ["tools/resolve_ui_dependencies.py"] + root_argv, findings))
        print("\n== specimen reproduction ==")
        steps.append(run_step("reproduce-specimen-receipts",
                              ["tools/reproduce_specimen_receipts.py"] + root_argv, findings))
        print("\n== baseline verification ==")
        steps.append(run_step("verify-baseline",
                              ["tools/freeze_baseline.py", "--verify"], findings))
    else:
        findings.append({"code": "M0_GATE_NOT_ATTEMPTED", "severity": "error",
                         "detail": "declarations did not validate; no step was run"})

    passed = bool(steps) and all(s["passed"] for s in steps) and not findings

    def load(path: Path):
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    resolution = load(EVIDENCE / "dependencies" / "ui-dependency-resolution.receipt.json")
    reproduction = load(EVIDENCE / "dependencies" / "specimen-reproduction.receipt.json")
    baseline = load(WORKBENCH / "fixtures" / "baseline" / "baseline-manifest.json")

    summary = {
        "receiptType": "milestone-gate-receipt.v1",
        "milestone": "M0",
        "title": "Freeze the reference and make composition portable",
        "evaluatedAt": now_utc(),
        "passed": passed,
        "declarations": declarations,
        "steps": steps,
        "exitCriteria": [
            {
                "criterion": "Clean dependency resolution",
                "met": bool(resolution and resolution["summary"]["clean"]),
                "evidence": "evidence/dependencies/ui-dependency-resolution.receipt.json",
                "observed": resolution["summary"] if resolution else None,
            },
            {
                "criterion": "Existing specimen receipts reproduced against the packaged closure",
                "met": bool(reproduction and reproduction["summary"]["divergent"] == 0
                            and reproduction["summary"]["specimens"] > 0),
                "evidence": "evidence/dependencies/specimen-reproduction.receipt.json",
                "observed": reproduction["summary"] if reproduction else None,
                "qualification": (reproduction or {}).get("criterion"),
            },
            {
                "criterion": "A baseline manifest identifies exactly what is being recreated",
                "met": bool(baseline and baseline["summary"]["frozen"]),
                "evidence": "fixtures/baseline/baseline-manifest.json",
                "observed": baseline["summary"] if baseline else None,
            },
        ],
        "findings": findings,
        "notEstablished": [
            "No browser was run, no geometry was measured and no interaction was captured.",
            "The captured zoom states are derived from source reading and remain marked requiresMeasurement.",
            "No capability was invoked; these fixtures are declared topology and carry no execution evidence.",
            "Reproduction is semantic equivalence with enumerated relocation deltas, not byte identity.",
        ],
    }

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    summary_path = EVIDENCE / "m0-gate.receipt.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print("\n== M0 gate ==")
    for criterion in summary["exitCriteria"]:
        print("  [%s] %s" % ("x" if criterion["met"] else " ", criterion["criterion"]))
    print("  gate           %s" % ("PASSED" if passed else "FAILED"))
    print("  receipt        %s" % summary_path.as_posix())

    return 0 if passed else 6


if __name__ == "__main__":
    raise SystemExit(main())
