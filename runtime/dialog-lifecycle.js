/* The input-to-outcome dialog lifecycle, as a state machine that can be checked.
 *
 * This is the plan's transition table expressed as data and a reducer, so the
 * rules that matter can be exercised without a browser, a server or a run:
 *
 *   * an invalid submission keeps the dialog open, preserves the draft and
 *     submits nothing
 *   * a submission in flight cannot be submitted again — and disabling a button
 *     is not what enforces that; this is
 *   * cancelling before Submit invokes nothing at all
 *   * a refusal preserves the draft; an uncertain delivery stays recoverable and
 *     is never resubmitted automatically
 *   * a very fast run may already have its outcome when admission returns, so
 *     the transitions must sequence without inventing execution time
 *   * dismissing an outcome does not erase evidence; a result indicator remains
 *     and the same outcome can be reopened
 *
 * The reducer returns the next state and the *effects* a provider should carry
 * out. It performs none of them. In particular it never writes domain state:
 * animation and focus are consequences of a transition, not participants in it.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) { module.exports = api; }
  else { root.SFX_DIALOG_LIFECYCLE = api; }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var STATES = ["CLOSED", "EDITING", "SUBMITTING", "ADMITTED", "EXECUTING",
                "OUTCOME", "REFUSED", "UNCERTAIN"];

  function initial() {
    return {
      state: "CLOSED",
      draft: {},
      fieldFindings: [],
      requestId: null,
      runId: null,
      outcome: null,
      resultIndicator: false,
      invokedCount: 0,
      anchorIdentity: null,
      returnFocusTo: null,
      history: []
    };
  }

  function clone(state) {
    return {
      state: state.state,
      draft: Object.assign({}, state.draft),
      fieldFindings: state.fieldFindings.slice(),
      requestId: state.requestId,
      runId: state.runId,
      outcome: state.outcome,
      resultIndicator: state.resultIndicator,
      invokedCount: state.invokedCount,
      anchorIdentity: state.anchorIdentity,
      returnFocusTo: state.returnFocusTo,
      history: state.history.slice()
    };
  }

  function result(next, effects, refusedReason) {
    var outcome = { state: next, effects: effects || [] };
    if (refusedReason) { outcome.refused = refusedReason; }
    return outcome;
  }

  /* Every transition the lifecycle admits. A pair not listed here is refused,
   * which is how an out-of-order event becomes visible instead of silently
   * corrupting the dialog's idea of what is happening. */
  function transition(current, event) {
    var from = current.state;

    if (event.type === "open") {
      if (from !== "CLOSED" && from !== "OUTCOME") {
        return result(from, [], "a dialog is already open");
      }
      return result("EDITING", ["resolveAnchor", "expandFromAnchor", "focusFirstControl"]);
    }

    if (event.type === "edit") {
      if (from !== "EDITING") { return result(from, [], "not editing"); }
      /* Changing input invalidates a previously displayed result. */
      return result("EDITING", current.outcome ? ["invalidateDisplayedResult"] : []);
    }

    if (event.type === "cancel") {
      if (from === "SUBMITTING") {
        /* Cancelling a running capability is a separate acknowledged service
         * action, not a dialog dismissal. */
        return result(from, [], "a submitted request cannot be cancelled from the dialog");
      }
      if (from !== "EDITING") { return result(from, [], "nothing to cancel"); }
      return result("CLOSED", ["collapseToAnchor", "restoreFocus"]);
    }

    if (event.type === "submit") {
      if (from === "SUBMITTING") {
        /* This is the duplicate-submission guard. A disabled attribute draws it;
         * this decides it. */
        return result(from, [], "a submission is already in flight");
      }
      if (from !== "EDITING") { return result(from, [], "not editing"); }
      if (event.findings && event.findings.length) {
        return result("EDITING",
          ["preserveDraft", "showFieldFindings", "focusFirstInvalidControl"],
          "input is inadmissible; nothing was submitted");
      }
      return result("SUBMITTING", ["preserveDraft", "preventDuplicateSubmission"]);
    }

    if (event.type === "admitted") {
      if (from !== "SUBMITTING") { return result(from, [], "no submission in flight"); }
      /* A very fast run may already have its outcome. Sequence the visuals
       * without adding fake execution time or losing the result. */
      if (event.outcome) {
        return result("OUTCOME",
          ["associateRun", "collapseToInputAnchor", "markOutcomeOccurrence",
           "expandOutcomeFromAnchor", "focusOutcome", "retainResult"]);
      }
      return result("ADMITTED", ["associateRun", "collapseToInputAnchor", "showRunOnCircuit"]);
    }

    if (event.type === "refused") {
      if (from !== "SUBMITTING") { return result(from, [], "no submission in flight"); }
      return result("REFUSED", ["preserveDraft", "showRefusal", "focusRefusal"]);
    }

    if (event.type === "uncertain") {
      if (from !== "SUBMITTING") { return result(from, [], "no submission in flight"); }
      return result("UNCERTAIN",
        ["preserveDraft", "showUncertainDelivery", "retainForReconciliation"]);
    }

    if (event.type === "acknowledge") {
      /* Returning to editing from a refusal or an uncertain delivery keeps the
       * draft; it does not resubmit. */
      if (from !== "REFUSED" && from !== "UNCERTAIN") {
        return result(from, [], "nothing to acknowledge");
      }
      return result("EDITING", ["focusFirstControl"]);
    }

    if (event.type === "executing") {
      if (from !== "ADMITTED" && from !== "EXECUTING") {
        return result(from, [], "no admitted run");
      }
      return result("EXECUTING", ["applyExecutionEvent"]);
    }

    if (event.type === "outcome") {
      if (from !== "ADMITTED" && from !== "EXECUTING") {
        return result(from, [], "no admitted run");
      }
      if (!event.terminal) {
        /* An intermediate outcome updates its node and becomes inspectable
         * without stealing focus from whatever the user is doing. */
        return result(from, ["updateNodeResult", "showResultIndicator"]);
      }
      if (event.active === false) {
        /* Another run's terminal outcome must not steal focus either. */
        return result(from, ["showResultIndicator", "retainResult"]);
      }
      return result("OUTCOME",
        ["markOutcomeOccurrence", "expandOutcomeFromAnchor", "focusOutcome", "retainResult"]);
    }

    if (event.type === "dismiss") {
      if (from !== "OUTCOME") { return result(from, [], "no outcome is open"); }
      /* Dismissal does not erase evidence. */
      return result("CLOSED",
        ["collapseToOutcomeAnchor", "restoreFocus", "keepResultIndicator"]);
    }

    if (event.type === "reopen") {
      if (!current.resultIndicator) { return result(from, [], "no retained result"); }
      if (from !== "CLOSED") { return result(from, [], "a dialog is already open"); }
      return result("OUTCOME", ["expandOutcomeFromAnchor", "focusOutcome"]);
    }

    return result(from, [], "unknown event " + event.type);
  }

  /* Apply a transition, producing the next lifecycle state.
   *
   * The draft is only ever replaced by an explicit edit. Every other path
   * preserves it, which is what "preserve the draft" has to mean in practice.
   */
  function reduce(current, event) {
    var outcome = transition(current, event);
    var next = clone(current);
    next.state = outcome.state;

    /* A refused transition changes nothing but the history. Without this, a
     * duplicate submit would still increment the invocation count and overwrite
     * the request identity while reporting that it had been refused. */
    if (outcome.refused) {
      /* One refusal still updates state: an inadmissible submission has field
       * findings to show. It advanced nothing and invoked nothing, so the
       * request identity and the invocation count are untouched. */
      if (event.type === "submit" && outcome.state === "EDITING") {
        next.fieldFindings = (event.findings || []).slice();
      }
      next.history = current.history.concat([{
        from: current.state, event: event.type, to: outcome.state,
        refused: outcome.refused
      }]);
      return { state: next, effects: outcome.effects, refused: outcome.refused };
    }

    if (event.type === "open") {
      next.anchorIdentity = event.anchorIdentity || null;
      next.returnFocusTo = event.returnFocusTo || null;
      next.fieldFindings = [];
      if (outcome.state === "EDITING" && current.state === "OUTCOME") {
        next.outcome = null;
      }
    }
    if (event.type === "edit" && outcome.state === "EDITING") {
      next.draft = Object.assign({}, current.draft);
      next.draft[event.pointer] = event.value;
      if (current.outcome) {
        /* A changed input invalidates the displayed result but not the record
         * that a result exists; the indicator survives. */
        next.outcome = null;
      }
    }
    if (event.type === "submit") {
      /* Reaching here means the submission was admitted by the lifecycle, so
       * the draft is clean and exactly one invocation is counted. */
      next.fieldFindings = [];
      next.requestId = event.requestId || null;
      next.invokedCount = current.invokedCount + 1;
    }
    if (event.type === "admitted" && outcome.state !== current.state) {
      next.runId = event.runId || null;
      if (event.outcome) {
        next.outcome = event.outcome;
        next.resultIndicator = true;
      }
    }
    if (event.type === "outcome" && !outcome.refused) {
      if (event.terminal) {
        next.outcome = event.outcome || null;
        next.resultIndicator = true;
      } else {
        next.resultIndicator = true;
      }
    }
    if (event.type === "refused") {
      next.refusal = event.refusal || null;
    }
    if (event.type === "acknowledge" && outcome.state === "EDITING") {
      next.refusal = null;
    }

    next.history = current.history.concat([{
      from: current.state, event: event.type, to: outcome.state,
      refused: outcome.refused || null
    }]);
    return { state: next, effects: outcome.effects, refused: outcome.refused || null };
  }

  return { initial: initial, reduce: reduce, transition: transition, STATES: STATES };
});
