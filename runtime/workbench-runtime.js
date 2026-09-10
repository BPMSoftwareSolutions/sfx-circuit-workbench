/* SideFX circuit workbench runtime — workbench-owned provider mechanics.
 *
 * This replaces the legacy page handlers. It does not reconstruct authority:
 * the interaction plan produced by realize-html-interaction is the authority
 * for state, validation, bindings and actions, and this file reads it.
 *
 * Division of ownership, decided once and enforced here:
 *
 *   sidefx-ui runtime  owns writable controls, validation reflection, action
 *                      dispositions and the dispatch boundary
 *   this file          owns the circuit region, the camera, the illustrative
 *                      trace overlay, keyed collection updates, and producer-
 *                      originated updates to read-only state
 *
 * Every region has exactly one mutation owner. This file never writes a
 * control the generic runtime writes, and it learns what the user asked for by
 * observing the generic runtime's own dispatch testimony rather than by
 * attaching a second click handler to the same button.
 *
 * Two boundary facts are recorded rather than hidden:
 *
 *   1. The generic runtime's reflect() updates validation bindings and action
 *      dispositions only. Read-aspect value bindings are realized once at
 *      projection time and never updated. Producer-originated updates
 *      therefore have no owner in the foundation, so this file supplies one
 *      and routes every write through the binding's declared physical region
 *      via plan.reflect. It does not invent ad hoc DOM writes.
 *   2. An action component's label cannot be bound, because the action family
 *      declares no binding directions. The playback affordance's label is
 *      therefore produced here against a declared state id rather than through
 *      a binding, and is reported as WORKBENCH_PRODUCER_BINDING_UNAVAILABLE.
 *
 * The illustrative trace is a finite inspection of declared routes. It observes
 * no branch decision, runs no provider and establishes no execution evidence.
 */
(function () {
  "use strict";

  var planNode = document.getElementById("sidefx-ui-plan");
  var configNode = document.getElementById("sfx-workbench-config");
  if (!planNode || !configNode) { return; }

  var plan = JSON.parse(planNode.textContent);
  var config = JSON.parse(configNode.textContent);
  var findings = [];

  function note(code, detail) { findings.push({ code: code, detail: detail }); }

  /* Every user-visible string is resolved through the declared text pack.
   * The runtime supplies values; the pack supplies wording, punctuation and
   * glyphs. Nothing below this line spells a separator or a message out. */
  var textModule = (typeof SFX_TEXT_FORMAT !== "undefined")
    ? SFX_TEXT_FORMAT : window.SFX_TEXT_FORMAT;
  if (!textModule || !config.text) {
    note("WORKBENCH_TEXT_PACK_UNAVAILABLE", "no declared text pack in this package");
    return;
  }
  var text = textModule.create(config.text, note);

  /* ---------------------------------------------------------------- plan */

  function component(componentId) {
    return document.querySelector(
      "[" + plan.componentAttribute + "=\"" + componentId + "\"]");
  }

  var bindingByState = {};
  plan.bindings.forEach(function (b) {
    if (b.aspect === "value") { bindingByState[b.state] = b; }
  });

  /* --------------------------------------------- producer state channel */

  /* Read-only state that a declared producer changes as the workbench runs.
   * The plan's own reflect descriptor names the physical region and property,
   * so this stays a routed write rather than a structural rewrite. */
  var producerState = {};
  plan.state.forEach(function (item) { producerState[item.stateId] = item.initialValue; });

  function publish(stateId, value) {
    producerState[stateId] = value;
    var binding = bindingByState[stateId];
    if (!binding) {
      note("WORKBENCH_PRODUCER_BINDING_UNAVAILABLE", stateId);
      return;
    }
    var node = component(binding.component);
    if (!node) { return; }

    if (binding.writable && binding.mutates) {
      var control = node.querySelector(binding.mutates.selector);
      if (control) { control[binding.mutates.property] = value; }
      return;
    }
    /* A wrapper that owns nested controls or items must never have its
     * textContent replaced; those components are updated by their own owner. */
    if (node.querySelector("[data-circuit-stage], li, select, input, button")) { return; }
    var target = node.matches(plan.reflect.text.selector)
      ? node : node.querySelector(plan.reflect.text.selector);
    (target || node)[plan.reflect.text.property] = value == null ? "" : String(value);
    republishTestimony();
  }

  function republishTestimony() {
    document.documentElement.setAttribute(
      "data-sfx-workbench-state", JSON.stringify({
        view: producerState["view.selected"],
        traceMode: producerState["trace.mode"],
        cameraScale: producerState["camera.scale"],
        presentation: producerState["presentation.mode"]
      }));
  }

  function userState(stateId) {
    /* State the generic runtime owns is read from its published testimony. */
    try {
      var published = JSON.parse(
        document.documentElement.getAttribute("data-sidefx-state") || "{}");
      if (Object.prototype.hasOwnProperty.call(published, stateId)) {
        return published[stateId];
      }
    } catch (error) { /* testimony absent or malformed; fall through */ }
    return producerState[stateId];
  }

  /* ----------------------------------------------------- circuit region */

  /* The wrapper carries the resolved geometry; the scene element inside it is
   * the one the provider gave overflow, so the camera scrolls that. Taking the
   * wrapper instead would scroll a box that never overflows. */
  var wrapper = component("circuit");
  var mount = wrapper && wrapper.querySelector("[data-component-type=\"circuit\"]");
  var stage = mount && mount.querySelector("[data-circuit-stage]");
  if (!mount || !stage) {
    note("WORKBENCH_CIRCUIT_REGION_UNAVAILABLE", "no circuit component in this projection");
    return;
  }

  var scene = null;
  var scale = 1;
  var selected = null;
  var loadToken = 0;

  function unsupported(message) {
    /* A missing scene is shown, never silently reduced to an empty box. */
    var notice = stage.querySelector(".circuit-unsupported")
      || document.createElement("p");
    notice.className = "circuit-unsupported";
    notice.textContent = message;
    if (!notice.parentNode) { stage.appendChild(notice); }
  }

  function sizing() {
    if (!scene) { return; }
    var svg = stage.querySelector("svg");
    if (svg) {
      svg.style.width = scene.geometry.width * scale + "px";
      svg.style.height = scene.geometry.height * scale + "px";
    }
    publish("camera.scale", scale);
    publish("camera.zoom-label",
            text.format("zoom", { percent: Math.round(scale * 100) }));
  }

  function fit() {
    if (!scene) { return; }
    scale = Math.min(
      1,
      (mount.clientWidth - 40) / scene.geometry.width,
      (mount.clientHeight - 40) / scene.geometry.height);
    sizing();
  }

  function centreOn(entityId) {
    var box = scene.geometry.boxes[entityId];
    if (!box) { return; }
    mount.scrollLeft = Math.max(0, (box[0] + box[2] / 2) * scale - mount.clientWidth / 2);
    mount.scrollTop = Math.max(0, (box[1] + box[3] / 2) * scale - mount.clientHeight / 2);
  }

  /* ------------------------------------------------------------ selection */

  function entity(entityId) {
    var found = null;
    scene.graph.nodes.some(function (n) {
      if (n.id === entityId) { found = { kind: "node", record: n }; return true; }
      return false;
    });
    if (found) { return found; }
    scene.graph.routes.some(function (r) {
      if (r.id === entityId) { found = { kind: "route", record: r }; return true; }
      return false;
    });
    return found;
  }

  function highlight(entityId) {
    Array.prototype.forEach.call(
      stage.querySelectorAll(".selected"),
      function (el) { el.classList.remove("selected"); });
    var target = entityId && stage.querySelector("[id=\"" + entityId + "\"]");
    if (target) { target.classList.add("selected"); }
  }

  function inspect(entityId) {
    var hit = entity(entityId);
    if (!hit) { return; }
    var record = hit.record;
    selected = hit.kind === "node" ? record.id : null;

    publish("selection.entity-id", record.id);
    publish("selection.kind", hit.kind === "node" ? record.kind : record.kind);
    publish("selection.title", record.label || record.identity);
    publish("selection.detail",
      (record.facts && record.facts.responsibility) || record.detail || record.kind);
    publish("selection.facts", JSON.stringify(
      Object.assign({ identity: record.identity }, record.facts || {}), null, 2));

    var source = record.source || record.provenance;
    publish("selection.source", source
      ? text.format("sourcePointer", { label: source.label,
          sha256: source.sha256, pointer: source.pointer })
      : "");

    highlight(record.id);
    renderRoutes();
    window.dispatchEvent(new CustomEvent("sfx-entity-selected", { detail: { sceneId: scene.sceneId, entity: record } }));
  }

  /* ------------------------------------------------- keyed collections */

  /* The legacy outline rebuilt itself wholesale on every keystroke and lost
   * focus. This updates by key and restores focus, which M1 requires. */
  function renderKeyed(componentId, items, build) {
    var host = component(componentId);
    if (!host) { return; }
    var list = host.querySelector("ul") || host;
    var active = document.activeElement;
    var activeKey = active && active.closest && active.closest("[data-item-key]")
      ? active.closest("[data-item-key]").getAttribute("data-item-key") : null;

    var existing = {};
    Array.prototype.forEach.call(list.children, function (child) {
      var key = child.getAttribute("data-item-key");
      if (key) { existing[key] = child; }
    });

    var order = [];
    items.forEach(function (item) {
      var node = existing[item.key];
      if (node) { delete existing[item.key]; build(node, item, false); }
      else {
        node = document.createElement("li");
        node.setAttribute("data-item-key", item.key);
        build(node, item, true);
      }
      order.push(node);
    });

    Object.keys(existing).forEach(function (key) {
      var node = existing[key];
      if (node.parentNode) { node.parentNode.removeChild(node); }
    });

    order.forEach(function (node, index) {
      if (list.children[index] !== node) {
        list.insertBefore(node, list.children[index] || null);
      }
    });

    if (activeKey) {
      var restored = list.querySelector("[data-item-key=\"" + activeKey + "\"] button");
      if (restored) { restored.focus(); }
    }
  }

  function renderOutline() {
    var query = String(userState("search.query") || "").toLowerCase();
    var matches = scene.graph.nodes.filter(function (n) {
      return (n.identity + " " + n.label + " " + (n.detail || ""))
        .toLowerCase().indexOf(query) !== -1;
    });

    renderKeyed("component-outline", matches.map(function (n) {
      return { key: n.id, node: n };
    }), function (li, item, created) {
      if (created) {
        var button = document.createElement("button");
        button.type = "button";
        var kind = document.createElement("small");
        button.appendChild(kind);
        button.appendChild(document.createTextNode(""));
        li.appendChild(button);
        button.addEventListener("click", function () {
          inspect(item.node.id);
          scale = Math.max(scale, 0.7);
          sizing();
          centreOn(item.node.id);
        });
      }
      var control = li.querySelector("button");
      control.querySelector("small").textContent = item.node.kind;
      control.lastChild.nodeValue = item.node.label;
    });

    publish("outline.items", matches.length);
    producerState["outline.items"] = matches.map(function (n) { return n.id; });
  }

  function renderRoutes() {
    var outgoing = !selected ? [] : scene.graph.routes.filter(function (r) {
      return r.source === selected && r.traversable;
    });

    renderKeyed("outgoing-routes", outgoing.map(function (r) {
      return { key: r.id, route: r };
    }), function (li, item, created) {
      if (created) {
        var button = document.createElement("button");
        button.type = "button";
        li.appendChild(button);
        button.addEventListener("click", function () { travel(item.route); });
      }
      var target = null;
      scene.graph.nodes.some(function (n) {
        if (n.id === item.route.target) { target = n; return true; }
        return false;
      });
      li.querySelector("button").textContent = text.format("routeChoice", {
        label: item.route.label || item.route.kind,
        target: target ? target.label : item.route.target });
    });

    if (!selected) { return; }
    var node = entity(selected);
    if (node && node.record.kind === "convergence") {
      status(text.message("convergence"));
    } else if (outgoing.length > 1) {
      status(text.message(node && node.record.kind === "fan-out"
        ? "fanOut" : "chooseRoute"));
    } else if (!outgoing.length) {
      status(text.message("endOfPath"));
    }
  }

  function status(text) { publish("trace.status", text); }

  /* --------------------------------------------------- illustrative trace */

  /* Planning lives in its own module so it can be compared against the frozen
   * reference implementation under Node. See tests/trace-parity.test.cjs. */
  var planner = (typeof SFX_ILLUSTRATIVE_TRACE !== "undefined")
    ? SFX_ILLUSTRATIVE_TRACE : window.SFX_ILLUSTRATIVE_TRACE;
  if (!planner) {
    note("WORKBENCH_TRACE_PLANNER_UNAVAILABLE", "illustrative-trace.js did not load");
  }

  function planTrace(preferred) {
    if (!planner) { return []; }
    return planner.planTrace(scene, preferred || undefined);
  }

  var trace = [], cursor = 0, running = false, playToken = 0, travelToken = 0;
  var visitedRoutes = {}, visitedNodes = {}, parallelCamera = false;

  function reducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion:reduce)").matches;
  }

  function playLabel(text) {
    producerState["trace.play-label"] = text;
    var node = component("play-trace");
    var control = node && node.querySelector("button");
    if (control) { control.textContent = text; }
    else { note("WORKBENCH_PRODUCER_BINDING_UNAVAILABLE", "trace.play-label"); }
  }

  function stopTrace() {
    if (running) {
      status(text.format("tracePaused", {
        visited: Object.keys(visitedRoutes).length,
        total: scene.graph.routes.length }));
    }
    running = false;
    parallelCamera = false;
    travelToken += 1;
    playToken += 1;
    playLabel(text.playLabel(
      trace.length && cursor < trace.length ? "paused" : "idle"));
  }

  function followPoint(point) {
    if (parallelCamera || !userState("trace.follow") || reducedMotion()) { return; }
    if (scene.geometry.width * scale > mount.clientWidth) {
      mount.scrollLeft = Math.max(0, point.x * scale - mount.clientWidth / 2);
    }
    if (scene.geometry.height * scale > mount.clientHeight) {
      mount.scrollTop = Math.max(0, point.y * scale - mount.clientHeight / 2);
    }
  }

  function travel(route) {
    var mine = travelToken;
    var group = stage.querySelector("[id=\"" + route.id + "\"]");
    var path = group && group.querySelector(".route-path");
    if (!path) {
      note("WORKBENCH_ROUTE_GEOMETRY_MISSING", route.id);
      return Promise.resolve(false);
    }
    visitedNodes[route.source] = true;
    group.classList.add("active-route");

    var ball = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    ball.setAttribute("r", "6");
    ball.setAttribute("fill", "#f2ffff");
    ball.setAttribute("stroke", "#89b8c3");
    ball.setAttribute("stroke-width", "2");
    stage.querySelector("svg").appendChild(ball);

    var length = path.getTotalLength();
    var duration = 800 / Number(userState("trace.speed") || 1);
    var reduce = reducedMotion();
    var start = performance.now();

    return new Promise(function (resolve) {
      function frame(now) {
        var t = reduce ? 1 : Math.min(1, (now - start) / duration);
        var point = path.getPointAtLength(length * t);
        ball.setAttribute("cx", point.x);
        ball.setAttribute("cy", point.y);
        followPoint(point);
        if (t === 1 || mine !== travelToken) { resolve(); }
        else { requestAnimationFrame(frame); }
      }
      requestAnimationFrame(frame);
    }).then(function () {
      if (ball.parentNode) { ball.parentNode.removeChild(ball); }
      if (mine !== travelToken) {
        if (!visitedRoutes[route.id]) { group.classList.remove("active-route"); }
        return false;
      }
      visitedRoutes[route.id] = true;
      inspect(route.target);
      return true;
    });
  }

  function step(play) {
    if (!scene) { return Promise.resolve(); }
    if (!trace.length || cursor >= trace.length) {
      trace = planTrace(trace.length ? null : selected);
      cursor = 0;
      visitedRoutes = {};
      visitedNodes = {};
      Array.prototype.forEach.call(stage.querySelectorAll(".active-route"),
        function (el) { el.classList.remove("active-route"); });
    }
    var mine = ++playToken;
    travelToken += 1;
    running = play;
    playLabel(text.playLabel(play ? "running" : "idle"));

    function advance() {
      var wave = trace[cursor];
      if (!wave) { return Promise.resolve(); }
      var batch = wave.filter(function (item) {
        return item.edgeId ? !visitedRoutes[item.edgeId] : !visitedNodes[item.nodeId];
      });
      if (!batch.length) { return Promise.resolve(); }

      parallelCamera = batch.length > 1;
      if (parallelCamera && userState("trace.follow")) { frameWave(batch); }

      var routeCount = batch.filter(function (i) { return i.edgeId; }).length;
      var done = Object.keys(visitedRoutes).length;
      status(batch.length > 1
        ? text.format("traceParallel", {
            from: done + 1, to: done + routeCount,
            total: scene.graph.routes.length, branches: batch.length })
        : text.format("traceStep", {
            index: Math.min(done + 1, scene.graph.routes.length),
            total: scene.graph.routes.length,
            kind: batch[0].kind || text.format("isolatedComponent", {}) }));

      return Promise.all(batch.map(function (item) {
        if (item.edgeId) {
          var route = scene.graph.routes.filter(function (r) {
            return r.id === item.edgeId; })[0];
          return travel(route);
        }
        inspect(item.nodeId);
        visitedNodes[item.nodeId] = true;
        var box = scene.geometry.boxes[item.nodeId];
        if (box) { followPoint({ x: box[0] + box[2] / 2, y: box[1] + box[3] / 2 }); }
        return Promise.resolve(true);
      })).then(function (completed) {
        if (mine !== playToken) { return null; }
        parallelCamera = false;
        if (completed.some(function (ok) { return !ok; })) { return null; }
        batch.forEach(function (item) {
          var node = stage.querySelector("[id=\"" + (item.target || item.nodeId) + "\"]");
          if (node) { node.classList.add("selected"); }
        });
        cursor += 1;
        if (play && running && cursor < trace.length) { return advance(); }
        return true;
      });
    }

    return advance().then(function (outcome) {
      if (mine !== playToken || outcome === null) { return; }
      if (cursor === trace.length) {
        stopTrace();
        playLabel(text.playLabel("complete"));
        status(text.format("traceComplete", {
          visited: Object.keys(visitedRoutes).length,
          total: scene.graph.routes.length,
          components: Object.keys(visitedNodes).length,
          totalComponents: scene.graph.nodes.length }));
      } else {
        running = false;
        playLabel(text.playLabel("paused"));
        status(text.format("tracePaused", {
          visited: Object.keys(visitedRoutes).length,
          total: scene.graph.routes.length }));
      }
    });
  }

  function frameWave(batch) {
    var boxes = [];
    batch.forEach(function (item) {
      [item.source, item.target].forEach(function (id) {
        if (scene.geometry.boxes[id]) { boxes.push(scene.geometry.boxes[id]); }
      });
    });
    if (!boxes.length) { return; }
    var left = Math.min.apply(null, boxes.map(function (b) { return b[0]; }));
    var top = Math.min.apply(null, boxes.map(function (b) { return b[1]; }));
    var right = Math.max.apply(null, boxes.map(function (b) { return b[0] + b[2]; }));
    var bottom = Math.max.apply(null, boxes.map(function (b) { return b[1] + b[3]; }));
    scale = Math.min(1, (mount.clientWidth - 60) / (right - left),
      (mount.clientHeight - 60) / (bottom - top));
    sizing();
    mount.scrollLeft = Math.max(0, (left + right) * scale / 2 - mount.clientWidth / 2);
    mount.scrollTop = Math.max(0, (top + bottom) * scale / 2 - mount.clientHeight / 2);
  }

  function resetTrace() {
    stopTrace();
    trace = [];
    cursor = 0;
    visitedRoutes = {};
    visitedNodes = {};
    selected = null;
    playLabel(text.playLabel("idle"));
    Array.prototype.forEach.call(stage.querySelectorAll(".selected, .active-route"),
      function (el) { el.classList.remove("selected", "active-route"); });
    renderRoutes();
    status(text.message("traceIdle"));
  }


  /* ------------------------------------------------------- estate selection */

  /* The catalogue is data: 218 capabilities, their views, their scenarios and
   * what may be done with each. The runtime reads it and narrows — capability,
   * then scenario, then view — rather than knowing any capability by name. */
  var catalogue = config.estate || { capabilities: [] };

  function capability(capabilityId) {
    return catalogue.capabilities.filter(function (c) {
      return c.capabilityId === capabilityId; })[0] || null;
  }

  function viewsFor(capabilityId, scenarioId) {
    var record = capability(capabilityId);
    if (!record) { return []; }
    return record.views.filter(function (view) {
      return !scenarioId || view.scenarioId === scenarioId;
    });
  }

  function fillChoice(stateId, options, selected) {
    var binding = bindingByState[stateId];
    var node = binding && component(binding.component);
    var control = node && binding.mutates
      && node.querySelector(binding.mutates.selector);
    if (!control) { return null; }
    control.replaceChildren();
    options.forEach(function (option) {
      var element = document.createElement("option");
      element.value = option.value;
      element.textContent = option.label;
      control.appendChild(element);
    });
    if (selected !== undefined && selected !== null) { control.value = selected; }
    producerState[stateId] = control.value;
    return control.value;
  }

  /* A capability the package cannot resolve a scene for is still offered, and
   * says so when opened. Hiding it would misrepresent the estate. */
  function renderCapabilities() {
    var options = catalogue.capabilities.map(function (record) {
      return {
        value: record.capabilityId,
        label: text.format("capabilityOption", {
          capabilityId: record.capabilityId,
          views: record.views.length,
          affordance: text.message(record.affordances.indexOf("invoke") !== -1
            ? "capabilityInvocable" : "capabilityInspectOnly")
        })
      };
    });
    return fillChoice("estate.capability", options, config.initialCapabilityId);
  }

  function renderScenarios(capabilityId) {
    var record = capability(capabilityId);
    var scenarios = (record && record.scenarios) || [];
    var options = [{
      value: "",
      label: text.format("scenarioAll", { count: scenarios.length })
    }].concat(scenarios.map(function (scenarioId) {
      return { value: scenarioId, label: text.format("scenarioOption", { scenarioId: scenarioId }) };
    }));
    return fillChoice("estate.scenario", options, "");
  }

  function renderViews(capabilityId, scenarioId) {
    var views = viewsFor(capabilityId, scenarioId);
    var options = views.map(function (view) {
      return {
        value: view.viewId,
        label: text.format("viewOption", {
          label: view.label, nodes: view.coverage.nodes, routes: view.coverage.routes })
      };
    });
    var chosen = fillChoice("view.selected", options, options.length ? options[0].value : "");
    return chosen;
  }

  function selectedView(capabilityId, viewId) {
    var views = viewsFor(capabilityId, null);
    return views.filter(function (view) { return view.viewId === viewId; })[0] || null;
  }

  function openSelection(capabilityId, scenarioId, viewId) {
    publish("capability.title", capabilityId);
    var view = viewId && selectedView(capabilityId, viewId);
    if (!view) {
      publish("view.scope", text.message("graphUnavailable"));
      unsupported(text.message("sceneUnpublished"));
      return;
    }
    if (!view.scene) {
      /* Catalogued, but its circuit is not in this package. Said plainly rather
       * than shown as an empty diagram. */
      publish("view.scope", text.message("graphUnavailable"));
      publish("view.coverage", text.format("coverage", {
        nodes: view.coverage.nodes, routes: view.coverage.routes,
        omitted: view.coverage.omittedSourceNodes }));
      unsupported(text.message("sceneNotPackaged"));
      return;
    }
    loadSceneFrom(view);
  }

  /* Load a scene from an estate view descriptor. Race protection and the
   * identity check are the same as any other load: a stale response can never
   * replace the newly selected view. */
  function loadSceneFrom(view) {
    var mine = ++loadToken;
    publish("view.scope", text.message("loadingGraph"));
    fetch(view.scene, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) { throw new Error("scene unavailable"); }
        return response.json();
      })
      .then(function (payload) {
        if (mine !== loadToken) { return; }
        if (payload.sceneVersion !== "circuit-scene.v1") {
          publish("view.scope", text.message("sceneContractUnsupported"));
          unsupported(text.message("sceneContractUnsupported"));
          return;
        }
        if (payload.identities.viewId !== view.viewId) {
          publish("view.scope", text.message("graphIdentityMismatch"));
          return;
        }
        var artifact = view.scene.replace(/\.scene\.json$/, ".svg");
        return fetch(artifact, { credentials: "same-origin" })
          .then(function (r) { return r.ok ? r.text() : ""; })
          .then(function (svg) {
            if (mine !== loadToken) { return; }
            adopt(payload, svg);
          });
      })
      .catch(function () {
        if (mine !== loadToken) { return; }
        publish("view.scope", text.message("graphUnavailable"));
        unsupported(text.message("sceneLoadFailed"));
      });
  }

  /* --------------------------------------------------------- run telemetry */

  /* Delivery phases are reported progress that maps to no circuit node. They
   * are listed here, beside the circuit, and never drawn on it. */
  function renderTelemetry(runId, phases) {
    publish("telemetry.heading", runId
      ? text.format("telemetryHeading", { runId: String(runId).slice(0, 18) })
      : text.message("telemetryHeadingIdle"));

    renderKeyed("run-telemetry", (phases || []).map(function (entry, index) {
      return { key: entry.phase + ":" + index, entry: entry };
    }), function (li, item, created) {
      if (created) {
        var line = document.createElement("span");
        li.appendChild(line);
      }
      li.querySelector("span").textContent = text.format("telemetryPhase", {
        label: item.entry.label, status: item.entry.status });
      li.setAttribute("data-phase-status", item.entry.status || "");
    });
    producerState["telemetry.items"] = (phases || []).map(function (p) { return p.phase; });
  }

  /* ------------------------------------------------------------ scene load */

  function loadScene(viewId) {
    var descriptor = config.scenes.filter(function (s) { return s.viewId === viewId; })[0];
    if (!descriptor) {
      publish("view.scope", text.message("graphUnavailable"));
      unsupported(text.message("sceneUnpublished"));
      return;
    }
    var mine = ++loadToken;
    publish("view.scope", text.message("loadingGraph"));

    fetch(descriptor.scene, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) { throw new Error("scene unavailable"); }
        return response.json();
      })
      .then(function (payload) {
        /* A stale response can never replace the newly selected graph. */
        if (mine !== loadToken) { return; }
        if (payload.sceneVersion !== "circuit-scene.v1") {
          publish("view.scope", text.message("sceneContractUnsupported"));
          unsupported(text.message("sceneContractUnsupported"));
          return;
        }
        if (payload.identities.viewId !== viewId) {
          publish("view.scope", text.message("graphIdentityMismatch"));
          return;
        }
        return fetch(descriptor.artifact, { credentials: "same-origin" })
          .then(function (r) { return r.text(); })
          .then(function (svg) {
            if (mine !== loadToken) { return; }
            adopt(payload, svg);
          });
      })
      .catch(function () {
        if (mine !== loadToken) { return; }
        publish("view.scope", text.message("graphUnavailable"));
        unsupported(text.message("sceneLoadFailed"));
      });
  }


  /* A derived circuit has geometry but no rendered artifact, because it was
   * read from authority rather than compiled by the estate's renderer. Draw it
   * from the scene itself: the contract already carries boxes, routes and hit
   * targets, so the same selection and keyboard model applies either way. */
  function renderSceneSvg(payload) {
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + payload.geometry.width + " " + payload.geometry.height);
    svg.setAttribute("width", payload.geometry.width);
    svg.setAttribute("height", payload.geometry.height);

    var centre = {};
    payload.graph.nodes.forEach(function (node) {
      var box = payload.geometry.boxes[node.id];
      if (!box) { return; }
      centre[node.id] = { x: box[0] + box[2] / 2, y: box[1] + box[3] / 2,
                          w: box[2], h: box[3], x0: box[0], y0: box[1] };
    });

    payload.graph.routes.forEach(function (route) {
      var a = centre[route.source], b = centre[route.target];
      if (!a || !b) { return; }
      var group = document.createElementNS(NS, "g");
      group.setAttribute("id", route.id);
      group.setAttribute("data-route", route.id);
      group.setAttribute("role", "button");
      group.setAttribute("tabindex", "0");
      group.setAttribute("aria-label", route.kind + ": " + (route.label || route.kind));
      var path = document.createElementNS(NS, "path");
      path.setAttribute("class", "route-path");
      path.setAttribute("d", "M " + a.x + " " + a.y + " L " + b.x + " " + b.y);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "#6f9fb0");
      path.setAttribute("stroke-width", "2");
      /* A provider binding is drawn as it is declared: not traversed. */
      if (route.traversable === false) { path.setAttribute("stroke-dasharray", "8 6"); }
      group.appendChild(path);
      svg.appendChild(group);
    });

    payload.graph.nodes.forEach(function (node) {
      var box = centre[node.id];
      if (!box) { return; }
      var group = document.createElementNS(NS, "g");
      group.setAttribute("id", node.id);
      group.setAttribute("data-entity", node.id);
      group.setAttribute("role", "button");
      group.setAttribute("tabindex", "0");
      group.setAttribute("aria-label", node.kind + ": " + node.label);
      var rect = document.createElementNS(NS, "rect");
      rect.setAttribute("x", box.x0); rect.setAttribute("y", box.y0);
      rect.setAttribute("width", box.w); rect.setAttribute("height", box.h);
      rect.setAttribute("rx", "10");
      rect.setAttribute("fill", "#0f2b3a");
      rect.setAttribute("stroke", "#4fd1c5");
      rect.setAttribute("stroke-width", "2");
      group.appendChild(rect);

      var kind = document.createElementNS(NS, "text");
      kind.setAttribute("x", box.x); kind.setAttribute("y", box.y0 + 26);
      kind.setAttribute("text-anchor", "middle");
      kind.setAttribute("fill", "#7fd7c8");
      kind.setAttribute("font-size", "13");
      kind.setAttribute("font-family", "ui-monospace, monospace");
      kind.textContent = node.kind.toUpperCase();
      group.appendChild(kind);

      /* Wrap the label rather than letting it run past the box. */
      var words = String(node.label).split(/[\s.]+/);
      var line = "", lines = [];
      words.forEach(function (word) {
        var candidate = line ? line + " " + word : word;
        if (candidate.length > 24) { lines.push(line); line = word; } else { line = candidate; }
      });
      if (line) { lines.push(line); }
      lines.slice(0, 4).forEach(function (row, index) {
        var label = document.createElementNS(NS, "text");
        label.setAttribute("x", box.x);
        label.setAttribute("y", box.y0 + 54 + index * 19);
        label.setAttribute("text-anchor", "middle");
        label.setAttribute("fill", "#e6f6f4");
        label.setAttribute("font-size", "15");
        label.textContent = row;
        group.appendChild(label);
      });
      svg.appendChild(group);
    });
    return svg;
  }

  function adopt(payload, svg) {
    scene = payload;
    selected = null;
    trace = [];
    cursor = 0;
    visitedRoutes = {};
    visitedNodes = {};

    stage.textContent = "";
    if (svg) {
      stage.insertAdjacentHTML("afterbegin", svg);
    } else {
      stage.appendChild(renderSceneSvg(scene));
    }

    scene.hitTargets.forEach(function (target) {
      var node = stage.querySelector("[id=\"" + target.targetId + "\"]");
      if (!node) { return; }
      node.addEventListener("click", function () { inspect(target.entityId); });
      if (target.keyboardActivable) {
        node.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            inspect(target.entityId);
          }
        });
      }
    });

    publish("view.scope", text.viewKindName(scene.identities.viewKind));
    publish("view.coverage", text.format("coverage", {
      nodes: scene.coverage.nodes, routes: scene.coverage.routes,
      omitted: scene.coverage.omittedSourceNodes }));
    publish("view.findings", (scene.findings || []).map(function (f) {
      return text.format("finding", { code: f.code, identity: f.identity || "" });
    }).join(" " + text.glyph("separator") + " "));
    publish("trace.mode", scene.traceMode);
    publish("selection.kind", text.viewKindName(scene.identities.viewKind));
    publish("selection.title", scene.label);
    /* The title names whichever capability is loaded. The scene carries that
     * identity, so no capability is named in this file. */
    publish("capability.title", scene.identities.viewKind === "invocation"
      ? scene.label : scene.identities.capabilityId);
    publish("experience.limit", scene.coverage.scope || config.evidenceLimit);
    publish("selection.detail", text.message("inspectPrompt"));
    publish("selection.facts", "");
    publish("selection.source", "");
    playLabel(text.playLabel("idle"));
    status(text.message(scene.identities.viewKind === "expression"
      ? "traceIdleExpression" : "traceIdle"));

    /* Playback speed follows graph size, as the reference does. */
    var waves = planTrace(null).length;
    setSpeed(planner ? planner.speedForWaveCount(waves) : "1");

    applyPresentation(userState("presentation.mode"));
    renderOutline();
    renderRoutes();

    fit();
    reportHeight();
    window.dispatchEvent(new CustomEvent("sfx-scene-loaded", { detail: { sceneId: scene.sceneId } }));
  }

  function setSpeed(value) {
    var binding = bindingByState["trace.speed"];
    var node = binding && component(binding.component);
    var control = node && node.querySelector(binding.mutates.selector);
    if (control) { control[binding.mutates.property] = value; }
    producerState["trace.speed"] = value;
  }

  function applyPresentation(mode) {
    producerState["presentation.mode"] = mode;
    mount.classList.toggle("base", mode === "base");
    [["show-material", "material"], ["show-base-svg", "base"]].forEach(function (pair) {
      var node = component(pair[0]);
      var control = node && node.querySelector("button");
      if (control) { control.setAttribute("aria-pressed", String(mode === pair[1])); }
    });
    republishTestimony();
  }

  function exportScene() {
    if (!scene) { return; }
    var source = stage.querySelector("svg");
    if (!source) { return; }
    var copy = new DOMParser().parseFromString(source.outerHTML, "image/svg+xml");
    Array.prototype.forEach.call(copy.querySelectorAll("image"), function (image) {
      image.parentNode.removeChild(image);
    });
    var blob = new Blob([new XMLSerializer().serializeToString(copy)],
      { type: "image/svg+xml" });
    var url = URL.createObjectURL(blob);
    var anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = text.format("exportFileName", {
      capabilityId: scene.identities.capabilityId,
      viewId: scene.identities.viewId });
    anchor.click();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  /* --------------------------------------------------- action observation */

  /* The generic runtime owns the click and publishes its disposition. This
   * observes that testimony rather than attaching a second handler, so the
   * admissibility decision is never made twice or made differently here. */
  var ACTIONS = {
    "select-source-view": function () {
      openSelection(userState("estate.capability"), userState("estate.scenario"),
                    userState("view.selected"));
    },
    "select-capability": function () {
      var capabilityId = userState("estate.capability");
      renderScenarios(capabilityId);
      var viewId = renderViews(capabilityId, null);
      openSelection(capabilityId, null, viewId);
    },
    "select-scenario": function () {
      var capabilityId = userState("estate.capability");
      var scenarioId = userState("estate.scenario") || null;
      var viewId = renderViews(capabilityId, scenarioId);
      openSelection(capabilityId, scenarioId, viewId);
    },
    "show-material": function () { applyPresentation("material"); },
    "show-base-svg": function () { applyPresentation("base"); },
    "fit-diagram": fit,
    "read-at-100": function () { scale = 1; sizing(); },
    "zoom-in": function () { scale = Math.min(2, scale * 1.3); sizing(); },
    "zoom-out": function () { scale = Math.max(0.01, scale / 1.3); sizing(); },
    "download-svg": exportScene,
    "search-components": function () { renderOutline(); },
    "play-trace": function () { if (running) { stopTrace(); } else { step(true); } },
    "next-trace-step": function () { stopTrace(); step(false); },
    "reset-trace": resetTrace,
    "set-trace-speed": function () { /* read at animation time from plan state */ },
    "toggle-trace-follow": function () { /* read at animation time from plan state */ }
  };

  new MutationObserver(function () {
    var raw = document.documentElement.getAttribute("data-sidefx-last-dispatch");
    if (!raw) { return; }
    var dispatch;
    try { dispatch = JSON.parse(raw); } catch (error) { return; }
    if (dispatch.disposition !== "DISPATCHED") { return; }
    var handler = ACTIONS[dispatch.actionId];
    if (producerState["trace.mode"] === "LIVE" && /trace|reset/.test(dispatch.actionId)) return;
    if (handler) { handler(dispatch); }
  }).observe(document.documentElement,
    { attributes: true, attributeFilter: ["data-sidefx-last-dispatch"] });

  /* Search and the selection chain are writable controls the generic runtime
   * owns; observing its published state keeps a single owner for each.
   *
   * A choice does not dispatch a semantic action when its value changes — it
   * updates bound state. So the chain is driven from state here rather than
   * from the action table, which only ever sees clicks. */
  var lastSelection = { capability: null, scenario: null, view: null };

  new MutationObserver(function () {
    if (scene) { renderOutline(); }

    var capabilityId = userState("estate.capability");
    var scenarioId = userState("estate.scenario") || null;
    var viewId = userState("view.selected");

    if (capabilityId && capabilityId !== lastSelection.capability) {
      lastSelection.capability = capabilityId;
      lastSelection.scenario = null;
      renderScenarios(capabilityId);
      lastSelection.view = renderViews(capabilityId, null);
      openSelection(capabilityId, null, lastSelection.view);
      return;
    }
    if (scenarioId !== lastSelection.scenario) {
      lastSelection.scenario = scenarioId;
      lastSelection.view = renderViews(capabilityId, scenarioId);
      openSelection(capabilityId, scenarioId, lastSelection.view);
      return;
    }
    if (viewId && viewId !== lastSelection.view) {
      lastSelection.view = viewId;
      openSelection(capabilityId, scenarioId, viewId);
    }
  }).observe(document.documentElement,
    { attributes: true, attributeFilter: ["data-sidefx-state"] });

  /* ------------------------------------------------------------ embedding */

  function reportHeight() {
    if (!config.embed || !config.embed.reportHeight) { return; }
    parent.postMessage({ type: "sidefx-circuit-height", height: document.body.scrollHeight }, "*");
  }

  if (window.ResizeObserver) { new ResizeObserver(reportHeight).observe(document.body); }
  window.addEventListener("pagehide", stopTrace);

  /* ---------------------------------------------------------------- start */

  var startCapability = renderCapabilities();
  if (startCapability) {
    renderScenarios(startCapability);
    var startView = renderViews(startCapability, null);
    renderTelemetry(null, []);
    lastSelection.capability = startCapability;
    lastSelection.scenario = null;
    lastSelection.view = startView;
    openSelection(startCapability, null, startView);
  } else {
    renderTelemetry(null, []);
    loadScene(config.initialViewId);
  }
  reportHeight();

  window.SFX_WORKBENCH = {
    findings: findings,
    renderTelemetry: renderTelemetry,
    catalogue: function () { return catalogue; },
    state: function () { return producerState; },
    scene: function () { return scene; },
    camera: function () { return scale; },
    mount: function () { return mount; },
    stage: function () { return stage; },
    activate: adopt,
    stopTrace: stopTrace,
    publish: publish,
    status: status,
    fit: fit
  };
})();
