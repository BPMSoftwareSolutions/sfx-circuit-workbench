/* Select capabilities from across the estate in a real browser and require that
 * each one's own circuit draws, with exactly the nodes and routes the catalogue
 * declares for it. Anything on screen is not the test; the right thing on
 * screen is — a stale diagram left by the previous selection would otherwise
 * pass a check that only asks whether something is drawn. */
import playwright from 'file:///C:/lab/repos/source-facts-semantic-search-engine/node_modules/playwright/index.js';
const { chromium } = playwright;

const ORIGIN = process.argv[2] ?? process.env.SFX_PLATFORM_ORIGIN ?? 'http://127.0.0.1:3000';
const browser = await chromium.launch({ executablePath: 'C:/Users/Sidney Jones/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe' });
const page = await browser.newPage({ viewport: { width: 1500, height: 980 } });

const failures = [], problems = [];
page.on('console', m => { if (m.type() === 'error') problems.push(m.text()); });
page.on('pageerror', e => problems.push('pageerror: ' + e.message));
page.on('requestfailed', r => problems.push('requestfailed: ' + r.url()));
page.on('response', r => { if (r.status() >= 400) problems.push(r.status() + ' ' + r.url()); });

await page.goto(ORIGIN + '/workbench/index.html', { waitUntil: 'networkidle' });

const capabilities = await page.evaluate(() =>
  [...document.querySelectorAll('[data-component-id="capability-select"] select option')].map(o => o.value));

const required = ['resolve-equity-market-price-evidence', 'greet-by-name', 'say-hello-world'];
const step = Math.max(1, Math.floor(capabilities.length / 24));
const sample = [...new Set([...required.filter(c => capabilities.includes(c)),
  ...capabilities.filter((_, i) => i % step === 0)])].slice(0, 27);

const declared = (capabilityId, viewId) => page.evaluate(([id, vid]) => {
  const cfg = JSON.parse(document.getElementById('sfx-workbench-config').textContent);
  const cap = cfg.estate.capabilities.find(c => c.capabilityId === id);
  if (!cap) return null;
  const v = vid ? cap.views.find(x => x.viewId === vid) : cap.views[0];
  return v ? { viewId: v.viewId, nodes: v.coverage.nodes, routes: v.coverage.routes } : null;
}, [capabilityId, viewId]);

const read = () => page.evaluate(() => {
  const stage = document.querySelector('[data-circuit-stage]');
  return {
    nodes: stage?.querySelectorAll('[data-entity]').length ?? 0,
    routes: stage?.querySelectorAll('[data-route]').length ?? 0,
    sceneId: stage?.getAttribute('data-scene-id') ?? null,
    busy: stage?.getAttribute('aria-busy') === 'true',
    unsupported: document.querySelector('.circuit-unsupported')?.textContent?.trim() ?? null,
    views: [...document.querySelectorAll('[data-component-id="source-view"] select option')].length,
  };
});

const settle = (capabilityId) => page.waitForFunction((id) => {
  const stage = document.querySelector('[data-circuit-stage]');
  const drawn = stage?.getAttribute('data-scene-id');
  return (typeof drawn === 'string' && drawn.indexOf(id + '/') === 0)
      || !!document.querySelector('.circuit-unsupported');
}, capabilityId, { timeout: 20000 }).catch(() => {});

function check(label, want, state) {
  const right = !!want && state.sceneId === want.viewId
    && state.nodes === want.nodes && state.routes === want.routes
    && !state.unsupported && !state.busy;
  if (!right) failures.push({ label, want, got: state });
  const w = want ? want.nodes + '/' + want.routes : '?';
  console.log((right ? 'ok   ' : 'FAIL ') + label.padEnd(52)
    + ' drew ' + String(state.nodes) + '/' + String(state.routes) + ' expected ' + w
    + ' views=' + state.views + (state.unsupported ? ' :: ' + state.unsupported : ''));
  return right;
}

for (const capabilityId of sample) {
  await page.selectOption('[data-component-id="capability-select"] select', capabilityId);
  const want = await declared(capabilityId, null);
  await settle(capabilityId);
  const state = await read();
  check(capabilityId, want && { ...want, viewId: capabilityId + '/' + want.viewId }, state);
}

/* Scenario drill-down: every view of one capability, each asserted the same way. */
console.log('\n-- scenario drill-down: construct-model-role-conveyor-plan --');
await page.selectOption('[data-component-id="capability-select"] select', 'construct-model-role-conveyor-plan');
await settle('construct-model-role-conveyor-plan');
const views = await page.evaluate(() =>
  [...document.querySelectorAll('[data-component-id="source-view"] select option')].map(o => o.value));
for (const viewId of views.slice(0, 10)) {
  await page.selectOption('[data-component-id="source-view"] select', viewId);
  const want = await declared('construct-model-role-conveyor-plan', viewId);
  await page.waitForFunction((vid) => document.querySelector('[data-circuit-stage]')
    ?.getAttribute('data-scene-id')?.endsWith('/' + vid), viewId, { timeout: 20000 }).catch(() => {});
  const state = await read();
  check('view ' + viewId, want && { ...want, viewId: 'construct-model-role-conveyor-plan/' + want.viewId }, state);
}

/* Selecting a node inside a host-resolved circuit must still inspect it. */
await page.click('[data-circuit-stage] [data-entity]');
const inspected = await page.evaluate(() => ({
  outline: document.querySelectorAll('[data-component-id="component-outline"] li').length,
  routes: document.querySelectorAll('[data-component-id="outgoing-routes"] li').length,
}));
console.log('\nselection on a resolved circuit: outline=' + inspected.outline + ' routes=' + inspected.routes);
if (inspected.outline === 0) failures.push({ label: 'selection', inspected });

await page.screenshot({ path: process.argv[3] ?? 'evidence/ux/estate-sweep.png' });
const noisy = [...new Set(problems)];
console.log('\nnetwork/console problems: ' + noisy.length);
for (const e of noisy.slice(0, 6)) console.log('  ' + e);
console.log('failures: ' + failures.length);
for (const f of failures.slice(0, 8)) console.log('  ' + JSON.stringify(f));
await browser.close();
process.exit(failures.length ? 1 : 0);
