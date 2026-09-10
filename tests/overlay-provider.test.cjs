/* Checks for the overlay provider's decision-making.
 *
 * No browser is available here, so the provider was split: every decision that
 * can be made from numbers lives in a pure module, and the DOM layer only
 * carries decisions out. This exercises the decisions. What it cannot reach —
 * whether focus actually moved, whether the animation actually ran — is listed
 * as not established, in the receipt and in the record.
 *
 * Run:  node tests/overlay-provider.test.cjs
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const WORKBENCH = path.resolve(__dirname, "..");
const anchor = require(path.join(WORKBENCH, "runtime/overlay-anchor.js"));
const lifecycle = require(path.join(WORKBENCH, "runtime/dialog-lifecycle.js"));
const focus = require(path.join(WORKBENCH, "runtime/focus-containment.js"));

const results = [];
let failures = 0;

function check(group, label, condition, detail) {
  const passed = Boolean(condition);
  if (!passed) { failures += 1; }
  results.push({ group, case: label, passed, detail: passed ? undefined : detail });
  console.log(`  ${passed ? "ok  " : "FAIL"} ${group} · ${label}`);
  if (!passed && detail !== undefined) {
    console.error(`       ${JSON.stringify(detail)}`);
  }
}

/* ---------------------------------------------------------------- anchoring */

const scene = JSON.parse(fs.readFileSync(
  path.join(WORKBENCH, "fixtures/scenes/blueprint-authority.scene.json"), "utf8"));
const someNode = scene.graph.nodes[0];
const camera = { scale: 0.5, stageOrigin: { x: 100, y: 80 } };

{
  const box = scene.geometry.boxes[someNode.id];
  const point = anchor.graphPointToViewport(box, camera.scale, camera.stageOrigin);
  check("anchor", "a node's centre maps through the camera into viewport space",
    point.x === 100 + (box[0] + box[2] / 2) * 0.5
    && point.y === 80 + (box[1] + box[3] / 2) * 0.5, point);

  const zoomed = anchor.graphPointToViewport(box, 1, camera.stageOrigin);
  check("anchor", "changing the camera moves the anchor, so it must be recomputed",
    zoomed.x !== point.x || zoomed.y !== point.y);

  const resolved = anchor.resolveAnchor(
    { kind: "scene-node", nodeIdentity: someNode.identity }, scene, camera);
  check("anchor", "an identity present in the scene resolves to a point",
    resolved.available && resolved.entityId === someNode.id
    && resolved.strategy === "expand-from-anchor", resolved);

  const missing = anchor.resolveAnchor(
    { kind: "scene-node", nodeIdentity: "cell:scenario:not-in-this-view:input" }, scene, camera);
  check("anchor", "an absent identity falls back to a labelled viewport transition",
    !missing.available && missing.strategy === "viewport-transition"
    && typeof missing.reason === "string", missing);
  check("anchor", "the fallback still carries the semantic identity",
    missing.nodeIdentity === "cell:scenario:not-in-this-view:input");

  const noScene = anchor.resolveAnchor(
    { kind: "scene-node", nodeIdentity: someNode.identity }, null, camera);
  check("anchor", "with no scene loaded the anchor is unavailable, not an error",
    !noScene.available && noScene.nodeIdentity === someNode.identity);

  const wrongScene = anchor.resolveAnchor(
    { kind: "scene-node", nodeIdentity: someNode.identity, sceneId: "another/scene" },
    scene, camera);
  check("anchor", "an anchor naming a different scene does not resolve against this one",
    !wrongScene.available);
}

/* ---------------------------------------------------------------- placement */

{
  const viewport = { x: 0, y: 0, width: 1220, height: 827 };
  const size = { width: 560, height: 320 };

  const middle = anchor.placeOverlay({ x: 600, y: 300, width: 100, height: 60 }, size, viewport);
  check("placement", "an anchor with room below places below it",
    middle.side === "below" && !middle.clamped, middle);

  const nearBottom = anchor.placeOverlay(
    { x: 600, y: 800, width: 100, height: 60 }, size, viewport);
  check("placement", "an anchor near the bottom flips above rather than overflowing",
    nearBottom.side === "above" && !nearBottom.clamped, nearBottom);

  const corner = anchor.placeOverlay({ x: 5, y: 5, width: 10, height: 10 }, size, viewport);
  check("placement", "a corner anchor still yields a box inside the viewport",
    corner.x >= viewport.x && corner.y >= viewport.y
    && corner.x + size.width <= viewport.width
    && corner.y + size.height <= viewport.height, corner);

  const centred = anchor.placeOverlay(null, size, viewport);
  check("placement", "no anchor point centres the overlay",
    centred.side === "centre" && !centred.clamped, centred);

  const tooBig = anchor.placeOverlay({ x: 600, y: 300, width: 10, height: 10 },
    { width: 2000, height: 2000 }, viewport);
  check("placement", "an overlay larger than the viewport is reported, not drawn negative",
    tooBig.fits === false && tooBig.clamped === true && typeof tooBig.reason === "string",
    tooBig);

  const placement = anchor.placeOverlay({ x: 600, y: 300, width: 100, height: 60 },
    size, viewport);
  const origin = anchor.transformOrigin({ x: 600, y: 300 }, placement, size);
  check("placement", "the transform origin sits inside the overlay box",
    origin.x >= 0 && origin.x <= size.width && origin.y >= 0 && origin.y <= size.height,
    origin);
}

/* ------------------------------------------------------------------- motion */

{
  const declared = { open: "expand-from-anchor", close: "collapse-to-anchor",
                     reducedMotionFallback: "none" };
  const normal = anchor.resolveMotion(declared, "open", false);
  check("motion", "the declared intent is used when motion is not reduced",
    normal.intent === "expand-from-anchor" && normal.substituted === false);

  const reduced = anchor.resolveMotion(declared, "open", true);
  check("motion", "reduced motion substitutes the declared fallback",
    reduced.intent === "none" && reduced.substituted === true
    && reduced.declaredIntent === "expand-from-anchor", reduced);
}

/* ---------------------------------------------------------------- lifecycle */

{
  let s = lifecycle.initial();
  check("lifecycle", "a dialog starts closed with no run and no result",
    s.state === "CLOSED" && s.runId === null && s.resultIndicator === false);

  let step = lifecycle.reduce(s, { type: "open", anchorIdentity: "cell:x:input",
                                   returnFocusTo: "node-button" });
  s = step.state;
  check("lifecycle", "opening expands from the anchor and focuses the first control",
    s.state === "EDITING" && step.effects.includes("expandFromAnchor")
    && step.effects.includes("focusFirstControl"), step.effects);

  step = lifecycle.reduce(s, { type: "edit", pointer: "/payload/symbol", value: "AAPL" });
  s = step.state;
  check("lifecycle", "editing records the draft",
    s.draft["/payload/symbol"] === "AAPL");

  step = lifecycle.reduce(s, {
    type: "submit",
    findings: [{ componentId: "field-payload-region", message: "Region is required." }] });
  check("lifecycle", "an invalid submission submits nothing and keeps the dialog open",
    step.state.state === "EDITING" && step.state.invokedCount === 0
    && step.refused !== null, step);
  check("lifecycle", "an invalid submission preserves the draft and focuses the first invalid",
    step.state.draft["/payload/symbol"] === "AAPL"
    && step.effects.includes("preserveDraft")
    && step.effects.includes("focusFirstInvalidControl"), step.effects);
  s = step.state;

  step = lifecycle.reduce(s, { type: "cancel" });
  check("lifecycle", "cancelling before submit invokes nothing",
    step.state.state === "CLOSED" && step.state.invokedCount === 0
    && step.effects.includes("restoreFocus"));

  step = lifecycle.reduce(s, { type: "submit", findings: [], requestId: "req-1" });
  s = step.state;
  check("lifecycle", "a valid submission moves to submitting and counts one invocation",
    s.state === "SUBMITTING" && s.invokedCount === 1
    && step.effects.includes("preventDuplicateSubmission"));

  const duplicate = lifecycle.reduce(s, { type: "submit", findings: [], requestId: "req-2" });
  check("lifecycle", "a second submit while in flight is refused, not queued",
    duplicate.state.state === "SUBMITTING" && duplicate.state.invokedCount === 1
    && duplicate.refused !== null, duplicate.refused);

  const cancelInFlight = lifecycle.reduce(s, { type: "cancel" });
  check("lifecycle", "a submitted request cannot be cancelled from the dialog",
    cancelInFlight.state.state === "SUBMITTING" && cancelInFlight.refused !== null);

  step = lifecycle.reduce(s, { type: "admitted", runId: "run-1" });
  s = step.state;
  check("lifecycle", "admission associates the run and collapses toward the input node",
    s.state === "ADMITTED" && s.runId === "run-1"
    && step.effects.includes("collapseToInputAnchor")
    && step.effects.includes("showRunOnCircuit"), step.effects);

  step = lifecycle.reduce(s, { type: "executing", event: { stepId: "admit-input" } });
  s = step.state;
  check("lifecycle", "execution events apply while the run proceeds",
    s.state === "EXECUTING" && step.effects.includes("applyExecutionEvent"));

  const intermediate = lifecycle.reduce(s, { type: "outcome", terminal: false, active: true });
  check("lifecycle", "an intermediate outcome updates its node without stealing focus",
    intermediate.state.state === "EXECUTING"
    && intermediate.effects.includes("showResultIndicator")
    && !intermediate.effects.includes("focusOutcome"), intermediate.effects);

  const otherRun = lifecycle.reduce(s, { type: "outcome", terminal: true, active: false,
                                         outcome: { contractId: "x" } });
  check("lifecycle", "another run's terminal outcome shows an indicator, not a dialog",
    otherRun.state.state === "EXECUTING" && !otherRun.effects.includes("focusOutcome")
    && otherRun.effects.includes("showResultIndicator"), otherRun.effects);

  step = lifecycle.reduce(s, { type: "outcome", terminal: true, active: true,
                               outcome: { contractId: "equity-market-price-evidence.v1" } });
  s = step.state;
  check("lifecycle", "the active terminal outcome expands from the outcome anchor",
    s.state === "OUTCOME" && s.resultIndicator === true
    && step.effects.includes("expandOutcomeFromAnchor")
    && step.effects.includes("markOutcomeOccurrence"), step.effects);

  step = lifecycle.reduce(s, { type: "dismiss" });
  s = step.state;
  check("lifecycle", "dismissing collapses, restores focus and keeps the result indicator",
    s.state === "CLOSED" && s.resultIndicator === true
    && step.effects.includes("keepResultIndicator")
    && step.effects.includes("restoreFocus"), step.effects);
  check("lifecycle", "dismissal does not erase the retained outcome",
    s.outcome !== null);

  step = lifecycle.reduce(s, { type: "reopen" });
  check("lifecycle", "a retained result can be reopened from its node",
    step.state.state === "OUTCOME" && step.effects.includes("expandOutcomeFromAnchor"));
}

/* An immediate outcome: admission returns with the result already present. */
{
  let s = lifecycle.initial();
  s = lifecycle.reduce(s, { type: "open" }).state;
  s = lifecycle.reduce(s, { type: "submit", findings: [], requestId: "req-fast" }).state;
  const step = lifecycle.reduce(s, { type: "admitted", runId: "run-fast",
                                     outcome: { contractId: "hello-world-greeting.v1" } });
  check("lifecycle", "a run that finishes immediately sequences without inventing delay",
    step.state.state === "OUTCOME"
    && step.effects.indexOf("collapseToInputAnchor")
       < step.effects.indexOf("expandOutcomeFromAnchor"), step.effects);
  check("lifecycle", "an immediate outcome is retained like any other",
    step.state.outcome !== null && step.state.resultIndicator === true);
}

/* Refusal and uncertainty. */
{
  let s = lifecycle.initial();
  s = lifecycle.reduce(s, { type: "open" }).state;
  s = lifecycle.reduce(s, { type: "edit", pointer: "/payload/name", value: "Ada" }).state;
  const submitted = lifecycle.reduce(s, { type: "submit", findings: [], requestId: "r" }).state;

  const refused = lifecycle.reduce(submitted, {
    type: "refused", refusal: { code: "PROVIDER_UNAVAILABLE", message: "unavailable" } });
  check("lifecycle", "a refusal preserves the draft and shows why",
    refused.state.state === "REFUSED"
    && refused.state.draft["/payload/name"] === "Ada"
    && refused.effects.includes("preserveDraft"), refused.effects);

  const acknowledged = lifecycle.reduce(refused.state, { type: "acknowledge" });
  check("lifecycle", "acknowledging a refusal returns to editing without resubmitting",
    acknowledged.state.state === "EDITING"
    && acknowledged.state.invokedCount === 1
    && acknowledged.state.draft["/payload/name"] === "Ada");

  const uncertain = lifecycle.reduce(submitted, { type: "uncertain" });
  check("lifecycle", "an uncertain delivery is retained for reconciliation",
    uncertain.state.state === "UNCERTAIN"
    && uncertain.effects.includes("retainForReconciliation")
    && uncertain.state.draft["/payload/name"] === "Ada", uncertain.effects);
  check("lifecycle", "an uncertain delivery does not resubmit",
    uncertain.state.invokedCount === 1);
}

/* Changing input invalidates a displayed result. */
{
  let s = lifecycle.initial();
  s = lifecycle.reduce(s, { type: "open" }).state;
  s = lifecycle.reduce(s, { type: "submit", findings: [], requestId: "r" }).state;
  s = lifecycle.reduce(s, { type: "admitted", runId: "run",
                            outcome: { contractId: "x" } }).state;
  s = lifecycle.reduce(s, { type: "open" }).state;
  const edited = lifecycle.reduce(s, { type: "edit", pointer: "/payload/name", value: "Grace" });
  check("lifecycle", "changing input clears the displayed result but keeps the indicator",
    edited.state.outcome === null && edited.state.resultIndicator === true);
}

/* Out-of-order events are refused rather than silently absorbed. */
{
  const s = lifecycle.initial();
  const stray = lifecycle.reduce(s, { type: "outcome", terminal: true, active: true });
  check("lifecycle", "an outcome with no admitted run is refused",
    stray.state.state === "CLOSED" && stray.refused !== null);
  const strayDismiss = lifecycle.reduce(s, { type: "dismiss" });
  check("lifecycle", "dismissing when nothing is open is refused",
    strayDismiss.refused !== null);
  const unknown = lifecycle.reduce(s, { type: "wat" });
  check("lifecycle", "an unknown event is refused, not ignored",
    unknown.refused !== null && unknown.state.state === "CLOSED");
}

/* ------------------------------------------------------------------- focus */

{
  const controls = [
    { id: "field-symbol" },
    { id: "field-region" },
    { id: "dialog-cancel" },
    { id: "dialog-submit" }
  ];

  check("focus", "opening focuses the first suitable control",
    focus.initialFocus(controls, "dialog") === "field-symbol");

  check("focus", "an explicit autofocus wins over document order",
    focus.initialFocus([{ id: "a" }, { id: "b", autofocus: true }], "dialog") === "b");

  check("focus", "a disabled first control is skipped",
    focus.initialFocus([{ id: "a", disabled: true }, { id: "b" }], "dialog") === "b");

  check("focus", "a hidden control is skipped",
    focus.initialFocus([{ id: "a", hidden: true }, { id: "b" }], "dialog") === "b");

  check("focus", "with nothing focusable, focus goes to the dialog, not the page",
    focus.initialFocus([{ id: "a", disabled: true }], "dialog") === "dialog");

  check("focus", "Tab moves forward",
    focus.nextFocus(controls, "field-symbol", false) === "field-region");
  check("focus", "Tab wraps from the last control to the first",
    focus.nextFocus(controls, "dialog-submit", false) === "field-symbol");
  check("focus", "Shift+Tab wraps from the first control to the last",
    focus.nextFocus(controls, "field-symbol", true) === "dialog-submit");
  check("focus", "a single focusable control wraps to itself rather than escaping",
    focus.nextFocus([{ id: "only" }], "only", false) === "only"
    && focus.nextFocus([{ id: "only" }], "only", true) === "only");
  check("focus", "focus arriving from outside the trap is pulled to an end",
    focus.nextFocus(controls, "somewhere-else", false) === "field-symbol"
    && focus.nextFocus(controls, "somewhere-else", true) === "dialog-submit");
  check("focus", "with nothing focusable, containment still returns the dialog",
    focus.nextFocus([], "x", false, "dialog") === "dialog");

  check("focus", "after an invalid submission focus goes to the first invalid control",
    focus.firstInvalidFocus(controls,
      [{ componentId: "field-region", message: "required" }], "dialog") === "field-region");
  check("focus", "an invalid flag on the descriptor is honoured too",
    focus.firstInvalidFocus([{ id: "a" }, { id: "b", invalid: true }], [], "dialog") === "b");

  check("focus", "containment recognises the dialog container itself",
    focus.contains(controls, "dialog", "dialog") === true);
  check("focus", "containment rejects a node outside the overlay",
    focus.contains(controls, "page-header-link", "dialog") === false);

  check("focus", "focus returns to the opener when it is still focusable",
    focus.returnFocus("node-button", [{ id: "node-button" }]) === "node-button");
  check("focus", "a vanished opener yields no return target, so the provider must choose",
    focus.returnFocus("node-button", [{ id: "something-else" }]) === null);

  check("focus", "Escape is permitted while editing and while showing an outcome",
    focus.dismissible("EDITING") && focus.dismissible("OUTCOME"));
  check("focus", "Escape is refused while a submission is in flight",
    focus.dismissible("SUBMITTING") === false);
}

/* ---------------------------------------------------------------- receipt */

const receiptDir = path.join(WORKBENCH, "evidence", "invoke");
fs.mkdirSync(receiptDir, { recursive: true });
fs.writeFileSync(path.join(receiptDir, "overlay-provider.receipt.json"), JSON.stringify({
  receiptType: "overlay-provider-receipt.v1",
  verifiedAt: new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00"),
  statement: "The overlay provider's decisions — anchor resolution against a real scene, "
    + "placement and clipping avoidance, motion intent under reduced motion, the input-to-"
    + "outcome lifecycle, and focus containment — are exercised as pure logic.",
  modules: [
    "runtime/overlay-anchor.js",
    "runtime/dialog-lifecycle.js",
    "runtime/focus-containment.js"
  ],
  node: process.version,
  summary: {
    cases: results.length,
    passed: results.filter((r) => r.passed).length,
    failed: results.filter((r) => !r.passed).length,
    groups: [...new Set(results.map((r) => r.group))]
  },
  results,
  notEstablished: [
    "No browser or DOM implementation is available here, so nothing below the decision "
      + "layer is executed: whether focus actually moved, whether an animation actually ran, "
      + "and whether the overlay was actually painted are all unverified.",
    "The provider's DOM layer in runtime/overlay-provider.js is unexercised.",
    "Anchor recomputation is proven to depend on camera state; it has not been observed "
      + "reacting to a real zoom, pan or resize.",
    "No capability was invoked, and no dialog has been opened by a person."
  ]
}, null, 2) + "\n", "utf8");

console.log(`overlay        ${results.filter((r) => r.passed).length}/${results.length} cases passed`);
console.log(`receipt        ${path.join(receiptDir, "overlay-provider.receipt.json")}`);
process.exit(failures === 0 ? 0 : 1);
