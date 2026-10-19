import { createServer } from 'node:http'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { access, readFile, realpath } from 'node:fs/promises'
import { constants } from 'node:fs'
import { randomUUID } from 'node:crypto'
import { spawn } from 'node:child_process'
import { dirname, extname, resolve, sep } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'
import { createFrontendApplication } from './agent.ts'
import { MAX_FRAME_BYTES, PipeRpc, RpcError } from './rpc.ts'
import type { RpcPort, Selection } from './rpc.ts'

export const COMMANDS = ['rpnh-help', 'rpnh-tasks', 'rpnh-task', 'rpnh-net', 'rpnh-resume', 'rpnh-rollback',
  'rpnh-workflow', 'rpnh-agent', 'rpnh-send'] as const
const DIRECTORY = dirname(fileURLToPath(import.meta.url))
const MIME: Record<string, string> = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
  '.css': 'text/css', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.svg': 'image/svg+xml' }

async function body(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = []; let size = 0
  for await (const chunk of req) {
    size += chunk.length
    if (size > MAX_FRAME_BYTES) throw new RpcError('frame_too_large', 'Request exceeds 68 MiB.', 413)
    chunks.push(Buffer.from(chunk))
  }
  const value = JSON.parse(Buffer.concat(chunks).toString('utf8'))
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new RpcError('invalid_params', 'An object is required.')
  return value
}
function selected(params: Record<string, unknown>): Selection {
  const output: Selection = {}
  for (const key of ['selectionId', 'reasoningEffort'] as const) {
    if (params[key] === undefined) continue
    if (typeof params[key] !== 'string' || !params[key]) throw new RpcError('invalid_params', `Invalid ${key}.`)
    output[key] = params[key]
  }
  return output
}
function string(params: Record<string, unknown>, key: string): string {
  if (typeof params[key] !== 'string') throw new RpcError('invalid_params', `${key} is required.`)
  return params[key]
}
function exact(params: Record<string, unknown>, keys: string[]): void {
  if (Object.keys(params).some(key => !keys.includes(key))) throw new RpcError('invalid_params', 'Unsupported parameters; the MainSession is bound by the launcher.')
}
function json(res: ServerResponse, value: unknown, status = 200): void {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
  res.end(JSON.stringify(value))
}
export interface FrontendOptions { readFd: number; writeFd: number; port?: number; openBrowser?: boolean }

export async function checkFrontendAssets(directory = resolve(DIRECTORY, 'dist')): Promise<void> {
  for (const file of ['index.html', 'browser.js', 'browser.css', 'layout.css']) {
    try { await access(resolve(directory, file), constants.R_OK) }
    catch { throw new Error(`DSH Web assets missing: ${resolve(directory, file)}. Build rpnh-frontend/build.ts in the prepared DSH source first.`) }
  }
}

/** Exported seam for native offline tests; normal launch supplies private inherited descriptors. */
export async function startDshFrontend(rpc: RpcPort, options: { port?: number; assetDirectory?: string } = {}) {
  const assets = await realpath(options.assetDirectory ?? resolve(DIRECTORY, 'dist'))
  await checkFrontendAssets(assets)
  const index = await readFile(resolve(assets, 'index.html'), 'utf8')
  const app = await createFrontendApplication(rpc)
  const token = randomUUID()
  let closing: Promise<void> | undefined
  let mutation = false
  const server = createServer((req, res) => {
    void (async () => {
      const address = server.address()
      if (!address || typeof address === 'string' || req.headers.host !== `127.0.0.1:${address.port}`) {
        throw new RpcError('forbidden', 'Use the exact loopback address printed by the launcher.', 403)
      }
      const url = new URL(req.url ?? '/', 'http://127.0.0.1')
      if (url.pathname.startsWith('/api/')) {
        if (req.headers.origin !== undefined && req.headers.origin !== `http://127.0.0.1:${address.port}`) {
          throw new RpcError('forbidden', 'This API accepts requests only from its own frontend origin.', 403)
        }
        if (req.headers['x-rpnh-token'] !== token) throw new RpcError('forbidden', 'Open this frontend using its launcher URL.', 403)
        if (req.method === 'GET' && url.pathname === '/api/snapshot') {
          const session = await app.machine.refresh()
          json(res, { session, configuration: app.initial.configuration, commands: COMMANDS,
            capabilities: { tools: [], skills: [], files: [], nativeProviders: [], backendSettings: false },
            agent: { status: app.machine.status, error: app.machine.lastError ?? null } })
          return
        }
        if (req.method !== 'POST') throw new RpcError('unsupported', 'Unsupported frontend operation.', 405)
        const params = await body(req)
        if (url.pathname === '/api/abort') {
          exact(params, []); json(res, await rpc.request('abort')); return
        }
        if (mutation) throw new RpcError('busy', 'Another RPNH operation is being admitted.', 409)
        mutation = true
        try {
          if (url.pathname === '/api/submit') {
            exact(params, ['text', 'requestId', 'selectionId', 'reasoningEffort'])
            json(res, await app.machine.submit(string(params, 'text'), string(params, 'requestId'), selected(params)))
          } else if (url.pathname === '/api/command') {
            exact(params, ['name', 'arguments', 'requestId', 'selectionId', 'reasoningEffort'])
            const name = string(params, 'name')
            if (!COMMANDS.includes(name as typeof COMMANDS[number])) throw new RpcError('unsupported', 'Use an RPNH command from /rpnh-help.')
            const result = await rpc.request('command', { name, arguments: string(params, 'arguments'),
              requestId: string(params, 'requestId'), ...selected(params) })
            // Rebuild the ephemeral native projection after explicit commands;
            // rollback/resume may change prior rows. No durable DSH log exists.
            await app.rebuild()
            json(res, result)
          } else throw new RpcError('unsupported', 'This native capability is unavailable in the bound RPNH frontend.', 404)
        } finally { mutation = false }
        return
      }
      if (req.method !== 'GET') throw new RpcError('unsupported', 'Method unavailable.', 405)
      if (url.pathname === '/favicon.ico') { res.writeHead(204); res.end(); return }
      if (url.pathname === '/') {
        res.writeHead(200, { 'Content-Type': MIME['.html'], 'Cache-Control': 'no-store',
          'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
          'X-Content-Type-Options': 'nosniff' })
        res.end(index.replace('</head>', `<meta name="rpnh-token" content="${token}"></head>`)); return
      }
      const path = await realpath(resolve(assets, '.' + decodeURIComponent(url.pathname)))
      if (!path.startsWith(assets + sep) || !MIME[extname(path)]) throw new RpcError('not_found', 'Asset unavailable.', 404)
      res.writeHead(200, { 'Content-Type': MIME[extname(path)], 'X-Content-Type-Options': 'nosniff' })
      res.end(await readFile(path))
    })().catch(error => {
      if (res.headersSent) { res.destroy(); return }
      json(res, { error: { code: error instanceof RpcError ? error.code : 'frontend_error',
        message: error instanceof Error ? error.message : String(error) } }, error instanceof RpcError ? error.status : 400)
    })
  })
  const close = (): Promise<void> => closing ??= (async () => {
    const stopped = new Promise<void>(done => server.close(() => done()))
    server.closeAllConnections(); rpc.close()
    await app.close(); await stopped
  })()
  try {
    await new Promise<void>((done, reject) => {
      server.once('error', reject)
      server.listen(options.port ?? 0, '127.0.0.1', () => { server.off('error', reject); done() })
    })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('DSH listener failed to bind')
    void rpc.closed.then(() => close()).catch(error => console.error(String(error)))
    return { url: `http://127.0.0.1:${address.port}/`, close, app }
  } catch (error) { await close(); throw error }
}

export async function runDshFrontend(options: FrontendOptions): Promise<void> {
  const rpc = PipeRpc.fromFds(options.readFd, options.writeFd)
  try {
    const frontend = await startDshFrontend(rpc, { port: options.port ?? 0 })
    console.log(`RPNH Web frontend using DSH widgets · MainSession ${frontend.app.initial.session.id}\n${frontend.url}`)
    const stop = (): void => { void frontend.close() }
    process.once('SIGINT', stop); process.once('SIGTERM', stop)
    if (options.openBrowser !== false) {
      const command = process.platform === 'darwin' ? 'open' : 'xdg-open'
      const opener = spawn(command, [frontend.url], { stdio: 'ignore' })
      opener.on('error', () => console.error(`Open ${frontend.url} in your browser.`))
      opener.unref()
    }
    await rpc.closed; await frontend.close()
    process.removeListener('SIGINT', stop); process.removeListener('SIGTERM', stop)
  } finally { rpc.close() }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const { values } = parseArgs({ options: { check: { type: 'boolean' }, 'read-fd': { type: 'string' }, 'write-fd': { type: 'string' },
    port: { type: 'string' }, 'no-open-browser': { type: 'boolean' } }, allowPositionals: false })
  const port = Number(values.port ?? 0)
  if (!Number.isSafeInteger(port) || port < 0 || port > 65535) throw new Error('Invalid port')
  void (values.check ? checkFrontendAssets().then(() => console.log('DSH Web ready: rpnh-frontend/dist/index.html'))
    : runDshFrontend({ readFd: Number(values['read-fd']), writeFd: Number(values['write-fd']), port,
      openBrowser: !values['no-open-browser'] })).catch(error => { console.error(String(error)); process.exitCode = 1 })
}
