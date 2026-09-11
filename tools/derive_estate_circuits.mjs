/* Derive the estate catalogue from the selected database authority.
 *
 * Every capability's circuit is projected from the same authority invocation
 * reads: the capability/scenario spine, closure and declared effect ports. The
 * catalogue carries each capability's operations view. The invocable (published)
 * capabilities also carry their derived scene on disk, because the local
 * observation-mapping checks need a scene without a server. Every other view is
 * resolved on demand from the estate service, so no separately compiled product
 * participates anywhere.
 *
 *   node tools/derive_estate_circuits.mjs
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { pathToFileURL, fileURLToPath } from 'node:url';

const WORKBENCH = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const EMBODY = process.env.SFX_EMBODY_ROOT ?? 'C:/lab/repos/sfx-embody';
const DATABASE = process.env.SFX_DATABASE_ROOT ?? 'C:/lab/sidefx-database';
const SDA = process.env.SFX_SDA_ROOT ?? 'C:/lab/repos/scenario-driven-architecture';
const OUT = path.join(WORKBENCH, 'fixtures', 'estate');
const sha256 = bytes => 'sha256:' + createHash('sha256').update(bytes).digest('hex');

const { config: readDatabaseConfig } = await import(pathToFileURL(path.join(DATABASE, 'src/core.mjs')));
const { connectionString } = await import(pathToFileURL(path.join(DATABASE, 'src/ingest/database.mjs')));
const { connectionEnvironmentVariable } = await readDatabaseConfig();
process.env[connectionEnvironmentVariable] = connectionString(connectionEnvironmentVariable);
const { query } = await import(pathToFileURL(path.join(DATABASE, 'src/query/run.mjs')));
const { readAuthority } = await import(pathToFileURL(path.join(EMBODY, 'src/read-authority.mjs')));
const { deriveCircuit } = await import(pathToFileURL(path.join(EMBODY, 'src/derive-circuit.mjs')));

const binding = JSON.parse(await fs.readFile(path.join(WORKBENCH, 'dependencies/lab-publication.binding.json'), 'utf8'));
const publicationPath = process.env[binding.publication.environmentVariable] ?? binding.publication.default;
const invocable = {};
try {
  const publication = JSON.parse(await fs.readFile(publicationPath, 'utf8'));
  for (const pilot of publication.pilots) {
    invocable[pilot.profile.subject] = {
      publicationId: publication.publicationId,
      namespace: pilot.profile.namespace,
      inputContract: pilot.profile.inputContract,
      scenarioId: pilot.authority?.scenarioId,
      examples: (pilot.examples ?? []).map(example => example.id),
      editablePointers: pilot.profile.inputs.filter(input => input.ownership === 'editable').map(input => input.pointer),
    };
  }
} catch { /* no publication: every capability is inspect-only */ }

const listing = await query(
  `SELECT c.capability_id AS capabilityId, n.namespace_id AS namespaceId
     FROM model.estate_capability ec
     JOIN model.capability c ON c.capability_pk = ec.capability_pk
     JOIN model.identity_namespace n ON n.namespace_pk = c.namespace_pk
    WHERE ec.estate_model_pk = @estate_model_pk
    ORDER BY c.capability_id`, { retainObjects: false, rowLimit: 100000 });
const capabilities = listing.recordsets[0];
console.log(JSON.stringify({ capabilities: capabilities.length, snapshotId: listing.snapshotId }));

await fs.mkdir(OUT, { recursive: true });
const catalogue = [], findings = [];
let derived = 0, refused = 0, packaged = 0;
for (const { capabilityId, namespaceId } of capabilities) {
  let circuit;
  try {
    const bundle = await readAuthority(DATABASE, { capabilityId, namespaceId, target: 'node' }, { retainObjects: false });
    circuit = deriveCircuit({ bundle, capabilityId });
  } catch (error) {
    refused += 1;
    findings.push({ code: 'CAPABILITY_CIRCUIT_UNRESOLVED', severity: 'info', identity: capabilityId, detail: String(error.message ?? error) });
    continue;
  }
  derived += 1;
  const viewId = circuit.identities.viewId;
  const published = invocable[capabilityId];
  let scene = null, digest = null;
  if (published) {
    const directory = path.join(OUT, capabilityId);
    await fs.mkdir(directory, { recursive: true });
    const file = path.join(directory, viewId + '.scene.json');
    const text = JSON.stringify(circuit, null, 2) + '\n';
    await fs.writeFile(file, text, 'utf8');
    scene = path.posix.join('fixtures/estate', capabilityId, viewId + '.scene.json');
    digest = sha256(Buffer.from(text, 'utf8'));
    packaged += 1;
  }
  catalogue.push({
    capabilityId,
    title: circuit.label,
    source: circuit.provenance.sourceAuthority,
    views: [{
      viewId, viewKind: circuit.identities.viewKind, label: circuit.label,
      scenarioId: circuit.identities.scenarioId,
      coverage: { nodes: circuit.coverage.nodes, routes: circuit.coverage.routes, omittedSourceNodes: circuit.coverage.omittedSourceNodes },
      source: 'estate authority (read and planned per invocation)',
      scene, artifact: null, sha256: digest,
    }],
    viewKinds: { [circuit.identities.viewKind]: 1 },
    scenarios: [circuit.identities.scenarioId],
    affordances: ['inspect', ...(published ? ['invoke'] : [])],
    publication: published ?? null,
  });
}
catalogue.sort((a, b) => a.capabilityId.localeCompare(b.capabilityId));

const index = {
  catalogueVersion: 'estate-catalogue.v1',
  ingestedAt: new Date().toISOString(),
  products: 'selected database authority (read and planned per invocation)',
  adapter: { name: 'derive-estate-circuits', version: '1.0.0' },
  snapshotId: listing.snapshotId,
  projectionDigest: listing.projectionDigest,
  available: capabilities.length,
  ingested: catalogue.length,
  invocable: catalogue.filter(record => record.affordances.includes('invoke')).length,
  capabilities: catalogue,
  findings,
  traceMode: 'ILLUSTRATIVE',
};
await fs.writeFile(path.join(OUT, 'estate-catalogue.json'), JSON.stringify(index, null, 2) + '\n', 'utf8');

const receiptDir = path.join(WORKBENCH, 'evidence', 'estate');
await fs.mkdir(receiptDir, { recursive: true });
await fs.writeFile(path.join(receiptDir, 'authority-ingestion.receipt.json'), JSON.stringify({
  receiptType: 'authority-ingestion-receipt.v1', ingestedAt: index.ingestedAt,
  source: index.products, snapshotId: listing.snapshotId, projectionDigest: listing.projectionDigest,
  summary: { capabilities: capabilities.length, derived, refused, invocable: index.invocable, packagedScenes: packaged },
  catalogue: 'fixtures/estate/estate-catalogue.json', findings,
}, null, 2) + '\n', 'utf8');

console.log(JSON.stringify({ derived, refused, invocable: index.invocable, packagedScenes: packaged }));
