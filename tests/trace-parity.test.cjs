/* Behavioural parity for the illustrative trace planner.
 *
 * The frozen reference viewer exports planTrace under Node, so the reimplemented
 * planner can be compared against it wave for wave, on the real fixtures, with
 * no browser involved. This is the only behavioural evidence available at M1,
 * so it is worth being strict: the comparison is on exact wave structure and
 * exact route order within each wave, not merely on coverage totals.
 *
 * Run:  node tests/trace-parity.test.cjs
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const WORKBENCH = path.resolve(__dirname, "..");
const reference = require(path.join(WORKBENCH, "fixtures/baseline/viewer/viewer.js"));
const ported = require(path.join(WORKBENCH, "runtime/illustrative-trace.js"));

/* The reference planner reads the legacy product shape. Rebuild exactly that
 * shape from the scene, so any difference is the planner's, not the adapter's. */
function legacyGraph(scene) {
  return {
    kind: scene.identities.viewKind,
    nodes: scene.graph.nodes.map((n) => ({ id: n.id, kind: n.kind })),
    edges: scene.graph.routes.map((r) => ({
      id: r.id,
      source: r.source,
      target: r.target,
      kind: r.traversable === false ? "provider-binding" : r.kind,
    })),
  };
}

function normalise(waves) {
  return waves.map((wave) =>
    wave.map((item) => (item.edgeId
      ? `edge:${item.edgeId}:${item.source}->${item.target}`
      : `node:${item.nodeId}`)));
}

const scenes = fs.readdirSync(path.join(WORKBENCH, "fixtures/scenes"))
  .filter((name) => name.endsWith(".scene.json"))
  .map((name) => ({
    name: name.replace(".scene.json", ""),
    scene: JSON.parse(fs.readFileSync(
      path.join(WORKBENCH, "fixtures/scenes", name), "utf8")),
  }));

assert.ok(scenes.length > 0, "no scenes to test; run the topology adapter first");

const results = [];
let failures = 0;

for (const { name, scene } of scenes) {
  const graph = legacyGraph(scene);

  const cases = [
    { label: "from declared roots", preferred: undefined },
    { label: "from first node", preferred: scene.graph.nodes[0].id },
    {
      label: "from a declared root",
      preferred: (scene.topology.roots || [])[0] || scene.graph.nodes[0].id,
    },
  ];

  for (const testCase of cases) {
    const expected = normalise(reference.planTrace(graph, testCase.preferred));
    const actual = normalise(ported.planTrace(scene, testCase.preferred));

    let passed = true;
    let detail = null;
    try {
      assert.deepStrictEqual(actual, expected);
    } catch (error) {
      passed = false;
      failures += 1;
      const firstDiff = expected.findIndex((wave, i) =>
        JSON.stringify(wave) !== JSON.stringify(actual[i]));
      detail = `wave counts ${expected.length} vs ${actual.length}; first divergence at ${firstDiff}`;
    }

    /* Coverage is a separate claim from ordering, and is asserted separately:
     * every declared route must appear exactly once across all waves. */
    const routeIds = new Set();
    let duplicated = 0;
    for (const wave of ported.planTrace(scene, testCase.preferred)) {
      for (const item of wave) {
        if (!item.edgeId) { continue; }
        if (routeIds.has(item.edgeId)) { duplicated += 1; }
        routeIds.add(item.edgeId);
      }
    }
    const complete = routeIds.size === scene.graph.routes.length && duplicated === 0;
    if (!complete) { failures += 1; passed = false; }

    results.push({
      scene: name,
      case: testCase.label,
      waves: expected.length,
      routesCovered: routeIds.size,
      routesDeclared: scene.graph.routes.length,
      duplicatedRoutes: duplicated,
      matchesReference: detail === null,
      coverageComplete: complete,
      passed,
      detail,
    });

    console.log(`  ${passed ? "ok  " : "FAIL"} ${name} · ${testCase.label} · `
      + `${expected.length} waves · ${routeIds.size}/${scene.graph.routes.length} routes`);
    if (detail) { console.error(`       ${detail}`); }
  }

  /* Speed selection is part of the recorded behaviour, so it is checked too. */
  const waveCount = ported.planTrace(scene, undefined).length;
  const speed = ported.speedForWaveCount(waveCount);
  const expectedSpeed = waveCount > 500 ? "16" : waveCount > 80 ? "4" : "1";
  if (speed !== expectedSpeed) { failures += 1; }
  results.push({
    scene: name, case: "speed selection", waves: waveCount,
    selectedSpeed: speed, passed: speed === expectedSpeed,
  });
  console.log(`  ${speed === expectedSpeed ? "ok  " : "FAIL"} ${name} · speed selection · `
    + `${waveCount} waves -> ${speed}×`);
}

const receiptDir = path.join(WORKBENCH, "evidence", "parity");
fs.mkdirSync(receiptDir, { recursive: true });
fs.writeFileSync(path.join(receiptDir, "trace-parity.receipt.json"), JSON.stringify({
  receiptType: "trace-parity-receipt.v1",
  verifiedAt: new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00"),
  statement: "The reimplemented illustrative trace planner is compared against the frozen "
    + "reference implementation, wave for wave and route order for route order, on every "
    + "ingested scene. Coverage completeness is asserted separately from ordering.",
  reference: "fixtures/baseline/viewer/viewer.js",
  ported: "runtime/illustrative-trace.js",
  node: process.version,
  summary: {
    cases: results.length,
    passed: results.filter((r) => r.passed).length,
    failed: results.filter((r) => !r.passed).length,
  },
  results,
  traceMode: "ILLUSTRATIVE",
  evidenceLimit: "Declared topology. This establishes planner equivalence, not execution.",
}, null, 2) + "\n", "utf8");

console.log(`trace parity   ${results.filter((r) => r.passed).length}/${results.length} cases passed`);
console.log(`receipt        ${path.join(receiptDir, "trace-parity.receipt.json")}`);
process.exit(failures === 0 ? 0 : 1);
