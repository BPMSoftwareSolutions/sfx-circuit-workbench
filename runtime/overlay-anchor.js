/* Overlay anchoring: where a dialog emerges from, and where it may sit.
 *
 * The scene provider owns graph-space geometry and the camera. The dialog
 * provider owns overlay placement, clipping avoidance and animation. This
 * module is the seam between them, and it is deliberately pure: it takes
 * numbers and returns numbers, so the arithmetic can be checked without a
 * browser.
 *
 * Two things it must get right, because both are stated requirements rather
 * than conveniences:
 *
 *   * the visual anchor is **recomputed** after zoom, pan or resize. A dialog
 *     anchored once and left behind is worse than one that never claimed to be
 *     anchored, because it points confidently at the wrong node.
 *   * when the anchoring node is not present in the current view, the dialog
 *     falls back to a **labelled viewport transition while retaining its
 *     semantic identity**. The identity is the authority; the graph entity id
 *     is a projection identifier that a re-layout may change or drop.
 *
 * Placement never changes what a dialog means. It moves a box.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) { module.exports = api; }
  else { root.SFX_OVERLAY_ANCHOR = api; }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var DEFAULT_MARGIN = 16;
  var DEFAULT_OFFSET = 12;

  /* Where a node's centre currently sits in viewport coordinates.
   *
   * `stageOrigin` is the viewport position of graph-space (0, 0) as the scene is
   * presently drawn — the DOM layer measures it rather than reconstructing it
   * from scroll offsets and padding, because the stage centres small content and
   * that offset is easier to observe than to predict. */
  function graphPointToViewport(box, scale, stageOrigin) {
    if (!box || !stageOrigin || !(scale > 0)) { return null; }
    return {
      x: stageOrigin.x + (box[0] + box[2] / 2) * scale,
      y: stageOrigin.y + (box[1] + box[3] / 2) * scale,
      width: box[2] * scale,
      height: box[3] * scale
    };
  }

  /* Resolve a declared anchor against the scene actually loaded.
   *
   * Returns a resolution that always carries the semantic identity, whether or
   * not a node was found, so a caller can never accidentally lose it by taking
   * the unavailable branch. */
  function resolveAnchor(anchor, scene, camera) {
    var identity = anchor && anchor.nodeIdentity;
    var base = {
      kind: anchor ? anchor.kind : "viewport",
      nodeIdentity: identity || null,
      strategy: "viewport-transition",
      point: null,
      entityId: null,
      available: false,
      reason: null
    };

    if (!anchor || anchor.kind !== "scene-node") {
      base.reason = "the declaration anchors to the viewport";
      return base;
    }
    if (!scene) {
      base.reason = "no scene is loaded";
      return base;
    }
    if (anchor.sceneId && scene.sceneId !== anchor.sceneId) {
      base.reason = "the anchor names a different scene";
      return base;
    }

    var node = null;
    for (var i = 0; i < scene.graph.nodes.length; i += 1) {
      if (scene.graph.nodes[i].identity === identity) { node = scene.graph.nodes[i]; break; }
    }
    if (!node) {
      /* The node is not in this view. The dialog still opens, still carries its
       * identity, and says why it is not pointing at anything. */
      base.reason = "no node in this view carries that identity";
      return base;
    }

    var point = graphPointToViewport(scene.geometry.boxes[node.id], camera.scale,
      camera.stageOrigin);
    if (!point) {
      base.entityId = node.id;
      base.reason = "the node has no resolved geometry";
      return base;
    }

    base.entityId = node.id;
    base.point = point;
    base.available = true;
    base.strategy = "expand-from-anchor";
    base.reason = null;
    return base;
  }

  /* Place an overlay near its anchor without letting it leave the viewport.
   *
   * Preference order is below, above, right, left; whichever fits first wins.
   * If none fits the overlay is centred and reported as `clamped`, because a
   * dialog half off-screen is not a placement, it is a defect. */
  function placeOverlay(anchorPoint, size, viewport, options) {
    var settings = options || {};
    var margin = settings.margin === undefined ? DEFAULT_MARGIN : settings.margin;
    var offset = settings.offset === undefined ? DEFAULT_OFFSET : settings.offset;

    var minX = viewport.x + margin;
    var minY = viewport.y + margin;
    var maxX = viewport.x + viewport.width - size.width - margin;
    var maxY = viewport.y + viewport.height - size.height - margin;

    /* An overlay larger than its viewport cannot be placed politely; say so
     * rather than producing a negative-width box. */
    if (maxX < minX || maxY < minY) {
      return {
        x: Math.max(viewport.x + margin, viewport.x + (viewport.width - size.width) / 2),
        y: Math.max(viewport.y + margin, viewport.y + (viewport.height - size.height) / 2),
        side: "centre",
        clamped: true,
        fits: false,
        reason: "the overlay is larger than the available viewport"
      };
    }

    if (!anchorPoint) {
      return {
        x: viewport.x + (viewport.width - size.width) / 2,
        y: viewport.y + (viewport.height - size.height) / 2,
        side: "centre",
        clamped: false,
        fits: true,
        reason: "no anchor point; centred"
      };
    }

    var halfW = (anchorPoint.width || 0) / 2;
    var halfH = (anchorPoint.height || 0) / 2;
    var candidates = [
      { side: "below", x: anchorPoint.x - size.width / 2, y: anchorPoint.y + halfH + offset },
      { side: "above", x: anchorPoint.x - size.width / 2,
        y: anchorPoint.y - halfH - offset - size.height },
      { side: "right", x: anchorPoint.x + halfW + offset, y: anchorPoint.y - size.height / 2 },
      { side: "left", x: anchorPoint.x - halfW - offset - size.width,
        y: anchorPoint.y - size.height / 2 }
    ];

    for (var i = 0; i < candidates.length; i += 1) {
      var candidate = candidates[i];
      var withinX = candidate.x >= minX && candidate.x <= maxX;
      var withinY = candidate.y >= minY && candidate.y <= maxY;
      if (withinX && withinY) {
        return { x: candidate.x, y: candidate.y, side: candidate.side,
                 clamped: false, fits: true, reason: null };
      }
    }

    /* Nothing fit cleanly. Keep the preferred side's intent, but clamp into the
     * viewport and admit that it was clamped. */
    var preferred = candidates[0];
    return {
      x: Math.min(Math.max(preferred.x, minX), maxX),
      y: Math.min(Math.max(preferred.y, minY), maxY),
      side: preferred.side,
      clamped: true,
      fits: true,
      reason: "no side fitted without clamping"
    };
  }

  /* The transform origin an expand-from-anchor animation should grow out of,
   * expressed relative to the placed overlay box. This is what makes the dialog
   * appear to come out of the node rather than out of its own centre. */
  function transformOrigin(anchorPoint, placement, size) {
    if (!anchorPoint) { return { x: size.width / 2, y: size.height / 2, relative: "centre" }; }
    return {
      x: Math.min(Math.max(anchorPoint.x - placement.x, 0), size.width),
      y: Math.min(Math.max(anchorPoint.y - placement.y, 0), size.height),
      relative: "anchor"
    };
  }

  /* Resolve the motion a transition should use. Reduced motion is honoured by
   * substituting the declared fallback, never by ignoring the preference. */
  function resolveMotion(declared, phase, reducedMotion) {
    var intent = (declared && declared[phase]) || (phase === "open"
      ? "expand-from-anchor" : "collapse-to-anchor");
    if (reducedMotion) {
      var fallback = (declared && declared.reducedMotionFallback) || "none";
      return { intent: fallback, substituted: true, declaredIntent: intent };
    }
    return { intent: intent, substituted: false, declaredIntent: intent };
  }

  return {
    graphPointToViewport: graphPointToViewport,
    resolveAnchor: resolveAnchor,
    placeOverlay: placeOverlay,
    transformOrigin: transformOrigin,
    resolveMotion: resolveMotion,
    DEFAULT_MARGIN: DEFAULT_MARGIN,
    DEFAULT_OFFSET: DEFAULT_OFFSET
  };
});
