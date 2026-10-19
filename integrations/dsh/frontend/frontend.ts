import { spawn } from 'node:child_process'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'
import { PipeRpc } from './rpc.ts'
import { startDshFrontend } from './official_host.ts'
import { checkOfficialAssets } from './setup.ts'
export { startDshFrontend } from './official_host.ts'
export { checkOfficialAssets as checkFrontendAssets } from './setup.ts'
export { RPNH_COMMANDS as COMMANDS } from './official_bff.ts'

export interface FrontendOptions { readFd: number; writeFd: number; port?: number; openBrowser?: boolean }
export async function runDshFrontend(options: FrontendOptions): Promise<void> {
  const rpc = PipeRpc.fromFds(options.readFd, options.writeFd)
  try {
    const frontend = await startDshFrontend(rpc, { port: options.port ?? 0 })
    console.log(`DSH official Web · RPNH MainSession ${frontend.view.id}\n${frontend.url}`)
    const stop = () => { void frontend.close() }
    process.once('SIGINT', stop); process.once('SIGTERM', stop)
    if (options.openBrowser !== false) {
      const opener = spawn(process.platform === 'darwin' ? 'open' : 'xdg-open', [frontend.url], { stdio: 'ignore' })
      opener.on('error', () => console.error(`Open ${frontend.url} in your browser.`)); opener.unref()
    }
    await rpc.closed; await frontend.close()
    process.removeListener('SIGINT', stop); process.removeListener('SIGTERM', stop)
  } finally { rpc.close() }
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const { values } = parseArgs({ options: { check: { type: 'boolean' }, 'read-fd': { type: 'string' },
    'write-fd': { type: 'string' }, port: { type: 'string' }, 'no-open-browser': { type: 'boolean' } }, allowPositionals: false })
  const port = Number(values.port ?? 0)
  if (!Number.isSafeInteger(port) || port < 0 || port > 65535) throw new Error('Invalid port')
  void (values.check ? checkOfficialAssets().then(() => console.log('Official DSH Web assets and native imports ready.'))
    : runDshFrontend({ readFd: Number(values['read-fd']), writeFd: Number(values['write-fd']), port,
      openBrowser: !values['no-open-browser'] })).catch(error => { console.error(String(error)); process.exitCode = 1 })
}
