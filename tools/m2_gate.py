"""Check M2 local invariants and retained evidence for the exact hosted package.
The browser suites execute effects separately; this gate only verifies evidence.
Exit 0 means local checks pass; milestoneComplete additionally requires hosted proof.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent
EVIDENCE = WORKBENCH / "evidence"

sys.path.insert(0, str(HERE))
from resolve_ui_dependencies import now_utc, sha256_file  # noqa: E402


def run_step(name: str, argv: list, findings: list, quiet: bool = False) -> dict:
    result = subprocess.run([sys.executable] + argv, capture_output=True, text=True,
                            cwd=str(WORKBENCH))
    record = {"step": name, "exitCode": result.returncode, "passed": result.returncode == 0}
    if not quiet:
        print(result.stdout, end="")
    if result.returncode != 0:
        record["detail"] = (result.stderr or result.stdout).strip()[-1500:]
        findings.append({"code": "M2_STEP_FAILED", "severity": "error",
                         "detail": "%s exited %d" % (name, result.returncode)})
        print(result.stderr, end="", file=sys.stderr)
    return record


def run_node(name: str, script: str, findings: list, extra: list | None = None) -> dict:
    """Run a Node check, reporting an absent Node as a failure rather than a skip."""
    if shutil.which("node") is None:
        findings.append({"code": "M2_CHECK_UNRUN", "severity": "error",
                         "detail": "node is unavailable, so %s was not verified" % name})
        print("  %-14s UNRUN (node unavailable)" % name)
        return {"step": name, "exitCode": None, "passed": False, "detail": "node unavailable"}
    result = subprocess.run(["node", script] + (extra or []), capture_output=True, text=True,
                            cwd=str(WORKBENCH))
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr, end="", file=sys.stderr)
        findings.append({"code": "M2_STEP_FAILED", "severity": "error",
                         "detail": "%s exited %d" % (name, result.returncode)})
    return {"step": name, "exitCode": result.returncode, "passed": result.returncode == 0}


def run_ux(findings: list) -> dict:
    """Drive the served workbench through its journeys, if one is being served."""
    import urllib.error
    import urllib.request
    origin = os.environ.get("SFX_WORKBENCH_ORIGIN", "http://127.0.0.1:8787")
    try:
        urllib.request.urlopen(origin + "/index.html", timeout=3).read(64)
    except (urllib.error.URLError, OSError):
        findings.append({"code": "M2_UX_UNRUN", "severity": "error",
                         "detail": "nothing served at %s; serve a built package and re-run"
                                   % origin})
        print("  user journeys  UNRUN (no package served at %s)" % origin)
        return {"step": "ux-journey", "exitCode": None, "passed": False,
                "detail": "no served package"}
    return run_node("ux-journey", "tests/ux-journey.test.mjs", findings, [origin])


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None)
    args = parser.parse_args(argv)
    root_argv = ["--root", args.root] if args.root else []
    findings: list = []

    print("== M1 gate ==")
    steps = [run_step("m1-gate", ["tools/m1_gate.py"] + root_argv, findings, quiet=True)]
    print("  %s" % ("passed" if steps[-1]["passed"] else "FAILED"))

    print("\n== capability UX compilation ==")
    steps.append(run_step("compile-capability-ux",
                          ["adapters/invocation/compile_capability_ux.py"], findings))

    print("\n== generated dialogs ==")
    steps.append(run_step("build-dialog-surfaces",
                          ["adapters/invocation/build_dialog_surface.py"] + root_argv, findings))

    print("\n== command boundary ==")
    steps.append(run_step("invocation-boundary",
                          ["adapters/invocation/invocation_adapter.py", "--self-check"], findings))

    print("\n== outcome presentation ==")
    steps.append(run_step("outcome-presentation",
                          ["adapters/invocation/present_outcome.py", "--self-check"], findings))

    print("\n== user journeys ==")
    # Opening the page is the only check that sees what a person sees. It needs a
    # browser and a served package, so it reports UNRUN rather than passing when
    # either is absent — a UX suite that silently skips is worse than none.
    steps.append(run_ux(findings))

    print("\n== overlay provider ==")
    steps.append(run_node("overlay-provider", "tests/overlay-provider.test.cjs", findings))

    compilation = load(EVIDENCE / "invoke" / "capability-ux-compilation.receipt.json")
    dialogs = load(EVIDENCE / "invoke" / "dialog-composition.receipt.json")
    boundary = load(EVIDENCE / "invoke" / "invocation-boundary.receipt.json")
    presentation = load(EVIDENCE / "invoke" / "outcome-presentation.receipt.json")
    overlay = load(EVIDENCE / "invoke" / "overlay-provider.receipt.json")

    compiled_ok = bool(compilation and compilation["summary"]["refused"] == 0
                       and compilation["summary"]["compiled"] == 4)
    dialogs_ok = bool(dialogs and dialogs["summary"]["failed"] == 0)
    boundary_ok = bool(boundary and boundary["summary"]["failed"] == 0)
    presentation_ok = bool(presentation and presentation["summary"]["failed"] == 0)
    overlay_ok = bool(overlay and overlay["summary"]["failed"] == 0)
    built = compiled_ok and dialogs_ok and boundary_ok and presentation_ok and overlay_ok

    from verify_hosted import assess
    hosted = assess()
    criteria = [
        {"criterion":"Contracts, ownership, dialogs, lifecycle and command boundary pass local checks", "met":built},
        {"criterion":"The current package executes all seven pilot cases in Hugging Face, presents real outcomes and reconciles durable runs", "met":hosted["passed"], "findings":hosted["findings"]},
    ]
    local_pass = bool(steps) and all(s["passed"] for s in steps) and not findings
    summary = {
        "receiptType":"milestone-gate-receipt.v1", "milestone":"M2",
        "evaluatedAt":now_utc(), "locallyVerifiablePassed":local_pass,
        "milestoneComplete":local_pass and all(c["met"] for c in criteria),
        "steps":steps, "exitCriteria":criteria, "findings":findings,
        "hosted":hosted,
        "notEstablished":["Full mechanic-level observability, website embed rollout, capability authoring/composition and microservice deployment are subsequent increments."],
    }

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    summary_path = EVIDENCE / "m2-gate.receipt.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print("\n== M2 gate ==")
    for criterion in criteria:
        mark = "x" if criterion["met"] else ("~" if criterion.get("partial") else " ")
        print("  [%s] %s" % (mark, criterion["criterion"]))
    print("  local steps    %s" % ("all passed" if local_pass else "FAILED"))
    print("  milestone      %s" % ("COMPLETE" if summary["milestoneComplete"]
                                   else "INCOMPLETE — see the local and hosted evidence findings"))
    print("  receipt        %s" % summary_path.as_posix())

    return 0 if local_pass else 17


if __name__ == "__main__":
    raise SystemExit(main())
