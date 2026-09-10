'use strict';
// A finite inspection of every declared route, including all branch alternatives.
// Each edge is visited once; cycles are illustrated once, never executed.
function planTrace(graph, preferred) {
  const visited = new Set(), reached = new Set(), waves = [];
  const flow = graph.edges.filter(e => e.kind !== 'provider-binding');
  const bindings = graph.edges.filter(e => e.kind === 'provider-binding'), byPort = new Map();
  for (const edge of bindings) {
    if (!byPort.has(edge.target)) byPort.set(edge.target, []);
    byPort.get(edge.target).push(edge);
  }
  const portBindings = ids => [...new Set(ids)].flatMap(id => (byPort.get(id) || []).filter(e => !visited.has(e.id)));
  const appendWave = edges => {
    if (!edges.length) return;
    for (const edge of edges) { visited.add(edge.id); reached.add(edge.source); reached.add(edge.target); }
    waves.push(edges.map(edge => ({edgeId:edge.id,source:edge.source,target:edge.target,kind:edge.kind})));
  };
  const nodes=new Map(graph.nodes.map(n=>[n.id,n])),out=new Map(),required=new Map(),targets=new Set();
  for(const edge of flow){
    if(!out.has(edge.source))out.set(edge.source,[]);out.get(edge.source).push(edge);targets.add(edge.target);
    if(edge.kind==='CONVERGENCE_REQUIREMENT'||graph.kind==='expression'&&['argument-dependency','conditional-argument'].includes(edge.kind)){
      if(!required.has(edge.target))required.set(edge.target,[]);required.get(edge.target).push(edge);
    }
  }
  const outgoing = id => (out.get(id)||[]).filter(e => !visited.has(e.id));
  const eligible = id => {
    const node = nodes.get(id),requirements=required.get(id)||[];
    return node?.kind !== 'convergence'&&graph.kind!=='expression' || requirements.every(e => visited.has(e.id));
  };
  const activate = (id, next) => {
    if(!eligible(id))return;
    for(const edge of outgoing(id))if(!next.some(e=>e.id===edge.id))next.push(edge);
  };
  const roots = graph.nodes.filter(n => !targets.has(n.id));
  const start = preferred || roots.find(n => outgoing(n.id).length)?.id || graph.nodes[0]?.id;
  let frontier=[];
  if(preferred&&start)activate(start,frontier);
  else for(const root of roots)activate(root.id,frontier);
  let remainingFlow = flow.length;
  while (remainingFlow) {
    if (!frontier.length) {
      const next=flow.find(e=>!visited.has(e.id)&&eligible(e.source)&&reached.has(e.source)) || flow.find(e=>!visited.has(e.id)&&eligible(e.source)) || flow.find(e=>!visited.has(e.id));
      if (!next) break;
      activate(next.source,frontier);
      if(!frontier.length)frontier.push(next);
    }
    const batch=frontier.filter(e=>!visited.has(e.id));frontier=[];
    if(!batch.length)continue;
    // A root/selected port needs its implementation binding before it continues.
    appendWave(portBindings(batch.map(edge => edge.source)));
    // Incoming flow and the corresponding provider binding arrive at the port together.
    // Bindings do not activate other ports that happen to share this provider.
    appendWave([...batch, ...portBindings(batch.map(edge => edge.target))]);
    remainingFlow -= batch.length;
    for(const edge of batch)activate(edge.target,frontier);
  }
  // Binding-only components have no flow arrival to accompany.
  appendWave(bindings.filter(edge => !visited.has(edge.id)));
  for (const node of graph.nodes) if (!reached.has(node.id)) waves.push([{nodeId:node.id}]);
  return waves;
}
if (typeof module === 'object' && module.exports) module.exports = {planTrace};
else
(() => {
  const $ = (id) => document.getElementById(id),
    catalog = window.ESTATE_TOPOLOGY_CATALOG,
    entry = window.ESTATE_TOPOLOGY_ENTRY;
  let view = null,
    scale = 1,
    selected = null,
    running = false,
    token = 0,
    loadToken = 0,
    visited = new Set(),
    visitedNodes = new Set(),
    trace = [],
    cursor = 0,
    playToken = 0,
    parallelCamera = false;
  const speed = document.createElement('select');
  speed.id = 'trace-speed'; speed.setAttribute('aria-label', 'Trace speed');
  for (const value of [1,4,16,64]) { const o=document.createElement('option');o.value=value;o.textContent=value+'×';speed.append(o); }
  const speedLabel=document.createElement('label');speedLabel.className='trace-control';speedLabel.append('Speed ',speed);
  const follow=document.createElement('input');follow.type='checkbox';follow.checked=true;follow.id='trace-follow';
  const followLabel=document.createElement('label');followLabel.className='trace-control';followLabel.append(follow,' Follow flow');
  $('play').parentElement.insertBefore(speedLabel,$('flow-status'));
  $('play').parentElement.insertBefore(followLabel,$('flow-status'));
  $('next').textContent='Next step';
  const report = () =>
    parent.postMessage(
      { type: 'sidefx-circuit-height', height: document.body.scrollHeight },
      '*',
    );
  const names = {
    blueprint: 'Blueprint authority',
    native: 'Native execution graph',
    operations: 'Declared operation flow',
    expression: 'Mechanic dependencies',
  };
  for (const kind of ['blueprint', 'operations', 'native', 'expression']) {
    const group = document.createElement('optgroup');
    group.label = names[kind];
    for (const v of catalog.views.filter((v) => v.kind === kind)) {
      const option = document.createElement('option');
      option.value = v.id;
      option.textContent =
        v.label +
        ' · ' +
        v.analytics.nodes +
        ' components / ' +
        v.analytics.edges +
        ' routes';
      group.append(option);
    }
    if (group.children.length) $('view').append(group);
  }
  function stop() {
    if(running)$('flow-status').textContent=`Trace paused · ${visited.size} / ${view.edges.length} routes`;
    running = false;
    parallelCamera=false;
    token++;
    playToken++;
    $('play').textContent = trace.length && cursor < trace.length ? 'Resume trace' : 'Trace flow';
  }
  function sizing() {
    if (!view) return;
    const svg = $('stage').firstElementChild;
    svg.style.width = view.layout.width * scale + 'px';
    svg.style.height = view.layout.height * scale + 'px';
    $('zoom').textContent = Math.round(scale * 100) + '%';
  }
  function fit() {
    scale = Math.min(
      1,
      ($('viewport').clientWidth - 40) / view.layout.width,
      ($('viewport').clientHeight - 40) / view.layout.height,
    );
    sizing();
  }
  function outline() {
    const query = $('search').value.toLowerCase();
    $('outline').replaceChildren();
    for (const n of view.nodes.filter((n) =>
      (n.identity + ' ' + n.label + ' ' + n.detail)
        .toLowerCase()
        .includes(query),
    )) {
      const button = document.createElement('button'),
        small = document.createElement('small');
      small.textContent = n.kind;
      button.append(small, document.createTextNode(n.label));
      button.onclick = () => {
        inspect(n.id);
        scale = Math.max(scale, 0.7);
        sizing();
        const [x, y, w, h] = view.layout.boxes[n.id];
        $('viewport').scrollLeft =
          (x + w / 2) * scale - $('viewport').clientWidth / 2;
        $('viewport').scrollTop =
          (y + h / 2) * scale - $('viewport').clientHeight / 2;
      };
      $('outline').append(button);
    }
  }
  function inspect(id) {
    stop();
    trace=[];cursor=0;$('play').textContent='Trace flow';
    const node = view.nodes.find((n) => n.id === id),
      edge = view.edges.find((e) => e.id === id),
      item = node || edge;
    if (!item) return;
    selected = node?.id ?? null;
    $('kind').textContent = node?.kind ?? edge.kind;
    $('title').textContent = item.label;
    $('detail').textContent =
      node?.facts.responsibility ?? node?.detail ?? edge.kind;
    $('facts').textContent = JSON.stringify(
      { identity: item.identity, ...item.facts },
      null,
      2,
    );
    const src = node?.source ?? edge.provenance;
    $('source').textContent =
      src.label + ' · SHA-256 ' + src.sha256 + ' · ' + src.pointer;
    for (const el of $('stage').querySelectorAll('.selected'))
      el.classList.remove('selected');
    $(id)?.classList.add('selected');
    choices();
  }
  const traversable = (e) => e.kind !== 'provider-binding';
  function outgoing() {
    return view.edges.filter((e) => e.source === selected && traversable(e));
  }
  function choices() {
    $('routes').replaceChildren();
    if (!selected) return;
    const edges = outgoing(),
      node = view.nodes.find((n) => n.id === selected);
    if (node.kind === 'convergence')
      $('flow-status').textContent =
        'Convergence: inspect its declared requirements before following the continuation.';
    else if (edges.length > 1)
      $('flow-status').textContent =
        node.kind === 'fan-out'
          ? 'All fan-out members are declared. Choose a member to inspect.'
          : 'Choose a declared route to follow. Alternatives are not executed by this diagram.';
    else if (!edges.length)
      $('flow-status').textContent = 'End of this declared path.';
    for (const e of edges) {
      const button = document.createElement('button');
      button.textContent =
        e.label + ' → ' + view.nodes.find((n) => n.id === e.target).label;
      button.onclick = () => {
        stop();
        travel(e);
      };
      $('routes').append(button);
    }
  }
  function showNode(id) {
    selected=id;visitedNodes.add(id);
    for (const el of $('stage').querySelectorAll('.selected')) el.classList.remove('selected');
    $(id)?.classList.add('selected');
    const n=view.nodes.find(n=>n.id===id);
    $('kind').textContent=n.kind;$('title').textContent=n.label;
    $('detail').textContent=n.facts.responsibility ?? n.detail;
    $('facts').textContent=JSON.stringify({identity:n.identity,...n.facts},null,2);
    $('source').textContent=n.source.label+' · SHA-256 '+n.source.sha256+' · '+n.source.pointer;
  }
  function followPoint(p) {
    if (parallelCamera || !follow.checked || matchMedia('(prefers-reduced-motion:reduce)').matches) return;
    const viewport=$('viewport');
    if (view.layout.width*scale > viewport.clientWidth) viewport.scrollLeft=Math.max(0,p.x*scale-viewport.clientWidth/2);
    if (view.layout.height*scale > viewport.clientHeight) viewport.scrollTop=Math.max(0,p.y*scale-viewport.clientHeight/2);
  }
  async function travel(edge) {
    const mine = token;
    const path = $(edge.id)?.querySelector('.route-path');
    if (!path) throw new Error('Trace route geometry missing: '+edge.id);
    visitedNodes.add(edge.source);
    $(edge.id).classList.add('active-route');
    $(edge.id).dataset.traceToken=String(mine);
    const ball = document.createElementNS(
      'http://www.w3.org/2000/svg',
      'circle',
    );
    ball.setAttribute('r', '6');
    ball.setAttribute('fill', '#f2ffff');
    ball.setAttribute('stroke', '#89b8c3');
    ball.setAttribute('stroke-width', '2');
    $('stage').firstElementChild.append(ball);
    const length = path.getTotalLength(),
      reduce = matchMedia('(prefers-reduced-motion:reduce)').matches,
      start = performance.now();
    await new Promise((resolve) => {
      const frame = (now) => {
        const t = reduce ? 1 : Math.min(1, (now - start) / (800 / Number(speed.value))),
          p = path.getPointAtLength(length * t);
        ball.setAttribute('cx', p.x);
        ball.setAttribute('cy', p.y);
        followPoint(p);
        if (t === 1 || mine !== token) resolve();
        else requestAnimationFrame(frame);
      };
      requestAnimationFrame(frame);
    });
    ball.remove();
    if (mine !== token) {if(!visited.has(edge.id)&&$(edge.id)?.dataset.traceToken===String(mine))$(edge.id)?.classList.remove('active-route');return false;}
    visited.add(edge.id);
    showNode(edge.target);
    if(!running)choices();
    return true;
  }
  async function step(play) {
    if (!view) return;
    if(!trace.length || cursor >= trace.length){
      trace=planTrace(view,trace.length?undefined:selected);cursor=0;visited.clear();visitedNodes.clear();
      for(const el of $('stage').querySelectorAll('.active-route'))el.classList.remove('active-route');
    }
    const mine=++playToken;token++;running=play;$('play').textContent=play?'Pause trace':'Trace flow';
    delete $('flow-status').dataset.state;
    $('routes').replaceChildren();
    do {
      const batch=trace[cursor]?.filter(item=>item.edgeId?!visited.has(item.edgeId):!visitedNodes.has(item.nodeId));if(!batch)break;
      parallelCamera=batch.length>1;
      if(parallelCamera&&follow.checked){
        const boxes=batch.flatMap(item=>[view.layout.boxes[item.source],view.layout.boxes[item.target]]);
        const left=Math.min(...boxes.map(b=>b[0])),top=Math.min(...boxes.map(b=>b[1])),right=Math.max(...boxes.map(b=>b[0]+b[2])),bottom=Math.max(...boxes.map(b=>b[1]+b[3]));
        const viewport=$('viewport');scale=Math.min(1,(viewport.clientWidth-60)/(right-left),(viewport.clientHeight-60)/(bottom-top));sizing();
        viewport.scrollLeft=Math.max(0,(left+right)*scale/2-viewport.clientWidth/2);viewport.scrollTop=Math.max(0,(top+bottom)*scale/2-viewport.clientHeight/2);
      }
      const edges=batch.filter(item=>item.edgeId);
      $('flow-status').textContent=batch.length>1?`Tracing ${visited.size+1}–${visited.size+edges.length} / ${view.edges.length} · ${batch.length} parallel branches`:`Tracing ${Math.min(visited.size+1,view.edges.length)} / ${view.edges.length} · ${batch[0]?.kind??'isolated component'}`;
      const completed=await Promise.all(batch.map(async item=>{
        if(item.edgeId)return travel(view.edges.find(e=>e.id===item.edgeId));
        showNode(item.nodeId);const [x,y,w,h]=view.layout.boxes[item.nodeId];followPoint({x:x+w/2,y:y+h/2});
        await new Promise(resolve=>requestAnimationFrame(resolve));return true;
      }));
      if(mine!==playToken)return;
      parallelCamera=false;
      if(completed.some(ok=>!ok))return;
      for(const item of batch)$(item.target||item.nodeId)?.classList.add('selected');
      cursor++;
    }while(play && running && cursor<trace.length);
    if(mine!==playToken)return;
    if(cursor===trace.length){
      stop();$('play').textContent='Replay trace';
      $('flow-status').textContent=`Trace complete · ${visited.size} / ${view.edges.length} routes · ${visitedNodes.size} / ${view.nodes.length} components. All declared alternatives inspected.`;
      $('flow-status').dataset.state='complete';
    }else{
      running=false;$('play').textContent='Resume trace';
      $('flow-status').textContent=`Trace paused · ${visited.size} / ${view.edges.length} routes`;
    }
  }
  async function load(id) {
    stop();
    const summary = catalog.views.find((v) => v.id === id);
    if (!summary) return;
    const mine = ++loadToken;
    $('scope').textContent = 'Loading source graph…';
    const script = document.createElement('script');
    script.src = summary.url;
    script.onload = () => {
      script.remove();
      if (mine !== loadToken) return;
      view = window.ESTATE_TOPOLOGY_VIEW;
      if (view.id !== id) {
        $('scope').textContent = 'Source graph identity mismatch';
        return;
      }
      selected = null;
      visited.clear();
      visitedNodes.clear();trace=[];cursor=0;delete $('flow-status').dataset.state;
      $('play').textContent='Trace flow';
      const waveCount=planTrace(view).length;
      speed.value=waveCount>500?'16':waveCount>80?'4':'1';
      $('stage').innerHTML = view.svg;
      for (const el of $('stage').querySelectorAll(
        '[data-entity],[data-route]',
      )) {
        el.onclick = () => inspect(el.id);
        el.onkeydown = (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            inspect(el.id);
          }
        };
      }
      const a = view.analytics;
      $('scope').textContent = names[view.kind];
      $('coverage').textContent =
        a.nodes +
        ' components · ' +
        a.edges +
        ' routes · ' +
        a.omittedSourceNodes +
        ' source components omitted';
      $('findings').textContent = view.findings
        .map((f) => f.code + ': ' + f.identity)
        .join(' · ');
      $('view').value = id;
      $('routes').replaceChildren();
      $('title').textContent = view.label;
      $('kind').textContent = names[view.kind];
      $('detail').textContent =
        'Select a component or route to inspect its meaning.';
      $('facts').textContent = '';
      $('source').textContent = '';
      $('flow-status').textContent =
        view.kind === 'expression'
          ? 'Arrows show named expression dependencies; conditional arguments remain distinct.'
          : 'Select a component, then follow its declared routes.';
      outline();
      fit();
      if (scale < 0.35) {
        scale = 0.7;
        sizing();
        const start = view.analytics.roots[0] || view.nodes[0].id;
        const [x, y, w, h] = view.layout.boxes[start];
        $('viewport').scrollLeft = Math.max(0, (x + w / 2) * scale - $('viewport').clientWidth / 2);
        $('viewport').scrollTop = Math.max(0, (y + h / 2) * scale - $('viewport').clientHeight / 2);
      }
      report();
    };
    script.onerror = () => {
      $('scope').textContent = 'Source graph unavailable';
      script.remove();
    };
    document.head.append(script);
  }
  $('view').onchange = (e) => load(e.target.value);
  $('fit').onclick = fit;
  $('read').onclick = () => {
    scale = 1;
    sizing();
  };
  $('plus').onclick = () => {
    scale = Math.min(2, scale * 1.3);
    sizing();
  };
  $('minus').onclick = () => {
    scale = Math.max(0.01, scale / 1.3);
    sizing();
  };
  $('search').oninput = outline;
  $('base').onclick = () => {
    document.body.classList.add('base');
    $('base').setAttribute('aria-pressed', 'true');
    $('material').setAttribute('aria-pressed', 'false');
  };
  $('material').onclick = () => {
    document.body.classList.remove('base');
    $('base').setAttribute('aria-pressed', 'false');
    $('material').setAttribute('aria-pressed', 'true');
  };
  $('play').onclick = () => (running ? stop() : step(true));
  $('next').onclick = () => {
    stop();
    step(false);
  };
  $('reset').onclick = () => {
    stop();
    visited.clear();
    visitedNodes.clear();trace=[];cursor=0;delete $('flow-status').dataset.state;
    $('play').textContent='Trace flow';
    selected = null;
    for (const el of $('stage').querySelectorAll('.selected,.active-route'))
      el.classList.remove('selected', 'active-route');
    $('routes').replaceChildren();
    $('flow-status').textContent =
      'Select a component, then follow its declared routes.';
  };
  $('download').onclick = () => {
    if (!view) return;
    const svg = new DOMParser().parseFromString(view.svg, 'image/svg+xml');
    for (const img of svg.querySelectorAll('image')) img.remove();
    const url = URL.createObjectURL(
        new Blob([new XMLSerializer().serializeToString(svg)], {
          type: 'image/svg+xml',
        }),
      ),
      a = document.createElement('a');
    a.href = url;
    a.download = catalog.capabilityId + '-' + view.id + '.svg';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  addEventListener('pagehide', stop);
  new ResizeObserver(report).observe(document.body);
  load(entry.viewId);
  report();
})();
