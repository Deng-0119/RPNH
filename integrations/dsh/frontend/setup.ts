/** Prepare native Loader entries for the official browser plugins, without booting a session. */
import { access, copyFile, mkdir, readFile, writeFile } from 'node:fs/promises'
import { constants } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

export const FRONTEND_DIR = dirname(fileURLToPath(import.meta.url))
export const DESCRIPTOR_PACKAGES = ['dsh-api-session-controller', 'dsh-api-workspace-controller',
  'dsh-api-settings-controller', 'dsh-commands', 'dsh-subagent', 'dsh-api-remote-access']
export const REQUIRED_CLIENTS = ['dsh-client-ui-commands', 'dsh-client-ui-model-selection']
export async function officialPackages() {
  const { packages } = JSON.parse(await readFile(resolve(FRONTEND_DIR, 'web-packages.json'), 'utf8'))
  return await Promise.all((packages as string[]).map(async name => {
    const directory = resolve(FRONTEND_DIR, 'node_modules', name)
    return { name, directory, metadata: JSON.parse(await readFile(resolve(directory, 'package.json'), 'utf8')) }
  }))
}
export async function checkOfficialAssets(): Promise<void> {
  const packages = await officialPackages()
  for (const name of [...REQUIRED_CLIENTS, ...DESCRIPTOR_PACKAGES]) {
    if (!packages.some(pkg => pkg.name === '@deepseek-ai/' + name)) throw new Error(`Missing official frontend package: @deepseek-ai/${name}`)
  }
  for (const pkg of packages) {
    if (pkg.metadata.name !== pkg.name || pkg.metadata.version !== '0.2.1-rc.2') throw new Error(`Unexpected official package/version: ${pkg.name}`)
    if (pkg.metadata.dsh?.client) {
      const entry = pkg.metadata.exports['./client']
      await access(resolve(pkg.directory, typeof entry === 'string' ? entry : entry.browser ?? entry.default), constants.R_OK)
    }
    if (DESCRIPTOR_PACKAGES.some(name => pkg.name === '@deepseek-ai/' + name)) {
      const entry = pkg.metadata.exports['./typert']
      await access(resolve(pkg.directory, typeof entry === 'string' ? entry : entry.default), constants.R_OK)
    }
  }
  await access(resolve(FRONTEND_DIR, 'node_modules/@deepseek-ai/dsh-web-frontend/dist/index.html'), constants.R_OK)
  await access(resolve(FRONTEND_DIR, 'official.json'), constants.R_OK)
  await access(resolve(FRONTEND_DIR, 'tsconfig.json'), constants.R_OK)
}
export async function prepareOfficialHost(): Promise<void> {
  const packages = await officialPackages()
  const entries: Array<{ id: string; name: string }> = []
  await mkdir(resolve(FRONTEND_DIR, 'generated'), { recursive: true })
  for (const pkg of packages) {
    if (pkg.metadata.dsh?.client) {
      const path = resolve(pkg.directory, 'rpnh-host.ts')
      await writeFile(path, 'export function apply() {}\n')
      entries.push({ id: `client-${entries.length}`, name: path })
    }
    if (DESCRIPTOR_PACKAGES.some(name => pkg.name === '@deepseek-ai/' + name)) {
      const exp = pkg.metadata.exports['./typert']
      // TSX source resolution deliberately excludes node_modules. This exact
      // generated descriptor copy shares the prepared upstream's source runtime.
      await copyFile(resolve(pkg.directory, typeof exp === 'string' ? exp : exp.default),
        resolve(FRONTEND_DIR, 'generated', pkg.name.split('/')[1] + '.ts'))
    }
  }
  const bridge = resolve(FRONTEND_DIR, 'generated/bound-client')
  await mkdir(bridge, { recursive: true })
  await writeFile(resolve(bridge, 'package.json'), JSON.stringify({ name: '@rpnh/dsh-bound-session', version: '1.0.0',
    type: 'module', exports: { '.': './host.ts', './client': './client.js', './package.json': './package.json' },
    dsh: { client: { platform: 'web', inject: ['@deepseek-ai/dsh-client-ui-workspace'] } } }))
  await writeFile(resolve(bridge, 'host.ts'), 'export function apply() {}\n')
  await writeFile(resolve(bridge, 'client.js'), `window.__ModuleLoader__.load({id:'@rpnh/dsh-bound-session',factory(){return {
    inject:['uiWorkspace','sessions','workspaces','remote','conversation'],apply(ctx){ctx.effect(()=>{
      let opened=false;const id=window.__RPNH_BOUND_SESSION__;
      const notice=ctx.remote.$on('api-session/error',(sessionId,message)=>{
        if(sessionId!==id)return;const scope=ctx.sessions.scope(id);
        if(scope)ctx.conversation.input.for(scope).notify('info',message);
      });
      const open=()=>{if(opened||ctx.sessions.list.getSnapshot().phase!=='ready'||ctx.workspaces.list.getSnapshot().phase!=='ready'||!ctx.sessions.list.getSnapshot().byId[id])return;
        opened=true;ctx.uiWorkspace.openSession(id)};
      const sessions=ctx.sessions.list.subscribe(open),workspaces=ctx.workspaces.list.subscribe(open);open();
      return()=>{sessions();workspaces();notice()}
    })}
  }}});\n`)
  entries.push({ id: 'bound-session', name: resolve(bridge, 'host.ts') })
  await writeFile(resolve(FRONTEND_DIR, 'official.json'), JSON.stringify(entries, null, 2))
  await writeFile(resolve(FRONTEND_DIR, 'tsconfig.json'), JSON.stringify({ extends: '../tsconfig.base.json' }))
  await checkOfficialAssets()
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  void prepareOfficialHost().catch(error => { console.error(String(error)); process.exitCode = 1 })
}
