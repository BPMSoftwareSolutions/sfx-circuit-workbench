/* The modal overlay provider: the DOM half of a node-anchored dialog.
 *
 * Everything that can be decided from numbers was decided elsewhere —
 * overlay-anchor.js places it, dialog-lifecycle.js sequences it, and
 * focus-containment.js decides where focus goes. This file only carries those
 * decisions out, which is why it is short and why the interesting rules are
 * testable without a browser.
 *
 * What it owns:
 *
 *   * an accessible dialog: role, modal semantics, a title and description
 *     referenced by id, and a labelled fallback when the anchor is unavailable
 *   * focus containment, and focus return to whatever opened the dialog
 *   * Escape, but only where the lifecycle says dismissal is permitted — a
 *     submission in flight is not dismissible, because the request has left
 *   * expand-from-anchor and collapse-to-anchor, with the origin taken from the
 *     anchor so the dialog appears to come out of the node
 *   * recomputing the anchor after zoom, pan or resize
 *
 * What it must never do, and does not:
 *
 *   * write domain state from an animation callback. The animation animates a
 *     shell. Field identity, draft values and run association are set by the
 *     lifecycle, before any transition is drawn.
 *   * schedule execution. Motion never causes a request, and a request never
 *     waits for motion to finish.
 */
(function (root, factory) {
  "use strict";
  var api = factory(
    typeof require === "function" ? require("./overlay-anchor.js") : root.SFX_OVERLAY_ANCHOR,
    typeof require === "function" ? require("./dialog-lifecycle.js") : root.SFX_DIALOG_LIFECYCLE,
    typeof require === "function" ? require("./focus-containment.js") : root.SFX_FOCUS_CONTAINMENT
  );
  if (typeof module === "object" && module.exports) { module.exports = api; }
  else { root.SFX_OVERLAY_PROVIDER = api; }
})(typeof self !== "undefined" ? self : this, function (anchoring, lifecycleModule, focusModule) {
  "use strict";

  var FOCUSABLE = "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])";

  function create(options) {
    var host = options.host || document.body;
    var tokens = options.tokens || {};
    var provider = {
      lifecycle: options.initialLifecycle || lifecycleModule.initial(),
      element: null,
      titleId: null,
      descriptionId: null,
      opener: null,
      anchor: null,
      declaredMotion: options.motion || {},
      findings: []
    };

    function reducedMotion() {
      return Boolean(window.matchMedia
        && window.matchMedia("(prefers-reduced-motion:reduce)").matches);
    }

    function descriptors() {
      if (!provider.element) { return []; }
      return Array.prototype.map.call(
        provider.element.querySelectorAll(FOCUSABLE),
        function (node) {
          return {
            id: node.id || node.getAttribute("data-component-id"),
            node: node,
            disabled: Boolean(node.disabled),
            hidden: node.hidden || node.offsetParent === null,
            autofocus: node.hasAttribute("data-autofocus"),
            invalid: node.getAttribute("aria-invalid") === "true"
          };
        });
    }

    function focusById(id) {
      if (!id) { return; }
      var found = descriptors().filter(function (d) { return d.id === id; })[0];
      if (found) { found.node.focus(); return; }
      if (provider.element && id === provider.element.id) { provider.element.focus(); }
    }

    /* Placement is recomputed rather than remembered, so zoom, pan and resize
     * all reach the same code path as opening does. */
    function reposition() {
      if (!provider.element || !options.camera) { return; }
      var camera = options.camera();
      provider.anchor = anchoring.resolveAnchor(options.anchor, options.scene && options.scene(),
        camera);

      var viewport = { x: 0, y: 0, width: window.innerWidth, height: window.innerHeight };
      var size = { width: provider.element.offsetWidth, height: provider.element.offsetHeight };
      var placement = anchoring.placeOverlay(provider.anchor.point, size, viewport);
      var origin = anchoring.transformOrigin(provider.anchor.point, placement, size);

      provider.element.style.left = placement.x + "px";
      provider.element.style.top = placement.y + "px";
      provider.element.style.transformOrigin = origin.x + "px " + origin.y + "px";
      provider.element.setAttribute("data-placement", placement.side);
      provider.element.setAttribute("data-anchor-strategy", provider.anchor.strategy);
      if (provider.anchor.nodeIdentity) {
        provider.element.setAttribute("data-anchor-identity", provider.anchor.nodeIdentity);
      }

      /* When the anchor cannot be resolved the dialog says so, in words, rather
       * than pointing confidently at nothing. */
      var note = provider.element.querySelector("[data-anchor-note]");
      if (note) {
        note.textContent = provider.anchor.available ? ""
          : "This capability's node is not in the current view — " + provider.anchor.reason + ".";
        note.hidden = provider.anchor.available;
      }
    }

    function animate(phase) {
      if (!provider.element) { return Promise.resolve(); }
      var motion = anchoring.resolveMotion(provider.declaredMotion, phase, reducedMotion());
      provider.element.setAttribute("data-motion", motion.intent);
      var element = provider.element;
      if (motion.intent === "none") {
        element.style.opacity = phase === "open" ? "1" : "0";
        element.style.transform = "none";
        return Promise.resolve();
      }

      var duration = Number(tokens.motionDurationMs || 160);
      provider.element.style.transition =
        "transform " + duration + "ms " + (tokens.motionEasing || "ease-out")
        + ", opacity " + duration + "ms " + (tokens.motionEasing || "ease-out");
      if (phase === "open") {
        element.style.transform = "scale(0.08)";
        element.getBoundingClientRect();
        requestAnimationFrame(function () { element.style.transform = "scale(1)"; element.style.opacity = "1"; });
      } else { element.style.transform = "scale(0.08)"; element.style.opacity = "0"; }

      /* The animation resolves on its own timer. Nothing downstream of it writes
       * state; a caller may ignore this promise entirely and lose nothing. */
      return new Promise(function (resolve) { setTimeout(resolve, duration); });
    }

    function onKeyDown(event) {
      if (event.key === "Escape") {
        if (!focusModule.dismissible(provider.lifecycle.state)) { return; }
        event.preventDefault();
        dispatch({ type: provider.lifecycle.state === "OUTCOME" ? "dismiss" : "cancel" });
        return;
      }
      if (event.key !== "Tab") { return; }
      var candidates = descriptors();
      var currentId = document.activeElement
        && (document.activeElement.id
            || document.activeElement.getAttribute("data-component-id"));
      var target = focusModule.nextFocus(candidates, currentId, event.shiftKey,
        provider.element && provider.element.id);
      event.preventDefault();
      focusById(target);
    }

    function mount(content, titles) {
      var element = document.createElement("div");
      element.className = "sfx-overlay";
      element.id = "sfx-overlay-" + (titles.dialogId || "dialog");
      element.setAttribute("role", "dialog");
      element.setAttribute("aria-modal", "true");
      element.tabIndex = -1;

      provider.titleId = element.id + "-title";
      provider.descriptionId = element.id + "-description";
      element.setAttribute("aria-labelledby", provider.titleId);
      if (titles.description) {
        element.setAttribute("aria-describedby", provider.descriptionId);
      }

      element.appendChild(content);
      var title = content.querySelector("[data-component-id='dialog-title'], "
        + "[data-component-id='outcome-title']");
      if (title) { title.id = provider.titleId; }
      var description = content.querySelector("[data-component-id='dialog-description']");
      if (description) { description.id = provider.descriptionId; }

      var note = document.createElement("p");
      note.className = "sfx-overlay-anchor-note";
      note.setAttribute("data-anchor-note", "true");
      note.hidden = true;
      element.appendChild(note);

      element.style.position = "fixed";
      element.style.opacity = "0";
      element.style.transform = "scale(0.92)";
      host.appendChild(element);
      provider.element = element;
      element.addEventListener("keydown", onKeyDown);
      return element;
    }

    function unmount(target) {
      var element = target || provider.element;
      if (!element) { return; }
      element.removeEventListener("keydown", onKeyDown);
      if (element.parentNode) {
        element.parentNode.removeChild(element);
      }
      if (element === provider.element) provider.element = null;
    }

    function closeCurrent() {
      var element = provider.element;
      if (!element) return Promise.resolve();
      return animate("close").then(function () { unmount(element); });
    }

    /* Effects the lifecycle asks for, carried out here and nowhere else. */
    var EFFECTS = {
      resolveAnchor: reposition,
      expandFromAnchor: function () { reposition(); animate("open"); },
      expandOutcomeFromAnchor: function () { reposition(); animate("open"); },
      collapseToAnchor: closeCurrent,
      collapseToInputAnchor: closeCurrent,
      collapseToOutcomeAnchor: closeCurrent,
      focusFirstControl: function () {
        focusById(focusModule.initialFocus(descriptors(), provider.element
          && provider.element.id));
      },
      focusFirstInvalidControl: function () {
        focusById(focusModule.firstInvalidFocus(descriptors(), provider.findings,
          provider.element && provider.element.id));
      },
      focusOutcome: function () {
        // A long outcome must open at its heading, rather than scrolling to
        // its footer merely because Close is the first interactive control.
        if (provider.element) {
          provider.element.scrollTop = 0;
          provider.element.focus({ preventScroll: true });
        }
      },
      focusRefusal: function () {
        focusById(focusModule.initialFocus(descriptors(), provider.element
          && provider.element.id));
      },
      restoreFocus: function () {
        var documentCandidates = Array.prototype.map.call(
          document.querySelectorAll(FOCUSABLE),
          function (node) {
            return { id: node.id || node.getAttribute("data-component-id"), node: node,
                     disabled: Boolean(node.disabled), hidden: node.hidden };
          });
        var target = focusModule.returnFocus(provider.opener, documentCandidates);
        if (target) {
          documentCandidates.filter(function (d) { return d.id === target; })[0].node.focus();
        } else if (options.onFocusLost) {
          /* The opener is gone. The provider must choose somewhere sensible
           * rather than leaving focus on the document body. */
          options.onFocusLost();
        }
      }
    };

    function dispatch(event) {
      var step = lifecycleModule.reduce(provider.lifecycle, event);
      provider.lifecycle = step.state;
      if (event.type === "submit") { provider.findings = event.findings || []; }

      // The host must release inertness before focus returns to the circuit.
      if (options.onTransition) { options.onTransition(step, event); }

      step.effects.forEach(function (name) {
        var effect = EFFECTS[name];
        if (effect) { effect(); }
        else if (options.onEffect) { options.onEffect(name, step.state); }
      });

      return step;
    }

    return {
      mount: mount,
      unmount: unmount,
      dispatch: dispatch,
      reposition: reposition,
      state: function () { return provider.lifecycle; },
      anchor: function () { return provider.anchor; },
      setOpener: function (id) { provider.opener = id; },
      element: function () { return provider.element; }
    };
  }

  return { create: create };
});
