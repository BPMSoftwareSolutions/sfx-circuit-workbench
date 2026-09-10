/* Illustrative trace planning over a circuit-scene.v1 scene.
 *
 * A finite inspection of every declared route, including all branch
 * alternatives. Each route is visited once; cycles are illustrated once, never
 * executed. This observes no branch decision, runs no provider and establishes
 * no execution evidence.
 *
 * It is a separate module so that its output can be compared, route for route
 * and wave for wave, against the reference implementation it replaces. That
 * comparison is the only behavioural parity evidence available without a
 * browser, so the planner is kept reachable from Node deliberately.
 *
 * The one difference from the reference is the input shape: this reads a
 * circuit-scene.v1 scene, where a provider-binding route is marked by its
 * `traversable` flag rather than by matching a route kind string. The rule is
 * the same rule; the scene contract states it as data instead of a literal.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) { module.exports = api; }
  else { root.SFX_ILLUSTRATIVE_TRACE = api; }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function planTrace(scene, preferred) {
    var visited = {}, reached = {}, waves = [];
    var routes = scene.graph.routes;
    var flow = routes.filter(function (r) { return r.traversable !== false; });
    var bindings = routes.filter(function (r) { return r.traversable === false; });
    var viewKind = scene.identities && scene.identities.viewKind;

    var byPort = {};
    bindings.forEach(function (edge) {
      (byPort[edge.target] = byPort[edge.target] || []).push(edge);
    });

    function portBindings(ids) {
      var seen = {}, out = [];
      ids.forEach(function (id) {
        if (seen[id]) { return; }
        seen[id] = true;
        (byPort[id] || []).forEach(function (edge) {
          if (!visited[edge.id]) { out.push(edge); }
        });
      });
      return out;
    }

    function appendWave(edges) {
      if (!edges.length) { return; }
      edges.forEach(function (edge) {
        visited[edge.id] = true;
        reached[edge.source] = true;
        reached[edge.target] = true;
      });
      waves.push(edges.map(function (edge) {
        return { edgeId: edge.id, source: edge.source, target: edge.target, kind: edge.kind };
      }));
    }

    var nodes = {}, out = {}, required = {}, targets = {};
    scene.graph.nodes.forEach(function (n) { nodes[n.id] = n; });
    flow.forEach(function (edge) {
      (out[edge.source] = out[edge.source] || []).push(edge);
      targets[edge.target] = true;
      var isRequirement = edge.kind === "CONVERGENCE_REQUIREMENT"
        || (viewKind === "expression"
            && ["argument-dependency", "conditional-argument"].indexOf(edge.kind) !== -1);
      if (isRequirement) {
        (required[edge.target] = required[edge.target] || []).push(edge);
      }
    });

    function outgoing(id) {
      return (out[id] || []).filter(function (edge) { return !visited[edge.id]; });
    }

    /* A convergence continues only once its declared requirements are visited.
     * Outside an expression view, nothing else waits. */
    function eligible(id) {
      var node = nodes[id];
      var requirements = required[id] || [];
      if ((!node || node.kind !== "convergence") && viewKind !== "expression") { return true; }
      return requirements.every(function (edge) { return visited[edge.id]; });
    }

    function activate(id, next) {
      if (!eligible(id)) { return; }
      outgoing(id).forEach(function (edge) {
        var already = next.some(function (e) { return e.id === edge.id; });
        if (!already) { next.push(edge); }
      });
    }

    var roots = scene.graph.nodes.filter(function (n) { return !targets[n.id]; });
    var start = preferred
      || (roots.filter(function (n) { return outgoing(n.id).length; })[0] || {}).id
      || (scene.graph.nodes[0] || {}).id;

    var frontier = [];
    if (preferred && start) { activate(start, frontier); }
    else { roots.forEach(function (rootNode) { activate(rootNode.id, frontier); }); }

    var remaining = flow.length;
    while (remaining > 0) {
      if (!frontier.length) {
        var next = flow.filter(function (e) {
            return !visited[e.id] && eligible(e.source) && reached[e.source]; })[0]
          || flow.filter(function (e) { return !visited[e.id] && eligible(e.source); })[0]
          || flow.filter(function (e) { return !visited[e.id]; })[0];
        if (!next) { break; }
        activate(next.source, frontier);
        if (!frontier.length) { frontier.push(next); }
      }
      var batch = frontier.filter(function (e) { return !visited[e.id]; });
      frontier = [];
      if (!batch.length) { continue; }

      /* A root or selected port needs its implementation binding before it
       * continues; incoming flow and the binding arrive at a port together.
       * A binding never activates another port sharing the same provider. */
      appendWave(portBindings(batch.map(function (e) { return e.source; })));
      appendWave(batch.concat(portBindings(batch.map(function (e) { return e.target; }))));
      remaining -= batch.length;
      batch.forEach(function (edge) { activate(edge.target, frontier); });
    }

    /* Binding-only components have no flow arrival to accompany. */
    appendWave(bindings.filter(function (edge) { return !visited[edge.id]; }));
    scene.graph.nodes.forEach(function (node) {
      if (!reached[node.id]) { waves.push([{ nodeId: node.id }]); }
    });
    return waves;
  }

  /* Playback speed follows graph size, as the reference does. */
  function speedForWaveCount(count) {
    return count > 500 ? "16" : count > 80 ? "4" : "1";
  }

  return { planTrace: planTrace, speedForWaveCount: speedForWaveCount };
});
