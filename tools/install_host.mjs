import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const platform = path.resolve(process.env.SFX_PLATFORM_ROOT ?? 'C:/lab/repos/sfx-platform');
await fs.cp(path.join(root, 'build/web/package'), path.join(platform, 'public/workbench'), { recursive: true });
await fs.copyFile(path.join(root, 'build/web/workbench-publication.json'), path.join(platform, 'generated/workbench-publication.json'));
console.log(JSON.stringify({ host: platform, path: '/workbench/index.html' }));
