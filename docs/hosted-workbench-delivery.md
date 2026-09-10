# First hosted workbench increment

10 September 2026

The circuit workbench now runs in the [private SideFX Space](https://huggingface.co/spaces/BPMSoftwareSolutions/SideFX). It composes its surfaces through `sidefx-ui` and connects generated input and outcome dialogs to the existing database-backed invocation service. The implementation covers the first usable invocation increment of the [architecture plan](circuit-workbench-architecture-plan.md). Capability authoring, workflow composition and estate-derived microservice deployment remain subsequent work.

## Use it

Choose a **Run capability** entry in **Source view**, then select its input component. The **Enter Capability Input** dialog contains only declared user controls. Submit admits a durable request, collapses the dialog toward its node and begins displaying reported execution observations. A terminal outcome opens a generated dialog at the outcome node. Closing that dialog retains its result indicator; selecting the node reopens the result.

| Runnable view | User input | Actual execution and outcome |
| --- | --- | --- |
| Hello World | Explicit Submit | Database-selected scenario returns greeting text. |
| Personal greeting | Required name, 1–100 Unicode characters | The scenario preserves supplied text; the generated greeting renders as escaped text. |
| Provider demonstration | Select one of four retained examples | The service supplies the entire canonical fixture. Both resolved and not-observable outcomes show disposition, considered/eligible counts, provider reasons, findings and trace digests. |
| Live stock price | Symbol and declared region | The remote service invokes the declared RapidAPI binding and runs database-selected normalization. The generated outcome shows price, currency, market time, quote details, provider identity and evidence. |

The provider demonstration is explicitly a retained fixture. Live finance obtains a new provider response. A normal terminal return and a positive domain disposition remain separate facts.

## Viewport layout and zoom

The workbench occupies the full width and height of the Space's application viewport. The circuit takes the remaining height between the header, controls and footer. Component details and routes are available in a collapsible bottom panel. The Space declares `fullWidth: true` and `header: mini` using the [Hugging Face configuration](https://huggingface.co/docs/hub/spaces-config-reference).

`profiles/viewport.host.json` declares the fluid host tracks and inspection panel. `tools/compile_viewport.py` lowers the existing surface's stack/grid declarations into CSS without changing component identities or capability contracts. The host runtime refits the diagram when the outer circuit frame changes size, including when details expand. Dialog anchors follow those layout changes.

The first viewport deployment exposed a zoom regression with visible scrollbars: their appearance resized the observed content box and triggered Fit, undoing the requested zoom. The observer now watches the border box and checks the outer dimensions, so scrollbar changes preserve the camera. Manual zoom, Read at 100%, scrolling and the existing 200% zoom limit remain available.

`tests/browser_viewport.py` checks zoom with visible scrollbars, 100% and 200%, scrolling the enlarged diagram, Fit, five viewport sizes, the details panel and an input dialog after resizing. Chromium runs with its default headless scrollbar-hiding option removed so this regression is observable. The viewport release has its own [hosted verification record](../evidence/host/viewport-verification.json); the broader invocation and restart evidence below describes the earlier release.

## What changed and where

`experiences/invoke/*.ux.json` declares dialog labels, field grouping, ownership-compatible controls, outcome layouts and node anchors. `adapters/invocation/compile_capability_ux.py` binds these declarations to the published input and outcome schema digests and provider binding digest. Unknown input shapes, outcome pointers and collection fields produce compilation findings. Schemas continue to determine admissible values; UX declarations cannot widen them.

`build_dialog_surface.py` produces semantic surfaces and submits them to the existing composition, projection and interaction-lowering capabilities in `sidefx-ui`. `tools/package_interactive_workbench.py` packages those resolved dialogs with the workbench and its scenes. Changing labels, layout or outcome fields does not require a capability-specific browser branch.

The generic `sidefx-ui` browser runtime now supports independent scoped mounts, initial draft state, Unicode string-length validation and emitted action dispositions. Workbench code owns modal placement, camera behavior, run association and circuit observations. Outcome providers consume bound values and already-resolved display operations; contract identity and variant selection happen on the server. A contract may identify its instances through the pinned publication without requiring a `contractId` property in every returned JSON object.

The existing `sfx-platform` host supplies `/workbench/session`, `/workbench/runs` and `/workbench/runs/<runId>`. Its authenticated server adapter calls the existing Azure capability service. The private Space is the renderer; SQL and RapidAPI credentials remain on the invocation service. The earlier `/lab` remains available.

The entity-neutral `sidefx-cli` SDK gains an optional observation callback. The database delivery emits a bounded, allowlisted observation channel while keeping the command-result protocol unchanged. Observer failures cannot change execution results. No business command or new capability authority was introduced by this telemetry transport.

## Source views and trace meaning

The complete retained authoring blueprint has **30 nodes / 45 routes**; the native execution source view has **10 nodes / 9 routes**. Both are hosted. Fit uses the available circuit viewport; the percentage varies with window size and the inspection panel.

The four runnable views are additional **scenario interface views** derived from verified SQL input/event/outcome relationships. Finance also shows the declared provider binding and its native input boundary. They explicitly state their limited scope. They do not replace the complete source scenes or claim to depict every internal mechanic.

`LIVE` reflects genuine delivery-phase and kernel observations received from the service. Polling uses a 350 ms interval, ordered event IDs and a resume cursor. Source-only illustrative playback is separate and cannot run during an active invocation. A phase marked completed means that phase returned; the actual terminal outcome determines the domain disposition. No timer invents a successful branch or intermediate result. Very fast kernel steps can arrive together in one poll.

## Admission, recovery and persistence

The browser uses a signed, HttpOnly session cookie. The service token stays in the host's server environment. Mutation requests require the configured origin. The remote service binds each request ID and canonical request digest to its session owner, enforces the published capability/input policy and shares capacity limits with the existing command endpoint.

Admission is written and fsynced before the service returns a run ID. Events and terminal results are retained in `/home/sidefx-runs` with [App Service persistent storage enabled](https://learn.microsoft.com/en-us/azure/app-service/configure-custom-container). A repeated identical request reconciles to the same run; a conflicting request body is refused. A response lost after admission remains uncertain. Reload restores that uncertainty and offers explicit reconciliation using the original request; it never silently creates a replacement invocation.

A completed run survives service restart. An interrupted run without retained terminal evidence becomes `UNKNOWN` and is never automatically re-executed. This is persistent request deduplication, not a claim of exactly-once external side effects. Run snapshots include a service-instance identity so the restart test proves that a different process read the retained result.

This implementation is deliberately limited to **one service worker**. Distributed admission, concurrent deployment handoff, journal retention/compaction and production tenant authorization require a shared transactional store and further operational work. The private Space plus scoped browser sessions forms the current pilot access boundary. Closing a dialog does not cancel an admitted execution; no runtime cancellation is offered.

## Verification and release evidence

**Invocation release `5f3f33b7b392c6eb270a47a7e861cec7cbc8cfae`: passed** all seven hosted execution cases, all three browser interaction suites, response-loss reconciliation and service-restart persistence. The M2 gate reported complete for that invocation increment. Its retained fingerprint-based evidence does not certify subsequent viewport packages.

The authoritative release identifiers and Space status are in [deployment.json](../evidence/host/deployment.json). The package receipt records asset digests and a content fingerprint. Browser proofs record that same fingerprint, the actual run identity, returned data, browser errors and observation receipt times.

The hosted verification gate checks:

- All seven cases: Hello World, personal greeting, live finance and every retained provider example.
- Generated outcome presentation and a reported execution observation received before terminal completion.
- Live finance's HTTP exchange, provider evidence and equality between the displayed normalized price and its own retained native testimony.
- Chromium, Firefox and WebKit checks for required/length validation, keyboard opening, focus return, reduced motion, draft retention and cancellation without invocation.
- Loss of an admission response, reload, explicit deduplication and a completed result retained across an actual service restart.

Evidence: [hosted verification](../evidence/host/verification.json), [browser checks](../evidence/browser/interaction-checks.json), [recovery](../evidence/browser/recovery.json), [blueprint screenshot](../evidence/browser/hosted-blueprint.png), [native source screenshot](../evidence/browser/hosted-native.png), [finance outcome](../evidence/browser/resolve-equity-market-price-evidence-outcome.png).

Local verification includes 17 SDK tests, 15 database/embodiment tests, eight service tests, the provider-presentation regression, TypeScript checking and the existing composition/parity/lifecycle gates. The local gate remains distinct from hosted evidence; stale or local-only browser results cannot satisfy hosted acceptance.

## Rebuild and verify

From `C:\lab\sfx-circuit-workbench`:

```powershell
node tools/read_invocation_authority.mjs
python tools/package_interactive_workbench.py
node tools/install_host.mjs
```

The Space's [deployment tooling](../deploy/README.md) now lives in this repository. Run `python tools/hf_space.py package`, `python tools/hf_space.py check`, and `python tools/hf_space.py deploy` from the workbench root; installing assets into the website checkout first is optional. The packager consumes the existing `sfx-platform` host and preserves the Space's configured secrets.

For remote service changes, `C:\lab\repos\sfx-platform\scripts\package-remote-lab.mjs` remains the service packager. Promote that service by immutable image digest and preserve App Service storage and the single-worker setting. The deployed service image is recorded in the release evidence; do not substitute a mutable tag when verifying a release.

Run `tests/browser_workbench.py --origin https://bpmsoftwaresolutions-sidefx.hf.space --subject <published-subject>` for each case, supplying `--example` for the four fixtures. `browser_interactions.py` covers the three engines. `browser_recovery.py --restart-service` deliberately restarts the existing service and should run after other invocations finish. Private browser tests consume `HF_TOKEN` from the environment; credentials are not written into evidence.

`python tools/verify_hosted.py` checks the retained proof against the current package. `python tools/m2_gate.py` additionally reruns local checks. Editing or rebuilding the hosted package invalidates prior fingerprint-based acceptance until the changed package is deployed and verified.

## Next increment

Finish detailed visual parity and the website embed rollout. Then expand runtime observation mappings from scenario interfaces to the full native mechanic graph, including explicit branch/route evidence and reconnect gaps. The authoring conveyor, voice/description input, workflow compatibility, integrated circuits and bounded service deployment should consume these same scene, UX, authority and run contracts. None of those later estate-management functions is claimed complete by this release.
