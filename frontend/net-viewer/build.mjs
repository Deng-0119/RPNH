/** Copy unmodified, lockfile-pinned distributions. No network at viewer runtime. */
import { readFile, writeFile, copyFile, mkdir, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, '../../cpn/frontend/static/assets');
const dependencies = [
  { name: '@joint/core', version: '4.3.1', license: 'LICENSE', source: 'https://github.com/clientIO/joint/tree/v4.3.1',
    files: { 'dist/joint.min.js': 'joint.js', 'LICENSE': 'joint-LICENSE.txt' } },
  { name: 'elkjs', version: '0.12.0', license: 'LICENSE.md', source: 'https://github.com/kieler/elkjs/tree/0.12.0',
    files: { 'lib/elk-api.js': 'elk-api.js', 'lib/elk-worker.min.js': 'elk-worker.js', 'LICENSE.md': 'elk-LICENSE.txt' } },
];
const manifest = { schema_version: 'rpnh/viewer_assets/v1', dependencies: [], files: [] };
// Validate before deleting any previous, usable build.
for (const dep of dependencies) {
  const pkg = JSON.parse(await readFile(resolve(here, 'node_modules', dep.name, 'package.json'), 'utf8'));
  if (pkg.version !== dep.version) throw new Error(`Unexpected ${dep.name} version: ${pkg.version}`);
  for (const source of Object.keys(dep.files)) await readFile(resolve(here, 'node_modules', dep.name, source));
  manifest.dependencies.push({ name: dep.name, version: pkg.version, license: pkg.license, source: dep.source });
}
await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
for (const dep of dependencies) for (const [source, target] of Object.entries(dep.files)) {
  await copyFile(resolve(here, 'node_modules', dep.name, source), resolve(out, target));
  manifest.files.push(target);
}
await writeFile(resolve(out, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log(JSON.stringify(manifest, null, 2));
