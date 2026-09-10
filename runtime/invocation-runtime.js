/* Application integration over resolved UI plans and authenticated run records.
 * No schemas, taxonomy rules or capability-name dispatch enter this runtime. */
(function () {
  'use strict';
  var wb = window.SFX_WORKBENCH;
  if (!wb) return;
  var manifest, active = null, drafts = {}, retained = {}, opening = false;
  var selector = document.querySelector('[data-component-id="source-view"] select');
  var pendingKey = 'sidefx-workbench-pending';
  var scriptConfig = JSON.parse(document.getElementById('sfx-workbench-config').textContent);
  var scrim = document.createElement('div'); scrim.className = 'sfx-overlay-scrim'; scrim.hidden = true; document.body.appendChild(scrim);
  function current() { return manifest && manifest.capabilities.find(function (c) { return wb.scene() && c.sceneId === wb.scene().sceneId; }); }
  function message(text) { wb.status(text); }
  function remember(value) { try { if (value) sessionStorage.setItem(pendingKey, JSON.stringify(value)); else sessionStorage.removeItem(pendingKey); } catch (_) {} }
  function camera() {
    var svg = wb.stage().querySelector('svg'), box = svg && svg.getBoundingClientRect();
    return { scale: wb.camera(), stageOrigin: box ? { x: box.left, y: box.top } : { x: 0, y: 0 } };
  }
  function bodyOf(layout) {
    var content = document.createElement('div'); content.innerHTML = layout.html;
    // Recomposition establishes these extents. The viewport can scroll inside
    // the overlay without changing the regions or the bound state identities.
    content.className = 'sfx-dialog-content'; return content;
  }
  function makeOverlay(capability, anchor, initialLifecycle) {
    var provider = window.SFX_OVERLAY_PROVIDER.create({ tokens: scriptConfig.overlay, motion: capability.motion,
      anchor: anchor, scene: wb.scene, camera: camera, initialLifecycle: initialLifecycle,
      onFocusLost: function () { wb.mount().focus(); },
      onTransition: function (step) {
        var shown = ['EDITING', 'SUBMITTING', 'REFUSED', 'UNCERTAIN', 'OUTCOME'].includes(step.state.state);
        scrim.hidden = !shown;
        document.querySelector('.ui-surface').inert = shown;
        if (step.state.state === 'CLOSED') { selector.disabled = false; }
      }
    });
    return provider;
  }
  function attach(layout, overlay) {
    var content = bodyOf(layout);
    content.querySelectorAll('[data-component-id]').forEach(function(region){
      var control=region.querySelector('input,select,button,textarea');
      if(control && !control.id)control.id='dialog-'+region.getAttribute('data-component-id')+'-control';
    });
    overlay.mount(content, { dialogId: 'capability', description: layout.description });
    overlay.element().style.width = layout.geometry.width + 'px';
    overlay.element().style.maxHeight = 'calc(100vh - 32px)'; overlay.element().style.overflow = 'auto';
    return content;
  }
  function fieldsEnabled(content, yes) {
    content.querySelectorAll('input,select,button').forEach(function (node) { node.disabled = !yes; });
  }
  async function establishSession() {
    var response = await fetch('/workbench/session', { method: 'POST', credentials: 'same-origin' });
    if (!response.ok) throw new Error('Run service unavailable. Your input has been preserved.');
  }
  async function openInput(capability) {
    if (opening || active && !['CLOSED','OUTCOME'].includes(active.overlay.state().state)) return;
    opening = true;
    try {
      if (active) active.overlay.unmount();
      var layout = await fetch(capability.dialog).then(function (r) { if (!r.ok) throw new Error('Input dialog unavailable'); return r.json(); });
      var overlay = makeOverlay(capability, capability.inputAnchor);
      var content = attach(layout, overlay);
      var inputId = wb.scene().invocation.inputNode;
      overlay.setOpener(inputId);
      active = { capability: capability, overlay: overlay, content: content, layout: layout, cursor: 0, events: [] };
      var interaction = window.SidefxUIRuntime.mount(content, layout.interaction, drafts[capability.subject]);
      active.interaction = interaction;
      overlay.dispatch({ type: 'open', anchorIdentity: capability.inputAnchor.nodeIdentity });
      var status = content.querySelector('[data-component-id="dialog-status"]');
      function error(text) { if (status) { status.textContent = text; status.setAttribute('role','status'); } }
      content.addEventListener('input', function () { drafts[capability.subject] = interaction.state(); });
      content.addEventListener('change', function () { drafts[capability.subject] = interaction.state(); });
      content.addEventListener('sidefx-ui-action', async function (e) {
        if (e.detail.disposition !== 'DISPATCHED') return;
        if (e.detail.scenarioEvent === 'workbench.capability.cancelled') {
          drafts[capability.subject] = interaction.state(); overlay.dispatch({ type: 'cancel' }); return;
        }
        if (e.detail.scenarioEvent !== 'workbench.capability.submitted' || overlay.state().state !== 'EDITING') return;
        var state = interaction.state(); drafts[capability.subject] = state;
        var command = { commandVersion: 'workbench-command.v1', requestId: crypto.randomUUID(), publicationId: manifest.publicationId,
          subject: capability.subject, editableValues: Object.fromEntries(layout.fields.map(function (f) { return [f.pointer, state[f.stateId]]; })) };
        if (layout.exampleSelector) command.exampleSelection = state[layout.exampleSelector.stateId];
        overlay.dispatch({ type: 'edit', draft: state });
        var submitted = overlay.dispatch({ type: 'submit', requestId: command.requestId });
        if (submitted.refused) return;
        selector.disabled = true; fieldsEnabled(content, false); error('Submitting…');
        document.documentElement.removeAttribute('data-sfx-run-result');
        active.command = command; remember({ command: command });
        try { await establishSession(); await submit(active, error); }
        catch (failure) {
          overlay.dispatch({ type: 'refused' }); overlay.dispatch({ type: 'acknowledge' });
          fieldsEnabled(content, true); selector.disabled = false; error(failure.message); remember(null);
        }
      });
    } catch (error) { message(error.message); }
    finally { opening = false; }
  }
  async function submit(run, error) {
    var response;
    try {
      response = await fetch('/workbench/runs', { method: 'POST', credentials: 'same-origin', headers: { 'content-type':'application/json' }, body: JSON.stringify(run.command) });
      var result = await response.json();
      if (result.disposition === 'ADMITTED') {
        run.runId = result.runId; run.selection = result.selection;
        remember({ command: run.command, runId: run.runId });
        run.overlay.dispatch({ type: 'admitted', runId: run.runId });
        wb.stopTrace(); wb.publish('trace.mode', 'LIVE'); message('Accepted · waiting for reported execution');
        poll(run); return;
      }
      if (result.code === 'SESSION_REQUIRED') {
        run.overlay.dispatch({type:'uncertain'});
        error('The session for this request is unavailable. Its execution is uncertain; restore the original session to reconcile it.');
        return;
      }
      if (result.disposition === 'REFUSED' || response.status >= 400 && response.status < 500) {
        run.overlay.dispatch({ type: 'refused' }); run.overlay.dispatch({ type: 'acknowledge' });
        fieldsEnabled(run.content, true); selector.disabled = false;
        error(result.message || result.code || 'Request refused'); remember(null); return;
      }
    } catch (_) { /* Admission may already have occurred. Keep its identity. */ }
    showUncertain(run, error);
  }
  function showUncertain(run, error) {
    run.overlay.dispatch({ type: 'uncertain' });
    error('Admission is uncertain. Reconcile this request before starting another.');
    var check = document.createElement('button'); check.textContent = 'Reconcile this request';
    check.onclick = async function () {
      check.disabled = true;
      // Explicit reconciliation resends the same body and request ID. The
      // durable service either finds that run or admits it exactly once.
      run.overlay.dispatch({ type: 'acknowledge' }); run.overlay.dispatch({ type: 'submit', requestId: run.command.requestId });
      check.remove(); await submit(run, error);
    };
    run.overlay.element().append(check);
  }
  function markObservation(run, event) {
    var scene = wb.scene(), mapping = scene && scene.invocation;
    if (!mapping || mapping.subject !== run.capability.subject) return;
    var o = event.observation, id = null;
    if (o && o.scenarioId === run.selection.scenarioId) id = mapping.stepNodes[o.stepId];
    if (o && o.phase === 'bindProviderInput') id = mapping.providerNode;
    if (o && o.phase === 'executeScenario') id = mapping.eventNode;
    wb.stage().querySelectorAll('.live-active').forEach(function (n) { n.classList.remove('live-active'); });
    if (id) {
      var node = document.getElementById(id); if (node) { node.classList.add('live-active', 'live-observed'); node.dataset.observedEvent = event.eventId; }
      var edge = scene.graph.routes.find(function (r) { return r.target === id; });
      if (edge && o && o.stepId) { var route = document.getElementById(edge.id); if (route) route.classList.add('live-observed'); }
    }
    var names = { readAuthority:'Reading selected database authority', planNativeBody:'Resolving native plan',
      bindProviderInput:'Provider input binding', loadMemoryModules:'Loading verified runtime', createScenario:'Establishing scenario', executeScenario:'Executing scenario' };
    if (o) message((names[o.phase] || o.stepId || o.observationType) + ' · ' + o.status + ' · ' + event.observedAt);
    run.events.push({ eventId:event.eventId, receivedAt:new Date().toISOString(), serverTime:event.observedAt, observation:o || null });
    document.documentElement.setAttribute('data-sfx-live-run', JSON.stringify({ runId:run.runId, cursor:event.sequence, event:event.kind, observation:o || null }));
    window.dispatchEvent(new CustomEvent('sfx-live-event', { detail:{ runId:run.runId, event:event, receivedAt:new Date().toISOString() } }));
  }
  async function poll(run) {
    while (active === run) {
      try {
        var response = await fetch('/workbench/runs/' + run.runId + '?after=' + run.cursor, { cache:'no-store', credentials:'same-origin' });
        if (!response.ok) throw new Error('status');
        var snapshot = await response.json();
        if (snapshot.runId !== run.runId || snapshot.selection.publicationId !== manifest.publicationId || snapshot.selection.subject !== run.capability.subject) throw new Error('identity');
        run.selection = snapshot.selection;
        snapshot.events.forEach(function (event) {
          if (event.sequence <= run.cursor) return;
          if (event.sequence !== run.cursor + 1) throw new Error('gap');
          markObservation(run, event); run.cursor = event.sequence;
        });
        if (snapshot.state === 'EXECUTING') run.overlay.dispatch({ type:'executing' });
        if (['COMPLETED','FAILED','UNKNOWN'].includes(snapshot.state)) {
          run.snapshot=snapshot; retained[run.capability.subject]=run;
          remember(null);
          try { await showOutcome(run, false); }
          catch (failure) { message('The run ended, but its outcome display failed: ' + failure.message); console.error(failure); }
          return;
        }
      } catch (_) { message('Connection interrupted · the run may still be executing. Reconnecting to its retained status…'); }
      await new Promise(function (resolve) { setTimeout(resolve, 350); });
    }
  }
  async function showOutcome(run, reopen) {
    var snapshot = run.snapshot, content, layout;
    run.overlay.unmount();
    // Outcome anchoring is a separate provider instance so an input-collapse
    // callback can never unmount a newly opened outcome dialog.
    var lifecycle=window.SFX_DIALOG_LIFECYCLE.initial();
    [{type:'open'},{type:'submit',requestId:run.command.requestId},{type:'admitted',runId:run.runId}].forEach(function(event){
      lifecycle=window.SFX_DIALOG_LIFECYCLE.reduce(lifecycle,event).state;
    });
    var overlay=makeOverlay(run.capability, snapshot.presentation && snapshot.presentation.anchor || run.capability.outcomeAnchor,lifecycle);
    run.overlay=overlay;
    if (snapshot.presentation) {
      layout=await fetch(snapshot.presentation.layout).then(function(r){return r.json();});
      content=attach(layout,overlay);
      window.SidefxUIRuntime.mount(content,layout.interaction);
      window.SFX_OUTCOME_COMPONENTS.render(content,snapshot.presentation);
      content.addEventListener('sidefx-ui-action',function(e){if(e.detail.scenarioEvent==='workbench.outcome.dismissed') overlay.dispatch({type:'dismiss'});});
    } else {
      content=document.createElement('section'); content.className='sfx-delivery-status';
      var title=document.createElement('h2'); title.textContent='Execution status'; title.setAttribute('data-component-id','outcome-title');
      var detail=document.createElement('p'); detail.textContent=snapshot.presentationFinding || snapshot.result && snapshot.result.message || 'No validated capability outcome was returned.';
      var code=document.createElement('p'); code.textContent=snapshot.result && snapshot.result.code || snapshot.state;
      var close=document.createElement('button'); close.textContent='Close'; close.onclick=function(){overlay.dispatch({type:'dismiss'});};
      content.append(title,detail,code,close); overlay.mount(content,{dialogId:'status'});
    }
    overlay.setOpener(wb.scene().invocation.outcomeNode);
    overlay.dispatch({type:'outcome',terminal:true,outcome:snapshot.result,active:true});
    wb.publish('trace.mode','OBSERVED');
    wb.stage().querySelectorAll('.live-active').forEach(function(n){n.classList.remove('live-active');});
    var node=document.getElementById(wb.scene().invocation.outcomeNode);
    if(node)node.classList.add(snapshot.presentation?'live-result':'live-uncertain');
    selector.disabled=false;
    message(snapshot.presentation?'Outcome received · select the outcome node to reopen it.':'Run ended · inspect the reported status.');
    document.documentElement.setAttribute('data-sfx-run-result',JSON.stringify({runId:run.runId,state:snapshot.state,subject:run.capability.subject,presented:!!snapshot.presentation}));
    window.SFX_WORKBENCH_RUN={runId:run.runId,events:run.events,snapshot:snapshot};
  }
  window.addEventListener('sfx-entity-selected',function(event){
    var capability=current(); if(!capability)return;
    if(active && !['CLOSED','OUTCOME'].includes(active.overlay.state().state))return;
    var ids=wb.scene().invocation;
    if(event.detail.entity.id===ids.inputNode)openInput(capability);
    else if(event.detail.entity.id===ids.outcomeNode && retained[capability.subject]) {
      active=retained[capability.subject]; showOutcome(active,true);
    }
  });
  window.addEventListener('resize',function(){if(active)active.overlay.reposition();});
  window.addEventListener('sfx-viewport-resized',function(){if(active)active.overlay.reposition();});
  wb.mount().addEventListener('scroll',function(){if(active)active.overlay.reposition();});
  fetch(scriptConfig.invocationManifest).then(function(r){return r.json();}).then(function(value){
    manifest=value;
    var subject=new URL(location.href).searchParams.get('capability');
    var saved; try{saved=JSON.parse(sessionStorage.getItem(pendingKey)||'null');}catch(_){}
    if(saved && saved.command)subject=saved.command.subject;
    var capability=manifest.capabilities.find(function(c){return c.subject===subject;});
    if(saved && saved.command && capability){
      window.addEventListener('sfx-scene-loaded',async function resume(){
        if(!wb.scene()||wb.scene().sceneId!==capability.sceneId)return;
        window.removeEventListener('sfx-scene-loaded',resume);
        if(!saved.runId){
          await openInput(capability);
          if(!active || active.capability!==capability)return;
          active.command=saved.command;selector.disabled=true;fieldsEnabled(active.content,false);
          active.overlay.dispatch({type:'submit',requestId:saved.command.requestId});
          var status=active.content.querySelector('[data-component-id="dialog-status"]');
          showUncertain(active,function(text){status.textContent=text;});
          return;
        }
        var overlay=makeOverlay(capability,capability.inputAnchor);
        active={capability:capability,overlay:overlay,command:saved.command,runId:saved.runId,cursor:0,events:[],selection:{scenarioId:wb.scene().identities.scenarioId}};
        overlay.dispatch({type:'open'});overlay.dispatch({type:'submit',requestId:saved.command.requestId});overlay.dispatch({type:'admitted',runId:saved.runId});
        selector.disabled=true;wb.publish('trace.mode','LIVE');poll(active);
      });
    }else message('Choose a “Run capability” source view, then select its input component.');
    if(capability){selector.value=capability.sceneId;selector.dispatchEvent(new Event('change',{bubbles:true}));}
  }).catch(function(){message('Capability experiences could not be loaded.');});
})();
