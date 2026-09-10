"""Map reported execution observations onto real circuit nodes.

This is the piece that makes a run visible on the diagram rather than in a log.
It takes a run's observations and a scene, and decides — per the declared
mapping, not per capability — which node each observation illuminates.

What it refuses to do matters as much as what it does:

  * a **delivery phase never lights a node.** Those are the service's own stages;
    the circuit is scenario topology. Drawing delivery progress on it would claim
    evidence the instrumentation never produced.
  * an observation it cannot place is **retained and reported**, never dropped.
    A vanished event makes the circuit look more complete than the run was.
  * a step declared as establishing a **scope** marks the node active and says
    mechanic-level detail is unavailable, because the observation identified an
    operation and not which internal mechanic ran.
  * every repeat of a step gets its **own occurrence**, so a loop or a retry is
    not silently collapsed into one highlight.

Verified against retained hosted runs rather than against documentation: the
step and phase vocabulary here was read out of real run records.

Usage
-----
    python adapters/trace/map_execution.py --self-check

Exit codes
----------
    0  every check passed
   21  a mapping check failed
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent.parent
DEFAULT_MAPPING = WORKBENCH / "profiles" / "estate-execution.mapping.json"

sys.path.insert(0, str(WORKBENCH / "tools"))
from resolve_ui_dependencies import now_utc, sha256_file  # noqa: E402


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def index_scene(scene: dict) -> dict:
    """Group a scene's nodes by kind, preserving declared order within each kind."""
    by_kind: dict = {}
    for node in scene["graph"]["nodes"]:
        by_kind.setdefault(node["kind"], []).append(node)
    return by_kind


def map_run(observations: list, scene: dict, mapping: dict) -> dict:
    """Place each observation, or record why it could not be placed."""
    by_kind = index_scene(scene)
    steps = {s["stepId"]: s for s in mapping["scenarioSteps"]}
    phases = {p["phase"]: p for p in mapping["deliveryPhases"]}
    scenario_type = mapping["observationTypes"]["scenario"]
    delivery_type = mapping["observationTypes"]["delivery"]

    placed, telemetry, unplaced = [], [], []
    occurrences: dict = {}

    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            continue
        kind = observation.get("observationType")

        if kind == delivery_type:
            phase = phases.get(observation.get("phase"))
            if phase is None:
                unplaced.append({"index": index, "observation": observation,
                                 "reason": "undeclared delivery phase"})
                continue
            # Deliberately carries no nodeId: delivery is not circuit topology.
            telemetry.append({
                "index": index, "phase": observation["phase"], "label": phase["label"],
                "meaning": phase["meaning"], "status": observation.get("status"),
                "observedAt": observation.get("observedAt"),
            })
            continue

        if kind != scenario_type:
            unplaced.append({"index": index, "observation": observation,
                             "reason": "unrecognised observation type"})
            continue

        step = steps.get(observation.get("stepId"))
        if step is None:
            unplaced.append({"index": index, "observation": observation,
                             "reason": "undeclared scenario step"})
            continue

        node = None
        for candidate_kind in step["nodeKinds"]:
            if by_kind.get(candidate_kind):
                node = by_kind[candidate_kind][0]
                break
        if node is None:
            unplaced.append({
                "index": index, "observation": observation,
                "reason": "this view carries none of the node kinds %s"
                          % ", ".join(step["nodeKinds"])})
            continue

        # A repeated step is a distinct occurrence, not a re-highlight.
        key = (observation.get("executionId"), observation["stepId"])
        occurrences[key] = occurrences.get(key, 0) + 1

        placed.append({
            "index": index,
            "stepId": observation["stepId"],
            "sequence": observation.get("sequence"),
            "nodeId": node["id"],
            "nodeIdentity": node["identity"],
            "nodeKind": node["kind"],
            "establishes": step.get("establishes", "node"),
            "mechanicDetail": ("unavailable" if step.get("establishes") == "scope"
                               else "not-applicable"),
            "executionId": observation.get("executionId"),
            "parentExecutionId": observation.get("parentExecutionId"),
            "occurrence": occurrences[key],
            "observedAt": observation.get("observedAt"),
            "meaning": step["meaning"],
        })

    return {
        "sceneId": scene["sceneId"],
        "viewKind": scene["identities"]["viewKind"],
        "placed": placed,
        "telemetry": telemetry,
        "unplaced": unplaced,
        "coverage": {
            "observations": len(observations),
            "placedOnCircuit": len(placed),
            "shownAsTelemetry": len(telemetry),
            "unplaced": len(unplaced),
            "nodesTouched": len({p["nodeId"] for p in placed}),
            "nodesInView": len(scene["graph"]["nodes"]),
        },
    }


def self_check() -> int:
    mapping = load(DEFAULT_MAPPING)
    catalogue = load(WORKBENCH / "fixtures" / "estate" / "estate-catalogue.json")
    results, failures = [], 0

    def check(label, condition, detail=None):
        nonlocal failures
        if not condition:
            failures += 1
        results.append({"case": label, "passed": bool(condition), "detail": detail})
        print("  %s %s" % ("ok  " if condition else "FAIL", label))
        if not condition and detail is not None:
            print("       %s" % json.dumps(detail)[:300], file=sys.stderr)

    scenes = {}
    for capability in catalogue["capabilities"]:
        # Only capabilities whose scene is resolvable locally can be mapped; the
        # rest are catalogued and load their view on demand.
        operations = [v for v in capability["views"]
                      if v["viewKind"] == "operations" and v.get("scene")]
        if operations:
            scenes[capability["capabilityId"]] = load(WORKBENCH / operations[0]["scene"])

    check("every invocable capability has an operations view to map onto",
          len(scenes) == sum(1 for c in catalogue["capabilities"]
                             if "invoke" in c["affordances"]),
          sorted(scenes))

    # Real retained runs, not fabricated observations.
    runs = []
    for path in sorted(glob.glob(str(WORKBENCH / "evidence" / "browser" / "*.json"))):
        document = load(Path(path))
        if not isinstance(document, dict) or "events" not in document:
            continue
        subject = (document.get("snapshot", {}).get("selection", {}) or {}).get("subject")
        observations = [e["observation"] for e in document["events"]
                        if isinstance(e.get("observation"), dict)]
        if observations:
            runs.append({"file": Path(path).name, "subject": subject,
                         "observations": observations})

    check("retained hosted runs are available to map", len(runs) >= 6, len(runs))

    mapped_any = None
    for run in runs:
        scene = scenes.get(run["subject"])
        if scene is None:
            continue
        outcome = map_run(run["observations"], scene, mapping)
        mapped_any = mapped_any or outcome
        run["mapped"] = outcome

    mapped = [r for r in runs if r.get("mapped")]
    check("runs map onto the real circuits of their own capabilities",
          len(mapped) >= 3, [r["subject"] for r in mapped])

    if mapped_any:
        check("no delivery phase was drawn on the circuit",
              all(all(p["stepId"] != "readAuthority" for p in r["mapped"]["placed"])
                  for r in mapped))
        check("delivery phases are retained as telemetry",
              all(r["mapped"]["coverage"]["shownAsTelemetry"] > 0 for r in mapped),
              [r["mapped"]["coverage"] for r in mapped])
        check("every scenario observation was placed on a node",
              all(r["mapped"]["coverage"]["unplaced"] == 0 for r in mapped),
              [r["mapped"]["unplaced"] for r in mapped if r["mapped"]["unplaced"]])
        check("input, authority, execution and outcome all light a node",
              all({p["stepId"] for p in r["mapped"]["placed"]} >=
                  {"admit-input", "resolve-event-authority", "execute-event-authority",
                   "admit-outcome", "resolve-disposition"} for r in mapped))
        check("the execution step marks mechanic detail unavailable rather than guessing",
              all(any(p["stepId"] == "execute-event-authority"
                      and p["mechanicDetail"] == "unavailable"
                      for p in r["mapped"]["placed"]) for r in mapped))
        check("nothing is lost: placed + telemetry + unplaced equals observations",
              all(r["mapped"]["coverage"]["placedOnCircuit"]
                  + r["mapped"]["coverage"]["shownAsTelemetry"]
                  + r["mapped"]["coverage"]["unplaced"]
                  == r["mapped"]["coverage"]["observations"] for r in mapped))

    # An observation the mapping does not know must be retained, not dropped.
    probe_scene = next(iter(scenes.values()))
    probe = map_run([{"observationType": "scenario-execution-observation.v1",
                      "stepId": "some-future-step", "sequence": 0},
                     {"observationType": "something-else", "status": "observed"}],
                    probe_scene, mapping)
    check("an unknown step and an unknown type are both retained as unplaced",
          probe["coverage"]["unplaced"] == 2 and probe["coverage"]["placedOnCircuit"] == 0)

    # A view without the needed node kinds must say so rather than mis-place.
    bare = {"sceneId": "probe", "identities": {"viewKind": "expression"},
            "graph": {"nodes": [{"id": "n1", "identity": "x", "kind": "event"}], "routes": []}}
    bare_mapped = map_run([{"observationType": "scenario-execution-observation.v1",
                            "stepId": "admit-input", "sequence": 0}], bare, mapping)
    check("a view lacking the node kind reports unplaced instead of mis-placing",
          bare_mapped["coverage"]["unplaced"] == 1)

    receipt = {
        "receiptType": "execution-mapping-receipt.v1",
        "checkedAt": now_utc(),
        "statement": "Reported observations from retained hosted runs are mapped onto the real "
                     "circuits of their own capabilities. Scenario execution lights nodes; "
                     "delivery phases are retained as telemetry and light nothing; anything "
                     "unrecognised is retained as unplaced.",
        "mapping": {"path": DEFAULT_MAPPING.relative_to(WORKBENCH).as_posix(),
                    "sha256": sha256_file(DEFAULT_MAPPING),
                    "mappingId": mapping["mappingId"]},
        "runs": [{"file": r["file"], "subject": r["subject"],
                  "coverage": r["mapped"]["coverage"],
                  "nodesLit": sorted({p["nodeIdentity"] for p in r["mapped"]["placed"]})}
                 for r in mapped],
        "summary": {"cases": len(results), "passed": len(results) - failures,
                    "failed": failures},
        "cases": results,
        "notEstablished": [
            "Nothing was executed here; these are retained observations from earlier hosted "
            "runs, read as data.",
            "A delivery phase establishes that a service stage ran, not that any circuit node "
            "did. It is never drawn on the circuit.",
        ],
    }
    receipt_dir = WORKBENCH / "evidence" / "trace"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / "execution-mapping.receipt.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("mapping        %d/%d cases passed" % (len(results) - failures, len(results)))
    print("receipt        %s" % (receipt_dir / "execution-mapping.receipt.json").as_posix())
    return 0 if failures == 0 else 21


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-check", action="store_true")
    parser.parse_args(argv)
    return self_check()


if __name__ == "__main__":
    raise SystemExit(main())
