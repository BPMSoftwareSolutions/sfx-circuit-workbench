# sfx-circuit-workbench

The primary interface for understanding, creating, composing, invoking and operating the
SideFX capability estate — with the circuit visible throughout.

> The circuit is not a visualization of SideFX. The circuit is the interface to SideFX.

See the [architecture plan](docs/circuit-workbench-architecture-plan.md) for the full direction
and the M0–M5 delivery sequence.

## Status

| Milestone | State |
| --- | --- |
| **M0** — Freeze the reference and make composition portable | local baseline and dependency checks pass |
| **M1** — Recreate the full source workbench | source views hosted; complete visual parity and website rollout remain |
| **M2** — Invoke real capabilities from the circuit | implemented in the private Space; [delivery record](docs/hosted-workbench-delivery.md) |
| M3 — Observe execution live | real delivery phases and scenario observations; deeper mechanic coverage remains |
| M4 — Author and compose capabilities | not started |
| M5 — Build and deploy bounded services | not started |

Open the [private SideFX Space](https://huggingface.co/spaces/BPMSoftwareSolutions/SideFX).
Choose a **Run capability** source view, then select its input node. Generated controls,
actual remote execution, reported circuit observations and a generated outcome dialog now
form one flow. The original complete authoring blueprint and native source view remain
available for inspection. The [delivery record](docs/hosted-workbench-delivery.md) identifies
the deployed revisions, evidence and remaining limits.

## Running the gates

```sh
python tools/m2_gate.py    # runs M1 too, then UX compilation, dialogs, boundary and outcomes
python tools/m1_gate.py    # runs M0 too, then scenes, profiles, both builds and parity
python tools/m0_gate.py    # the baseline and dependency gate alone
```

Each exits non-zero if a local step fails. M2 also evaluates retained hosted evidence against
the exact package fingerprint; its receipt distinguishes local success from milestone completion.
`python tools/verify_hosted.py` exits non-zero when the hosted evidence is absent, stale or fails.

Individual steps:

```sh
python tools/resolve_ui_dependencies.py          # resolve + verify the pinned UI closure
python tools/reproduce_specimen_receipts.py      # recompose retained specimens, compare fields
python tools/freeze_baseline.py --verify         # re-digest the frozen reference
python adapters/topology/ingest_topology_view.py # legacy products -> circuit-scene.v1
python providers/build_provider_profile.py       # presentation profile -> provider profile
python tools/derive_layout_profile.py            # layout profile -> derived surface
python tools/build_workbench.py                  # compose -> project -> lower -> package
python tools/verify_m1_parity.py                 # fidelity, controls, optionality, architecture
node tests/trace-parity.test.cjs                 # planner vs the frozen reference
node tests/overlay-provider.test.cjs            # anchoring, lifecycle and focus containment

python adapters/invocation/compile_capability_ux.py       # contracts + ownership + UX -> plans
python adapters/invocation/build_dialog_surface.py        # plans -> composed dialogs
python adapters/invocation/invocation_adapter.py --self-check   # command boundary and refusals
python adapters/invocation/present_outcome.py --self-check      # outcomes bound to real data
```

Requires Python 3.12 with `jsonschema`; the composition closure also needs `networkx`. The
trace parity test needs Node.

## Building an experience

```sh
python tools/build_workbench.py --experience experiences/source-inspection/source-inspection.experience.json
```

The package lands in `build/<experienceId>/package/` — a projected document, its interaction
plan, both scenes with their screened SVG artifacts, the textures they reference, the trace
planner and the workbench runtime.

Findings are reconciled against the experience's `expectedFindings` register. A declared
finding is a recorded boundary; an undeclared one fails the build.

## Relocation

Consumed workspaces are pinned by capability identity, not by path. Both roots are relocatable:

| Variable | Selects |
| --- | --- |
| `SIDEFX_UI_ROOT` | the `sidefx-ui` capability closure |
| `SFX_ESTATE_TOPOLOGY_PRODUCTS` | the restored topology products |
| `SFX_ESTATE_TOPOLOGY_TEMPLATE` | the viewer template, stylesheet and runtime |

Resolution order is the command-line argument, then the environment variable, then the declared
default. Every tool accepts `--root` (or `--products` / `--template`) directly.

The workbench consumes the provisional `sidefx-ui` composition pipeline. This increment also
extends its generic browser runtime with scoped mounts, draft initialization, Unicode length
checks and action events. Domain schemas and capability selection stay outside that runtime.

## Layout

```text
adapters/      Ingestion from the existing topology compiler's products
build/         Generated packages — never edit by hand
contracts/     Schemas for what this workspace authors
dependencies/  The pinned, relocatable UI capability closure
docs/          Architecture plan and milestone records
evidence/      Generated receipts — never edit by hand
experiences/   Authored modes; experiences/derived/ is generated
fixtures/      The frozen reference baseline and the ingested scenes
profiles/      Presentation and layout profiles
providers/     Provider profiles; providers/resolved/ is generated
runtime/       Workbench-owned browser mechanics; the decisions are pure modules,
               the DOM layer only carries them out
tests/         Behavioural parity tests
tools/         Resolution, building, verification and gate runners
```

`build/`, `evidence/`, `providers/resolved/`, `experiences/derived/` and `fixtures/scenes/`
are generated. Re-run the gate rather than editing them.

`tools/package_interactive_workbench.py` builds the hosted workbench. This repository
also owns its [Hugging Face deployment tooling](deploy/README.md): Docker configuration,
host packaging, upload, status, and verification of the running release.

```sh
python -m pip install -r deploy/requirements.txt
python tools/hf_space.py package --platform-root C:/lab/repos/sfx-platform
python tools/hf_space.py check
python tools/hf_space.py deploy
```

The packager consumes the existing `sfx-platform` host and this repository's built
assets directly. `tools/install_host.mjs` remains available for installing the assets
into the website checkout. The remote invocation service has a separate deployment.

## Where change belongs

| Change | Change point |
| --- | --- |
| Colour, material, typography, density | a presentation profile in `profiles/` |
| Move a region, resize a track | a layout profile in `profiles/` |
| A new control or a new meaning | the surface authority in `experiences/` |
| A different renderer for the circuit | a scene provider against `circuit-scene.v1` |
| A capability's dialog labels, grouping, order | its `capability-ux.v1` declaration |
| A new input control or outcome family | `providers/component-registry.v1.json` |

The first two are enforced: a presentation profile that redefines or removes a basis
realization is refused, and a layout profile that changes any identity is refused. So is the
fourth: a UX declaration cannot widen a contract, invent a successful outcome, or make a
system-owned field editable.

## The invocation boundary

A browser may submit a publication id, a published subject, editable values keyed by declared
pointer, an optional example selection and a request identity. Nothing else — a canonical
envelope, namespace override, fixed-field override, provider inventory or endpoint selection has
no place in `workbench-command.v1`.

```sh
python adapters/invocation/invocation_adapter.py --command FILE --emit-canonical
```

shows the canonical input the server would construct, without contacting it.
