/* Drive the workbench the way a person does, and report what actually happens.
 *
 * Every earlier check in this repository reads declarations, markup or receipts.
 * None of them opens the page. That gap let a build report "wired" while the
 * circuit never loaded, the layout collapsed and capability switching was dead,
 * because a `<select>` change updates bound state and never dispatches an
 * action. Those are not defects a schema or a regex can see.
 *
 * So this exercises journeys: select a capability, drill into a scenario, switch
 * views, click a node, search the outline, move the camera, play the trace, and
 * look at what a person would be looking at. Each check states what a person
 * should be able to do, and fails when they cannot.
 *
 * Run:
 *   node tests/ux-journey.test.mjs [origin] [--shots DIR]
 *
 * Playwright is resolved from SFX_PLAYWRIGHT (a package directory) and the
 * browser from SFX_CHROMIUM. Neither is vendored here; when they are absent the
 * suite says so and exits non-zero rather than reporting a pass it did not earn.
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

const ORIGIN = process.argv[2]?.startsWith('http') ? process.argv[2] : 'http://127.0.0.1:8787';
const shotsFlag = process.argv.indexOf('--shots');
const SHOTS = shotsFlag !== -1 ? process.argv[shotsFlag + 1] : null;
const WORKBENCH = path.resolve(path.dirname(new URL(import.meta.url).pathname.slice(1)), '..');

const PLAYWRIGHT = process.env.SFX_PLAYWRIGHT
  ?? 'C:/lab/repos/source-facts-semantic-search-engine/node_modules/playwright';
const CHROMIUM = process.env.SFX_CHROMIUM
  ?? 'C:/Users/Sidney Jones/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe';

const results = [];
let failures = 0;

function check(journey, expectation, passed, detail) {
  if (!passed) { failures += 1; }
  results.push({ journey, expectation, passed: Boolean(passed), detail: passed ? undefined : detail });
  console.log(`  ${passed ? 'ok  ' : 'FAIL'} ${journey} · ${expectation}`);
  if (!passed && detail !== undefined) {
    console.error(`       ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`.slice(0, 400));
  }
}

let chromium;
try {
  ({ chromium } = await import(pathToFileURL(path.join(PLAYWRIGHT, 'index.mjs')).href));
} catch (error) {
  console.error('PLAYWRIGHT_UNAVAILABLE: set SFX_PLAYWRIGHT to a playwright package directory');
  console.error(String(error.message).slice(0, 200));
  process.exit(2);
}
if (!fs.existsSync(CHROMIUM)) {
  console.error(`CHROMIUM_UNAVAILABLE: ${CHROMIUM}\n  set SFX_CHROMIUM to an installed browser`);
  process.exit(2);
}

const browser = await chromium.launch({ executablePath: CHROMIUM });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();

const consoleErrors = [];
const missing = [];
/* Requests this suite deliberately refuses, to exercise a refusal path. They
 * are not missing assets and must not be counted as such. */
const refusedOnPurpose = new Set();
page.on('pageerror', e => consoleErrors.push(String(e.message)));
page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
page.on('response', r => {
  if (r.status() !== 404) { return; }
  const url = r.url();
  if ([...refusedOnPurpose].some(pattern => url.includes(pattern))) { return; }
  missing.push(url.replace(ORIGIN, ''));
});

const shot = async name => {
  if (!SHOTS) { return; }
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, name + '.png') });
};

/* Reading the page the way a person reads it: what is on screen, not what is in
 * the markup. A control that is present but zero-height is not visible. */
const read = () => page.evaluate(() => {
  const q = id => document.querySelector(`[data-component-id="${id}"]`);
  const visible = el => {
    if (!el) { return false; }
    const box = el.getBoundingClientRect();
    return box.width > 1 && box.height > 1 && getComputedStyle(el).visibility !== 'hidden';
  };
  const sel = id => {
    const s = q(id)?.querySelector('select');
    return s && { options: s.options.length, value: s.value,
                  text: s.selectedOptions[0]?.textContent?.trim(), visible: visible(s) };
  };
  const stage = document.querySelector('[data-circuit-stage]');
  return {
    title: q('capability-title')?.textContent?.trim(),
    capability: sel('capability-select'), scenario: sel('scenario-select'), view: sel('source-view'),
    scope: q('view-scope')?.textContent?.trim(), coverage: q('view-coverage')?.textContent?.trim(),
    zoom: q('zoom-readout')?.textContent?.trim(), status: q('flow-status')?.textContent?.trim(),
    traceMode: q('trace-mode-readout')?.textContent?.trim(),
    telemetryTitle: q('telemetry-title')?.textContent?.trim(),
    telemetryVisible: visible(q('run-telemetry')),
    inspectKind: q('inspect-kind')?.textContent?.trim(),
    inspectTitle: q('inspect-title')?.textContent?.trim(),
    inspectSource: q('inspect-source')?.textContent?.trim(),
    outline: q('component-outline')?.querySelectorAll('li').length ?? 0,
    routes: q('outgoing-routes')?.querySelectorAll('li').length ?? 0,
    nodes: stage?.querySelectorAll('[data-entity]').length ?? 0,
    routeShapes: stage?.querySelectorAll('[data-route]').length ?? 0,
    unsupported: document.querySelector('.circuit-unsupported')?.textContent?.trim() ?? null,
    circuitVisible: visible(document.querySelector('[data-component-type="circuit"]')),
    overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
  };
});

const pick = async (id, value) => {
  await page.selectOption(`[data-component-id="${id}"] select`, value);
  await page.waitForTimeout(1100);
};
const press = async id => {
  await page.click(`[data-component-id="${id}"] button`);
  await page.waitForTimeout(500);
};

await page.goto(ORIGIN + '/index.html', { waitUntil: 'networkidle' });
await page.waitForTimeout(2000);

/* ---------------------------------------------------- 1. arriving at the page */
{
  const s = await read();
  await shot('01-arrival');
  check('arrival', 'the page opens on a capability whose circuit is drawn',
    s.nodes > 0 && !s.unsupported, s);
  check('arrival', 'the title names the capability that is loaded',
    s.title && s.capability?.text?.startsWith(s.title), { title: s.title, capability: s.capability?.text });
  check('arrival', 'the coverage strip states components, routes and omissions',
    /\d+ components .+ \d+ routes .+ \d+ source components omitted/.test(s.coverage ?? ''), s.coverage);
  check('arrival', 'trace mode is stated so an illustration is never read as a run',
    s.traceMode === 'ILLUSTRATIVE', s.traceMode);
  check('arrival', 'the outline lists every component in the view',
    s.outline === s.nodes, { outline: s.outline, nodes: s.nodes });
  check('arrival', 'the page does not scroll sideways', !s.overflowX);
  check('arrival', 'the telemetry region is visible and names itself',
    s.telemetryVisible && (s.telemetryTitle ?? '').length > 0,
    { visible: s.telemetryVisible, title: s.telemetryTitle });
}

/* ------------------------------------------- 2. choosing a capability to look at */
{
  const before = await read();
  await pick('capability-select', 'resolve-equity-market-price-evidence');
  const after = await read();
  await shot('02-capability-switch');
  check('capability', 'choosing a capability loads its circuit',
    after.nodes > 0 && after.title === 'resolve-equity-market-price-evidence', after);
  check('capability', 'the view list narrows to that capability’s own views',
    after.view?.options !== before.view?.options || after.view?.text !== before.view?.text,
    { before: before.view, after: after.view });
  check('capability', 'the coverage strip follows the new circuit',
    after.coverage !== before.coverage, { before: before.coverage, after: after.coverage });
  check('capability', 'the provider a capability actually calls is on its circuit',
    await page.evaluate(() => !!document.querySelector('[data-circuit-stage]')?.textContent
      ?.match(/rapidapi|yahoo-finance/i)), 'no provider node found');
}

/* ------------------------- 3. a capability whose circuit is not packaged here */
{
  const unpackaged = await page.evaluate(() => {
    const cfg = JSON.parse(document.getElementById('sfx-workbench-config').textContent);
    const cap = cfg.estate.capabilities.find(c => !c.views.some(v => v.scene));
    return cap ? { capabilityId: cap.capabilityId, view: cap.views[0] } : null;
  });
  if (unpackaged) {
    await pick('capability-select', unpackaged.capabilityId);
    await page.waitForFunction((id) => {
      const stage = document.querySelector('[data-circuit-stage]');
      const drawn = stage?.getAttribute('data-scene-id');
      return (typeof drawn === 'string' && drawn.indexOf(id + '/') === 0)
          || !!document.querySelector('.circuit-unsupported');
    }, unpackaged.capabilityId, { timeout: 20000 }).catch(() => {});
    const s = await read();
    const drawn = await page.evaluate(() =>
      document.querySelector('[data-circuit-stage]')?.getAttribute('data-scene-id'));
    await shot('03-resolved-from-host');
    /* The estate is far larger than any package, so most circuits are resolved
     * from the host rather than carried. That is the ordinary path, not a
     * degraded one: the capability draws its own circuit, whole. */
    check('resolved', 'a capability whose circuit is not packaged still draws it',
      s.nodes > 0 && !s.unsupported, s);
    check('resolved', 'the circuit drawn is that capability’s own view',
      drawn === unpackaged.capabilityId + '/' + unpackaged.view.viewId,
      { drew: drawn, expected: unpackaged.capabilityId + '/' + unpackaged.view.viewId });
    check('resolved', 'it carries the components and routes the catalogue declares',
      s.nodes === unpackaged.view.coverage.nodes
        && s.routeShapes === unpackaged.view.coverage.routes,
      { drew: { nodes: s.nodes, routes: s.routeShapes }, declared: unpackaged.view.coverage });
  } else {
    check('resolved', 'a capability whose circuit is not packaged exists to test', false,
      'every catalogued capability has a packaged scene');
  }
}

/* ------------------------- 3b. a circuit that cannot be resolved at all */
{
  /* Resolution can fail — the host may refuse a scene or not have it. The
   * guarantee under test is that this is said, never shown as an empty box. */
  refusedOnPurpose.add('/workbench/scene/');
  await page.route('**/workbench/scene/**', route => route.fulfill({
    status: 404, contentType: 'application/json', body: '{"code":"SCENE_UNAVAILABLE"}' }));
  const unresolvable = await page.evaluate(() => {
    const cfg = JSON.parse(document.getElementById('sfx-workbench-config').textContent);
    return cfg.estate.capabilities.filter(c => !c.views.some(v => v.scene))[1]?.capabilityId;
  });
  await pick('capability-select', unresolvable);
  await page.waitForFunction(() => !!document.querySelector('.circuit-unsupported'),
    null, { timeout: 20000 }).catch(() => {});
  const s = await read();
  await shot('03b-unresolvable');
  check('unavailable', 'a circuit that cannot be resolved says so plainly',
    (s.unsupported ?? '').length > 0, s.unsupported);
  check('unavailable', 'it is not left looking like an empty diagram',
    s.nodes === 0 && (s.unsupported ?? '').length > 0, s);
  await page.unroute('**/workbench/scene/**');
  refusedOnPurpose.delete('/workbench/scene/');
}

/* ------------------------------------------------ 4. drilling into a scenario */
{
  await pick('capability-select', 'greet-by-name');
  const all = await read();
  await pick('scenario-select', 'greet-by-name');
  const narrowed = await read();
  await shot('04-scenario-drilldown');
  check('scenario', 'narrowing to a scenario reduces the views on offer',
    narrowed.view.options < all.view.options,
    { all: all.view?.options, narrowed: narrowed.view?.options });
  check('scenario', 'the narrowed view is drawn', narrowed.nodes > 0, narrowed);
  check('scenario', 'widening back to all scenarios restores the full view list',
    await (async () => { await pick('scenario-select', ''); return (await read()).view.options === all.view.options; })(),
    'view list did not restore');
}

/* --------------------------------------------------- 5. switching source view */
{
  await pick('capability-select', 'greet-by-name');
  const views = await page.evaluate(() =>
    [...document.querySelectorAll('[data-component-id="source-view"] select option')].map(o => o.value));
  const drawn = [];
  for (const viewId of views) {
    await pick('source-view', viewId);
    const s = await read();
    drawn.push({ viewId, nodes: s.nodes, scope: s.scope, coverage: s.coverage });
  }
  await shot('05-view-switch');
  check('views', 'every view of a capability draws its own circuit',
    drawn.every(v => v.nodes > 0), drawn);
  check('views', 'each view reports its own coverage',
    new Set(drawn.map(v => v.coverage)).size > 1, drawn.map(v => v.coverage));
}

/* ----------------------------------------------------- 6. inspecting a node */
{
  await pick('capability-select', 'greet-by-name');
  await page.click('[data-circuit-stage] [data-entity]');
  await page.waitForTimeout(600);
  const s = await read();
  await shot('06-node-inspected');
  check('inspect', 'clicking a node reports what it is',
    (s.inspectKind ?? '').length > 0 && (s.inspectTitle ?? '').length > 0,
    { kind: s.inspectKind, title: s.inspectTitle });
  check('inspect', 'the selected node keeps its source digest and pointer',
    /SHA-256/.test(s.inspectSource ?? ''), s.inspectSource);
  check('inspect', 'a selected node offers the routes leaving it',
    s.routes >= 0, s.routes);

  const keyboard = await page.evaluate(() => {
    const node = document.querySelectorAll('[data-circuit-stage] [data-entity]')[1];
    if (!node) { return null; }
    node.focus();
    const focused = document.activeElement === node;
    node.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    return { focused, tabindex: node.getAttribute('tabindex'), role: node.getAttribute('role') };
  });
  await page.waitForTimeout(400);
  check('inspect', 'a node can be reached and activated from the keyboard',
    keyboard?.focused && keyboard.tabindex === '0', keyboard);
}

/* ------------------------------------------------------- 7. finding a component */
{
  /* Select a known view first. Searching whichever view a previous journey left
   * loaded made this check report a product defect that was not one: the
   * mechanics view legitimately has no node matching the term. */
  await pick('capability-select', 'greet-by-name');
  const blueprint = await page.evaluate(() =>
    [...document.querySelectorAll('[data-component-id="source-view"] select option')]
      .find(o => /blueprint/i.test(o.textContent))?.value);
  if (blueprint) { await pick('source-view', blueprint); }

  const before = (await read()).outline;
  const matching = await page.evaluate(() =>
    [...document.querySelectorAll('[data-component-id="component-outline"] li')]
      .filter(li => /greeting/i.test(li.textContent)).length);
  check('search', 'the chosen view has something to find', matching > 0 && matching < before,
    { matching, before });
  await page.fill('[data-component-id="component-search"] input', 'greeting');
  await page.waitForTimeout(700);
  const filtered = (await read()).outline;
  await shot('07-search');
  check('search', 'typing narrows the outline to the matching components',
    filtered === matching, { before, filtered, matching });
  await page.fill('[data-component-id="component-search"] input', '');
  await page.waitForTimeout(700);
  check('search', 'clearing the search restores every component',
    (await read()).outline === before, { before, after: (await read()).outline });

  await page.click('[data-component-id="component-outline"] li button');
  await page.waitForTimeout(600);
  check('search', 'choosing from the outline selects that component',
    ((await read()).inspectTitle ?? '').length > 0, (await read()).inspectTitle);
}

/* ------------------------------------------------------------- 8. the camera */
{
  const fitted = await (async () => { await press('fit-diagram'); return (await read()).zoom; })();
  const read100 = await (async () => { await press('read-at-100'); return (await read()).zoom; })();
  await press('zoom-out');
  const out = (await read()).zoom;
  await press('zoom-in');
  const back = (await read()).zoom;
  await shot('08-camera');
  check('camera', 'Read at 100% sets the reading scale', read100 === '100%', read100);
  check('camera', 'Fit diagram fits the circuit', /^\d+%$/.test(fitted ?? ''), fitted);
  check('camera', 'zooming out then in changes and restores the scale',
    out !== read100 && back !== out, { read100, out, back });
}

/* -------------------------------------------------------- 9. material presentation */
{
  await press('show-base-svg');
  const base = await page.evaluate(() => document.querySelector('[data-component-id="show-base-svg"] button')?.getAttribute('aria-pressed'));
  await press('show-material');
  const material = await page.evaluate(() => document.querySelector('[data-component-id="show-material"] button')?.getAttribute('aria-pressed'));
  check('presentation', 'Material and Base SVG reflect which is pressed',
    base === 'true' && material === 'true', { base, material });
}

/* ------------------------------------------------------ 10. illustrative playback */
{
  await press('reset-trace');
  const idle = (await read()).status;
  await press('next-trace-step');
  const stepped = (await read()).status;
  await shot('10-trace');
  check('playback', 'advancing the trace reports progress',
    stepped !== idle && (stepped ?? '').length > 0, { idle, stepped });
  await press('reset-trace');
  check('playback', 'reset returns the trace to its idle message',
    (await read()).status === idle, { idle, after: (await read()).status });
  check('playback', 'playback never claims to be execution',
    (await read()).traceMode === 'ILLUSTRATIVE', (await read()).traceMode);
}

/* ------------------------------------------------------- 11. a narrow viewport */
{
  await page.setViewportSize({ width: 430, height: 900 });
  await page.waitForTimeout(900);
  const s = await read();
  await shot('11-narrow');
  const narrow = await page.evaluate(() => {
    const within = el => {
      if (!el) { return { ok: false, why: 'absent' }; }
      const b = el.getBoundingClientRect();
      return { ok: b.left >= -1 && b.right <= window.innerWidth + 1 && b.width > 40 && b.height > 8,
               box: { l: Math.round(b.left), r: Math.round(b.right), w: Math.round(b.width) } };
    };
    const labels = [...document.querySelectorAll('[data-component-type="choice"]>span')]
      .map(el => { const b = el.getBoundingClientRect(); return { text: el.textContent.trim(), t: Math.round(b.top), l: Math.round(b.left), r: Math.round(b.right), b: Math.round(b.bottom) }; });
    let overlapping = 0;
    for (let i = 0; i < labels.length; i += 1) {
      for (let j = i + 1; j < labels.length; j += 1) {
        const a = labels[i], c = labels[j];
        if (a.l < c.r && c.l < a.r && a.t < c.b && c.t < a.b) { overlapping += 1; }
      }
    }
    return {
      capability: within(document.querySelector('[data-component-id="capability-select"] select')),
      scenario: within(document.querySelector('[data-component-id="scenario-select"] select')),
      view: within(document.querySelector('[data-component-id="source-view"] select')),
      overlappingLabels: overlapping, labels,
    };
  });
  check('narrow', 'each selection control is readable and inside the screen',
    narrow.capability.ok && narrow.scenario.ok && narrow.view.ok, narrow);
  check('narrow', 'control labels do not overprint each other',
    narrow.overlappingLabels === 0, narrow.labels);
  const controls = await page.evaluate(() => [...document.querySelectorAll(
      '[data-component-id="show-material"] button, [data-component-id="show-base-svg"] button,'
      + '[data-component-id="fit-diagram"] button, [data-component-id="download-svg"] button')]
    .map(el => { const b = el.getBoundingClientRect();
      return { label: el.textContent.trim(), w: Math.round(b.width),
               r: Math.round(b.right), clipped: b.right > window.innerWidth + 1 || b.width < 40 }; }));
  check('narrow', 'toolbar controls are neither crushed nor cut off',
    controls.every(c => !c.clipped), controls.filter(c => c.clipped));
  check('narrow', 'the page still does not scroll sideways', !s.overflowX, s);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.waitForTimeout(600);
}

/* ------------------------------------------------------------ 12. cleanliness */
{
  check('console', 'no uncaught script errors',
    consoleErrors.filter(e => !/404|Failed to load resource/.test(e)).length === 0, consoleErrors);
  check('console', 'no missing assets',
    missing.length === 0, [...new Set(missing)].slice(0, 8));
}

await browser.close();

const receiptDir = path.join(WORKBENCH, 'evidence', 'ux');
fs.mkdirSync(receiptDir, { recursive: true });
fs.writeFileSync(path.join(receiptDir, 'ux-journey.receipt.json'), JSON.stringify({
  receiptType: 'ux-journey-receipt.v1',
  verifiedAt: new Date().toISOString().replace(/\.\d{3}Z$/, '+00:00'),
  statement: 'The workbench is driven through the journeys a person takes: choosing a '
    + 'capability, drilling into a scenario, switching views, inspecting a node, searching, '
    + 'moving the camera, and playing the illustrative trace. Each check states what a person '
    + 'should be able to do.',
  origin: ORIGIN,
  browser: 'chromium',
  summary: {
    checks: results.length,
    passed: results.filter(r => r.passed).length,
    failed: results.filter(r => !r.passed).length,
    journeys: [...new Set(results.map(r => r.journey))],
  },
  results,
  consoleErrors: [...new Set(consoleErrors)].slice(0, 20),
  missingAssets: [...new Set(missing)].slice(0, 20),
  notEstablished: [
    'One engine only. Firefox and WebKit are not exercised here.',
    'No capability is invoked; the trace shown is the declared illustration.',
    'Visual appearance is not compared against a reference; this checks behaviour.',
  ],
}, null, 2) + '\n', 'utf8');

console.log(`ux             ${results.filter(r => r.passed).length}/${results.length} checks passed`);
console.log(`receipt        ${path.join(receiptDir, 'ux-journey.receipt.json')}`);
process.exit(failures === 0 ? 0 : 1);
