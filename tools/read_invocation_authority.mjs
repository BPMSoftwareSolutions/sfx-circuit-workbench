import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const embody = process.env.SFX_EMBODY_ROOT ?? 'C:/lab/repos/sfx-embody';
const databaseRoot = process.env.SFX_DATABASE_ROOT ?? 'C:/lab/sidefx-database';
const sdaRoot = process.env.SFX_SDA_ROOT ?? 'C:/lab/repos/scenario-driven-architecture';
const { readAuthority } = await import(pathToFileURL(path.join(embody, 'src/read-authority.mjs')));
const { planNode } = await import(pathToFileURL(path.join(embody, 'src/materialize-node.mjs')));
const { verifyInvocationBinding } = await import(pathToFileURL(path.join(embody, 'src/verify-invocation-binding.mjs')));
const publication = JSON.parse(await fs.readFile(process.env.SFX_LAB_PUBLICATION ?? 'C:/lab/repos/sfx-platform/generated/lab-publication.json', 'utf8'));
const directory = path.join(root, 'build/invocation-authority'); await fs.mkdir(directory, { recursive: true });
for (const pilot of publication.pilots) {
  const bundle = await readAuthority(databaseRoot, { capabilityId: pilot.profile.subject, namespaceId: pilot.profile.namespace, target: 'node' }, { retainObjects: false });
  const plan = await planNode({ bundle, sdaRoot });
  verifyInvocationBinding({ subject: pilot.profile.subject, namespace: pilot.profile.namespace, ...pilot.authority }, bundle, plan);
  await fs.writeFile(path.join(directory, pilot.profile.subject + '.json'), JSON.stringify({ publicationId: publication.publicationId,
    subject: pilot.profile.subject, authority: pilot.authority, bundle, plan }, null, 2));
  console.log(JSON.stringify({ subject: pilot.profile.subject, scenarioId: plan.selectedScenarioId,
    publicationId: publication.publicationId, verified: true }));
}
