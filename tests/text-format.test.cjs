/* Checks for text resolution against a declared pack.
 *
 * The point of the pack is that wording, punctuation and glyphs are data. These
 * exercise that claim: the same runtime call produces different text under a
 * different pack, one glyph change reaches every string that uses it, and a name
 * the pack does not declare fails loudly instead of rendering blank.
 *
 * Run:  node tests/text-format.test.cjs
 */
"use strict";

const fs = require("fs");
const path = require("path");

const WORKBENCH = path.resolve(__dirname, "..");
const textFormat = require(path.join(WORKBENCH, "runtime/text-format.js"));
const PACK_PATH = path.join(WORKBENCH, "profiles/workbench-en.text.json");
const pack = JSON.parse(fs.readFileSync(PACK_PATH, "utf8"));

const results = [];
let failures = 0;

function check(label, condition, detail) {
  const passed = Boolean(condition);
  if (!passed) { failures += 1; }
  results.push({ case: label, passed, detail: passed ? undefined : detail });
  console.log(`  ${passed ? "ok  " : "FAIL"} ${label}`);
  if (!passed && detail !== undefined) { console.error(`       ${JSON.stringify(detail)}`); }
}

const findings = [];
const text = textFormat.create(pack, (code, name) => findings.push({ code, name }));

/* --- the readouts that were previously concatenated in the runtime -------- */

const coverage = text.format("coverage", { nodes: 30, routes: 45, omitted: 0 });
check("coverage reads exactly as the frozen reference did",
  coverage === "30 components · 45 routes · 0 source components omitted", coverage);

check("zoom formats as a whole percentage",
  text.format("zoom", { percent: 15 }) === "15%");

check("a view option carries the label and its counts",
  text.format("viewOption", { label: "Capability blueprint", nodes: 30, routes: 45 })
  === "Capability blueprint · 30 components / 45 routes");

check("a speed option is its value and the declared suffix",
  text.format("speedOption", { value: "16" }) === "16×");

check("a route choice uses the declared arrow",
  text.format("routeChoice", { label: "sequence", target: "admit input" })
  === "sequence → admit input");

check("the source pointer keeps its digest and pointer",
  text.format("sourcePointer", { label: "policy", sha256: "abc", pointer: "/x" })
  === "policy · SHA-256 abc · /x");

check("a parallel trace wave uses the declared range dash",
  text.format("traceParallel", { from: 3, to: 5, total: 45, branches: 3 })
  === "Tracing 3–5 / 45 · 3 parallel branches");

/* --- messages resolve glyphs too, so nothing spells a separator ----------- */

check("a message resolves glyph placeholders",
  text.message("loadingGraph") === "Loading source graph…",
  text.message("loadingGraph"));

check("no message in the pack spells a glyph literally",
  !Object.values(pack.messages).some((m) => [...m].some((c) => c.charCodeAt(0) > 127)),
  Object.entries(pack.messages).filter(([, m]) => [...m].some((c) => c.charCodeAt(0) > 127)));

check("play labels cover every lifecycle state",
  ["idle", "running", "paused", "complete"].every((s) => text.playLabel(s).length > 0));

check("view kind names cover every supported kind",
  ["blueprint", "operations", "native", "expression"]
    .every((k) => text.viewKindName(k).length > 0));

/* --- an undeclared name fails loudly, never blank ------------------------- */

const before = findings.length;
const missingFormat = text.format("noSuchFormat", {});
check("an undeclared format renders visibly wrong, not empty",
  missingFormat.includes("noSuchFormat") && missingFormat.length > 0
  && findings.length === before + 1
  && findings[findings.length - 1].code === "TEXT_FORMAT_UNDECLARED", missingFormat);

const missingMessage = text.message("noSuchMessage");
check("an undeclared message renders visibly wrong, not empty",
  missingMessage.includes("noSuchMessage")
  && findings[findings.length - 1].code === "TEXT_MESSAGE_UNDECLARED", missingMessage);

const missingValue = text.format("coverage", { nodes: 30 });
check("a missing placeholder value renders visibly wrong, not a stray brace",
  !missingValue.includes("{routes}") && missingValue.includes("coverage"), missingValue);

check("an undeclared lookup is reported",
  text.playLabel("nonsense").includes("playLabels.nonsense"));

/* --- the pack is data: swapping it changes the interface ------------------ */

const swapped = JSON.parse(JSON.stringify(pack));
swapped.glyphs.separator = "|";
swapped.glyphs.speedSuffix = "x";
const other = textFormat.create(swapped);

check("changing one glyph reaches every string that uses it",
  other.format("coverage", { nodes: 30, routes: 45, omitted: 0 })
    === "30 components | 45 routes | 0 source components omitted"
  && other.format("sourcePointer", { label: "p", sha256: "a", pointer: "/x" })
    === "p | SHA-256 a | /x"
  && other.message("runAccepted") === "Accepted | waiting for reported execution");

check("changing a glyph does not disturb formats that never used it",
  other.format("zoom", { percent: 15 }) === "15%");

const reworded = JSON.parse(JSON.stringify(pack));
reworded.formats.coverage = "{nodes}/{routes} ({omitted} omitted)";
check("rewording a format changes only that readout",
  textFormat.create(reworded).format("coverage", { nodes: 30, routes: 45, omitted: 0 })
    === "30/45 (0 omitted)");

/* --- an unsupported pack is refused rather than half-used ----------------- */

let refused = false;
try { textFormat.create({ textVersion: "workbench-text.v0" }); }
catch (error) { refused = error.message === "TEXT_PACK_UNSUPPORTED"; }
check("a pack of an unsupported version is refused", refused);

/* --- receipt -------------------------------------------------------------- */

const receiptDir = path.join(WORKBENCH, "evidence", "text");
fs.mkdirSync(receiptDir, { recursive: true });
fs.writeFileSync(path.join(receiptDir, "text-format.receipt.json"), JSON.stringify({
  receiptType: "text-format-receipt.v1",
  verifiedAt: new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00"),
  statement: "Text resolution is exercised against the declared pack: the readouts that were "
    + "previously concatenated in the runtime, glyph substitution inside messages, loud "
    + "failure for undeclared names, and the swap that proves wording is data.",
  pack: { packId: pack.packId, locale: pack.locale,
          formats: Object.keys(pack.formats).length,
          messages: Object.keys(pack.messages).length,
          glyphs: pack.glyphs },
  node: process.version,
  summary: { cases: results.length, passed: results.filter((r) => r.passed).length,
             failed: results.filter((r) => !r.passed).length },
  results,
  notEstablished: [
    "No browser rendered these strings; this checks resolution, not painting.",
    "A second locale pack does not exist, so translation is demonstrated by swapping "
      + "glyphs and a format rather than by a full alternate pack."
  ]
}, null, 2) + "\n", "utf8");

console.log(`text format    ${results.filter((r) => r.passed).length}/${results.length} cases passed`);
console.log(`receipt        ${path.join(receiptDir, "text-format.receipt.json")}`);
process.exit(failures === 0 ? 0 : 1);
