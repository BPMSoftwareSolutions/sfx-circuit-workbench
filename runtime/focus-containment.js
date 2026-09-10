/* Focus containment for a modal overlay, as arithmetic rather than as DOM.
 *
 * A focus trap is one of those things that looks trivial and then quietly fails
 * on the cases nobody tried: an overlay with a single focusable control, one
 * with none at all, a disabled or hidden control that should be skipped, and
 * Shift+Tab from the first element. Those are the cases that matter to somebody
 * navigating by keyboard, so the decision is made here — where it can be
 * exercised — and the DOM layer only carries it out.
 *
 * The provider supplies an ordered list of descriptors:
 *
 *     { id, disabled, hidden, autofocus, invalid }
 *
 * and gets back an id to focus. It never gets back "do nothing" when a
 * containment decision was required, because falling through to the browser's
 * default is how focus escapes a modal.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) { module.exports = api; }
  else { root.SFX_FOCUS_CONTAINMENT = api; }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function focusable(candidates) {
    return (candidates || []).filter(function (item) {
      return item && !item.disabled && !item.hidden;
    });
  }

  /* The control to focus when the dialog opens: an explicit autofocus, else the
   * first suitable control, else the dialog itself so focus is inside the modal
   * rather than left behind on whatever opened it. */
  function initialFocus(candidates, dialogId) {
    var available = focusable(candidates);
    for (var i = 0; i < available.length; i += 1) {
      if (available[i].autofocus) { return available[i].id; }
    }
    return available.length ? available[0].id : (dialogId || null);
  }

  /* After an invalid submission, focus goes to the first invalid control — not
   * to the first control, and not to the error summary. */
  function firstInvalidFocus(candidates, findings, dialogId) {
    var flagged = {};
    (findings || []).forEach(function (finding) {
      if (finding && finding.componentId) { flagged[finding.componentId] = true; }
    });
    var available = focusable(candidates);
    for (var i = 0; i < available.length; i += 1) {
      if (available[i].invalid || flagged[available[i].id]) { return available[i].id; }
    }
    return available.length ? available[0].id : (dialogId || null);
  }

  /* Where Tab or Shift+Tab should land, wrapping at both ends.
   *
   * A single focusable control wraps to itself: the correct behaviour is that
   * focus stays put, not that it leaves the modal. */
  function nextFocus(candidates, currentId, backwards, dialogId) {
    var available = focusable(candidates);
    if (!available.length) { return dialogId || null; }
    var index = -1;
    for (var i = 0; i < available.length; i += 1) {
      if (available[i].id === currentId) { index = i; break; }
    }
    if (index === -1) {
      /* Focus was somewhere the trap does not know about — outside the overlay,
       * or on the dialog container. Pull it back to an end. */
      return backwards ? available[available.length - 1].id : available[0].id;
    }
    var target = backwards ? index - 1 : index + 1;
    if (target < 0) { target = available.length - 1; }
    if (target >= available.length) { target = 0; }
    return available[target].id;
  }

  /* Whether focus currently sits inside the overlay. A provider polls this after
   * an update, because a re-render can drop the focused node. */
  function contains(candidates, currentId, dialogId) {
    if (currentId && dialogId && currentId === dialogId) { return true; }
    return focusable(candidates).some(function (item) { return item.id === currentId; });
  }

  /* Where focus returns when the overlay closes: whatever opened it, if that is
   * still focusable, else nothing — and the provider must then choose a sensible
   * landing place rather than leaving focus on the document body. */
  function returnFocus(opener, documentCandidates) {
    if (!opener) { return null; }
    var available = focusable(documentCandidates || []);
    for (var i = 0; i < available.length; i += 1) {
      if (available[i].id === opener) { return opener; }
    }
    return null;
  }

  /* Escape closes a dialog only where dismissal is permitted. A submission in
   * flight is not dismissible: the request has already left. */
  function dismissible(lifecycleState) {
    return lifecycleState === "EDITING" || lifecycleState === "OUTCOME"
      || lifecycleState === "REFUSED" || lifecycleState === "UNCERTAIN";
  }

  return {
    focusable: focusable,
    initialFocus: initialFocus,
    firstInvalidFocus: firstInvalidFocus,
    nextFocus: nextFocus,
    contains: contains,
    returnFocus: returnFocus,
    dismissible: dismissible
  };
});
