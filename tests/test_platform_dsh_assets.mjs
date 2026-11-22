/** Execute production DSH preparation checks using synthetic local packages.
 * Node's built-in TypeScript stripping loads setup.ts; no npm/tsx/upstream UI,
 * network, credentials, Registry, or model calls are involved. This is not
 * native DSH product or macOS acceptance.
 * Run: node --test tests/test_platform_dsh_assets.mjs
 */
import assert from 'node:assert/strict'
import { copyFile, mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const repository = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const adapters = join(repository, 'integrations/dsh/frontend')

async function treeState(root) {
  const rows = []
  async function visit(directory) {
    for (const entry of (await readdir(directory, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
      const path = join(directory, entry.name)
      if (entry.isDirectory()) await visit(path)
      else rows.push([path, (await stat(path)).mtimeMs, await readFile(path)])
    }
  }
  await visit(root)
  return rows
}

for (const manifestName of ['web-packages.json', 'web-packages.rc1.json', 'web-packages.rc5.json']) {
  test(`production setup.ts with synthetic ${manifestName}`, async t => {
    const root = await mkdtemp(join(tmpdir(), 'rpnh-dsh-ts-contract-'))
    try {
      const source = join(root, 'source')
      const target = join(source, 'rpnh-frontend')
      await mkdir(target, { recursive: true })
      await writeFile(join(source, 'package.json'), JSON.stringify({ type: 'module' }))
      await writeFile(join(source, 'tsconfig.base.json'), '{}')
      await copyFile(join(adapters, manifestName), join(target, 'web-packages.json'))
      await copyFile(join(adapters, 'setup.ts'), join(target, 'setup.ts'))
      await copyFile(join(adapters, 'projection_frames.ts'), join(target, 'projection_frames.ts'))
      const production = await import(pathToFileURL(join(target, 'setup.ts')).href)
      const manifest = JSON.parse(await readFile(join(target, 'web-packages.json'), 'utf8'))
      const descriptors = new Set(production.DESCRIPTOR_PACKAGES)
      const clients = new Set([...production.REQUIRED_CLIENTS, 'dsh-client-ui-workspace'])
      for (const name of manifest.packages) {
        const directory = join(target, 'node_modules', name)
        await mkdir(directory, { recursive: true })
        const short = name.split('/')[1]
        const metadata = { name, version: manifest.version, exports: {} }
        if (clients.has(short)) {
          metadata.dsh = { client: { inject: [] } }
          metadata.exports['./client'] = './client.js'
          await writeFile(join(directory, 'client.js'), '// synthetic official client entry\n')
        }
        if (descriptors.has(short)) {
          metadata.exports['./typert'] = './descriptor.ts'
          await writeFile(join(directory, 'descriptor.ts'), 'export const TYPERT = { invocations: [] }\n')
        }
        if (short === 'dsh-web-frontend') {
          await mkdir(join(directory, 'dist'))
          await writeFile(join(directory, 'dist/index.html'), '<!doctype html><title>Fixture</title>')
        }
        await writeFile(join(directory, 'package.json'), JSON.stringify(metadata))
      }

      await t.test('explicit preparation produces a complete local host', async () => {
        await production.prepareOfficialHost()
        await production.checkOfficialAssets()
        const entries = JSON.parse(await readFile(join(target, 'official.json'), 'utf8'))
        assert.equal(entries.length, clients.size + 1)
        assert.equal(entries.at(-1).name, join(target, 'generated/bound-client/host.ts'))
      })

      await t.test('readiness check leaves all prepared files unchanged', async () => {
        const before = await treeState(source)
        await production.checkOfficialAssets()
        assert.deepEqual(await treeState(source), before)
      })

      await t.test('a missing generated descriptor is rejected', async () => {
        const path = join(target, 'generated', production.DESCRIPTOR_PACKAGES[0] + '.ts')
        const original = await readFile(path)
        await rm(path)
        try {
          await assert.rejects(production.checkOfficialAssets(), { code: 'ENOENT' })
        } finally {
          await writeFile(path, original)
        }
      })

      await t.test('missing bound-client generated files are rejected', async () => {
        for (const name of ['host.ts', 'client.js', 'package.json']) {
          const path = join(target, 'generated/bound-client', name)
          const original = await readFile(path)
          await rm(path)
          try {
            await assert.rejects(production.checkOfficialAssets(), { code: 'ENOENT' })
          } finally {
            await writeFile(path, original)
          }
        }
      })

      await t.test('existing hosts from an old absolute path are rejected', async () => {
        const path = join(target, 'official.json')
        const original = await readFile(path)
        const entries = JSON.parse(original)
        const oldHost = join(root, 'old-checkout-host.ts')
        await writeFile(oldHost, 'export function apply() {}\n')
        try {
          for (const index of [0, entries.length - 1]) {
            const stale = structuredClone(entries)
            stale[index].name = oldHost
            await writeFile(path, JSON.stringify(stale))
            await assert.rejects(production.checkOfficialAssets(), /host entries do not match/)
          }
        } finally {
          await writeFile(path, original)
        }
      })

      await t.test('an incomplete list of client hosts is rejected', async () => {
        const path = join(target, 'official.json')
        const original = await readFile(path)
        const entries = JSON.parse(original)
        await writeFile(path, JSON.stringify(entries.filter(entry => entry.id === 'bound-session')))
        try {
          await assert.rejects(production.checkOfficialAssets(), /host entries do not match/)
        } finally {
          await writeFile(path, original)
        }
      })
    } finally {
      await rm(root, { recursive: true, force: true })
    }
  })
}
