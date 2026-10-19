import test from 'node:test'
import assert from 'node:assert/strict'
import { PassThrough } from 'node:stream'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { request as httpRequest } from 'node:http'
import { startDshFrontend, checkFrontendAssets } from './frontend.ts'
import { PipeRpc, PROTOCOL, MAX_FRAME_BYTES } from './rpc.ts'
import type { Initialization, RpcPort, SessionRow, Turn } from './rpc.ts'
import { projectSession } from './projection.ts'

const profile = { selection: 'rpnh:test', provider: 'test', provider_name: 'Offline', model: 'fixture',
  reasoning_effort: null, supported_reasoning_efforts: [], default_reasoning_effort: null, ready: true }
const turn = (ordinal: number, state = 'committed'): Turn => ({ ordinal, key: `request-${ordinal}`, text: `Cross frontend prompt ${ordinal}`,
  kind: 'main', model: profile, state, created: 1000, updated: 2000, turn_ref: null, attempt: null,
  answer: state === 'committed' ? { reply: `Answer ${ordinal}`, protocol_valid: true } : null, children: [] })
export class ScriptedPort implements RpcPort {
  readonly closure = Promise.withResolvers<Error>(); readonly closed = this.closure.promise
  row: SessionRow = { id: 'bound-main-session', created: 1000, updated: 2000, model: profile, turns: [turn(1)], error: null }
  calls: Array<{ method: string; params: Record<string, unknown> }> = []
  autoComplete = true
  private reads = 0
  async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    this.calls.push({ method, params })
    if (method === 'initialize') return structuredClone({ session: this.row,
      configuration: { default_selection: profile.selection, default_reasoning_effort: null, profiles: [profile] } } satisfies Initialization) as T
    if (method === 'snapshot') {
      const active = this.row.turns.at(-1)!
      if (this.autoComplete && active.state === 'accepted') {
        this.reads++
        if (this.reads > 2) { active.state = 'committed'; active.answer = { reply: `Native result: ${active.text}`, protocol_valid: true } }
      }
      return structuredClone(this.row) as T
    }
    if (method === 'submit') {
      assert.ok(Object.keys(params).every(key => ['requestId', 'text', 'selectionId', 'reasoningEffort'].includes(key)))
      const next = turn(this.row.turns.length + 1, 'accepted'); next.text = String(params.text)
      this.row.turns.push(next); this.reads = 0
      return { ordinal: next.ordinal } as T
    }
    if (method === 'abort') {
      this.row.turns.at(-1)!.state = 'stopped_by_owner'
      return { stopped: true } as T
    }
    if (method === 'command') {
      // MainSession rollback retains lineage and closes the pending turn.
      if (params.name === 'rpnh-rollback') this.row.turns.at(-1)!.state = 'interrupted'
      if (params.name === 'rpnh-resume') this.row.turns.at(-1)!.state = 'accepted'
      return { text: 'RPNH command accepted' } as T
    }
    throw new Error('Unexpected method: ' + method)
  }
  close(): void { this.closure.resolve(new Error('owner closed')) }
}

test('pipe correlates fragmented UTF-8 and out-of-order replies, propagates errors and EOF', async () => {
  const input = new PassThrough(); const output = new PassThrough()
  const rpc = new PipeRpc(input, output)
  const one = rpc.request('snapshot'); const a = JSON.parse(output.read().toString())
  const two = rpc.request('abort'); const b = JSON.parse(output.read().toString())
  assert.equal(a.protocol, PROTOCOL)
  const reply = Buffer.from(JSON.stringify({ protocol: PROTOCOL, id: b.id, result: '中文' }) + '\n')
  input.write(reply.subarray(0, reply.length - 5)); input.write(reply.subarray(reply.length - 5))
  assert.equal(await two, '中文')
  input.write(JSON.stringify({ protocol: PROTOCOL, id: a.id, error: { code: 'busy', message: 'Busy', status: 409 } }) + '\n')
  await assert.rejects(one, { code: 'busy', message: 'Busy', status: 409 })
  const pending = rpc.request('snapshot'); input.end()
  await assert.rejects(pending, /pipe closed/)
  await assert.rejects(rpc.request('snapshot'), /pipe closed/)
})
test('pipe rejects oversized outgoing frames and malformed replies without replay', async () => {
  const input = new PassThrough(); const output = new PassThrough(); const rpc = new PipeRpc(input, output)
  await assert.rejects(rpc.request('submit', { text: 'a'.repeat(MAX_FRAME_BYTES) }), /68 MiB/)
  assert.equal(output.read(), null)
  const pending = rpc.request('snapshot'); input.write('{broken}\n')
  await assert.rejects(pending)
  rpc.close()
})
test('history projects any frontend and keeps active/paused turns open', () => {
  const port = new ScriptedPort()
  port.row.turns.push(turn(2, 'stopped_by_owner'))
  const events = projectSession(port.row)
  assert.equal(events.filter(event => event.type === 'user/message').length, 2)
  assert.equal(events.filter(event => event.type === 'assistant/message').length, 1)
  assert.equal(events.filter(event => event.type === 'turn/end').length, 1)
})

test('official BFF reads bound history without execution and maintains one presentation cursor', async () => {
  const port = new ScriptedPort(); const frontend = await startDshFrontend(port)
  try {
    const handlers = frontend.view.handlers()
    const listing = await handlers.session!.list!({})
    assert.equal(listing.items.length, 1)
    assert.equal(listing.items[0].sessionId, port.row.id)
    assert.equal(port.calls.some(call => ['submit', 'abort', 'command'].includes(call.method)), false)
    assert.throws(() => handlers.session!.projections!({ sessionId: 'foreign' }), /bound/)
    const before = frontend.view.cursor
    handlers.session!.selectModel!({ sessionId: port.row.id, provider: 'rpnh', model: profile.selection })
    assert.ok(frontend.view.cursor > before)
    assert.equal(handlers.session!.projections!({ sessionId: port.row.id }).asOfSeq, frontend.view.cursor)
    assert.equal(port.calls.some(call => ['submit', 'command'].includes(call.method)), false)
    await assert.rejects(handlers.session!.prompt!({ sessionId: port.row.id, mode: 'queue',
      requestId: 'not-a-turn', content: [{ type: 'text', text: '/rpnh-resume' }] }), /slash-command/)
    assert.throws(() => handlers.session!.selectModel!({ sessionId: port.row.id, provider: 'rpnh', model: 'unknown' }), /available/)
    assert.equal(frontend.ctx.get('agentLoop'), undefined)
    assert.equal(frontend.ctx.get('llm'), undefined)
    assert.equal(frontend.ctx.get('tools'), undefined)
    assert.equal(frontend.ctx.get('sessionPersistence'), undefined)
  } finally { await frontend.close() }
})

test('official BFF rejects a second session and reports initial and changed public errors', async () => {
  const port = new ScriptedPort(); port.autoComplete = false
  port.row.error = { code: 'reconciliation_required', message: 'Inspect the RPNH checkpoint.' }
  port.row.turns.push(turn(2, 'stopped_by_owner'))
  const frontend = await startDshFrontend(port)
  const errors: string[] = []
  const off = frontend.ctx.on('api-session/error', (_id, message) => errors.push(message))
  const abort = new AbortController()
  const stream = frontend.view.handlers().session!.follow!({ sessionId: port.row.id }, abort.signal)
  try {
    assert.throws(() => frontend.view.handlers().session!.create!({}), /bound|new session/i)
    await stream.next()
    const next = stream.next()
    await new Promise(done => setImmediate(done))
    assert.ok(errors.some(error => error.includes('reconciliation_required')))
    assert.ok(errors.some(error => error.includes('/rpnh-resume')))
    port.row.error = { code: 'reconciliation_required', message: 'Checkpoint status changed.' }
    await frontend.view.refresh()
    assert.ok(errors.some(error => error.includes('Checkpoint status changed.')))
    assert.equal(port.calls.some(call => ['submit', 'command', 'abort'].includes(call.method)), false)
    abort.abort(); await next
  } finally { abort.abort(); off(); await frontend.close() }
})

function rawHttp(url: string, headers: Record<string, string>, body?: string): Promise<{ status: number; text: string }> {
  return new Promise((done, reject) => {
    const request = httpRequest(url, { method: body === undefined ? 'GET' : 'POST', headers }, response => {
      let text = ''; response.setEncoding('utf8'); response.on('data', chunk => { text += chunk })
      response.on('end', () => done({ status: response.statusCode!, text })); response.on('error', reject)
    }); request.on('error', reject); request.end(body)
  })
}
test('official native connection rejects foreign Host and Origin before mutations', async () => {
  const port = new ScriptedPort(); const frontend = await startDshFrontend(port)
  try {
    await checkFrontendAssets()
    const foreign = await rawHttp(frontend.url, { Host: 'foreign.invalid' })
    assert.equal(foreign.status, 403)
    assert.ok(!foreign.text.includes('__DSH_BOOT__'))
    const api = new URL('/api', frontend.url).href
    assert.equal((await rawHttp(api, { Origin: 'http://foreign.invalid', 'Content-Type': 'application/json' }, '{}')).status, 403)
    assert.equal(port.calls.some(call => ['submit', 'abort', 'command'].includes(call.method)), false)
  } finally { await frontend.close() }
})

async function browserFor(frontend: Awaited<ReturnType<typeof startDshFrontend>>) {
  const { chromium } = await import(new URL('../apps/web/node_modules/playwright/index.mjs', import.meta.url).href)
  return await chromium.launch({ executablePath: process.env.RPNH_DSH_BROWSER, headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'] }).catch(async (error: unknown) => { await frontend.close(); throw error })
}
test('official stock shell auto-opens history and handles native input, stop, resume, commands and EOF', {
  skip: !process.env.RPNH_DSH_BROWSER, timeout: 45000,
}, async () => {
  const port = new ScriptedPort(); const frontend = await startDshFrontend(port)
  const browser = await browserFor(frontend)
  let page
  const errors: string[] = []
  try {
    page = await browser.newPage({ viewport: { width: 1280, height: 900 }, locale: 'en-US' })
    page.setDefaultTimeout(7000)
    page.on('pageerror', (error: Error) => { errors.push(String(error)); console.error('PAGE_ERROR', error) })
    page.on('console', (message: { type(): string; text(): string }) => { if (message.type() === 'error') console.error('BROWSER_ERROR', message.text().slice(0, 600)) })
    await page.goto(frontend.url)
    await page.getByText('Answer 1', { exact: true }).waitFor()
    assert.equal(port.calls.some(call => ['submit', 'command'].includes(call.method)), false)
    const native = await page.evaluate(() => ({ mode: (window as any).__ModuleLoader__.mode,
      plugins: (window as any).__DSH_BOOT__.entries.map((row: any) => row.id) }))
    assert.equal(native.mode, 'live')
    assert.ok(native.plugins.includes('@deepseek-ai/dsh-client-ui-chat'))
    const input = page.getByRole('textbox', { name: /^Message or run a task/ })
    await input.fill('official browser 中文'); await input.press('Enter')
    await page.getByText('Native result: official browser 中文', { exact: true }).waitFor()
    assert.equal(port.calls.filter(call => call.method === 'submit').length, 1)
    port.autoComplete = false
    await input.fill('interrupt official'); await input.press('Enter')
    await page.getByRole('button', { name: 'Stop generating', exact: true }).click()
    await page.getByText(/Paused at the RPNH checkpoint/).waitFor()
    await page.reload()
    await page.getByText('Native result: official browser 中文', { exact: true }).waitFor()
    await page.getByText(/Paused at the RPNH checkpoint/).waitFor()
    assert.equal(port.calls.filter(call => call.method === 'command').length, 0)
    port.autoComplete = true
    await page.getByRole('textbox', { name: /^Message or run a task/ }).fill('/rpnh-resume')
    await page.getByRole('textbox', { name: /^Message or run a task/ }).press('Enter')
    await page.getByText('Native result: interrupt official', { exact: true }).waitFor()
    await page.getByText(/Paused at the RPNH checkpoint/).waitFor({ state: 'hidden' })
    assert.equal(port.calls.filter(call => call.method === 'submit').length, 2)
    assert.equal(port.calls.filter(call => call.method === 'command').length, 1)
    assert.deepEqual(errors, [])
    const promptCall = port.calls.find(call => call.method === 'submit')!
    assert.equal(typeof promptCall.params.requestId, 'string')
    if (process.env.RPNH_DSH_SCREENSHOT) await page.screenshot({ path: process.env.RPNH_DSH_SCREENSHOT, fullPage: true })
    port.close(); await frontend.close()
    await assert.rejects(fetch(new URL('/', frontend.url)))
  } finally {
    if (page && process.env.RPNH_DSH_SCREENSHOT) {
      await page.screenshot({ path: process.env.RPNH_DSH_SCREENSHOT + '.end.png', fullPage: true })
      console.log('FINAL_PAGE_TEXT', (await page.locator('body').innerText()).slice(-3500))
    }
    await browser.close(); await frontend.close()
  }
})
test('official native rollback retains interrupted history without a submit and permits the next input', {
  skip: !process.env.RPNH_DSH_BROWSER, timeout: 30000,
}, async () => {
  const port = new ScriptedPort(); port.autoComplete = false
  const frontend = await startDshFrontend(port); const browser = await browserFor(frontend)
  const errors: string[] = []
  try {
    const page = await browser.newPage({ locale: 'en-US' }); page.setDefaultTimeout(7000)
    page.on('pageerror', (error: Error) => errors.push(String(error)))
    page.on('console', (message: { type(): string; text(): string }) => {
      if (message.type() === 'error') errors.push(message.text())
    })
    await page.goto(frontend.url)
    await page.getByText('Answer 1', { exact: true }).waitFor()
    const input = page.getByRole('textbox', { name: /^Message or run a task/ })
    await input.fill('rollback pending prompt'); await input.press('Enter')
    await page.getByRole('button', { name: 'Stop generating', exact: true }).click()
    await page.getByText(/Paused at the RPNH checkpoint/).waitFor()
    const before = frontend.view.cursor
    await input.fill('/rpnh-rollback'); await input.press('Enter')
    for (let attempt = 0; attempt < 100 && !port.calls.some(call => call.method === 'command'); attempt++) {
      await new Promise(done => setTimeout(done, 25))
    }
    assert.equal(port.row.turns.at(-1)?.state, 'interrupted')
    assert.equal(port.row.turns.length, 2)
    await page.getByText(/Paused at the RPNH checkpoint/).waitFor({ state: 'hidden' })
    await page.getByText('Answer 1', { exact: true }).waitFor()
    await page.getByText('rollback pending prompt', { exact: true }).waitFor()
    assert.equal(await page.getByText(/The DSH process exited before this turn finished/).count(), 0)
    await frontend.view.refresh(); await frontend.view.refresh()
    assert.ok(frontend.view.cursor > before)
    assert.equal(port.calls.filter(call => call.method === 'submit').length, 1)
    assert.deepEqual(port.calls.filter(call => call.method === 'command').map(call => call.params.name), ['rpnh-rollback'])
    port.autoComplete = true
    await input.fill('after official rollback'); await input.press('Enter')
    await page.getByText('Native result: after official rollback', { exact: true }).waitFor()
    assert.equal(port.calls.filter(call => call.method === 'submit').length, 2)
    assert.equal(port.row.turns[1]!.state, 'interrupted')
    assert.equal(port.row.turns[1]!.answer, null)
    assert.equal(port.row.turns.at(-1)?.state, 'committed')
    assert.deepEqual(errors, [])
    if (process.env.RPNH_DSH_SCREENSHOT) await page.screenshot({ path: process.env.RPNH_DSH_SCREENSHOT + '.rollback.png', fullPage: true })
  } finally { await browser.close(); await frontend.close() }
})
test('official native model selector switches A/high to B/no-effort and retains it across polling', {
  skip: !process.env.RPNH_DSH_BROWSER, timeout: 30000,
}, async () => {
  const modelA = { ...profile, selection: 'rpnh:A', model: 'A', reasoning_effort: 'high',
    supported_reasoning_efforts: ['low', 'high'], default_reasoning_effort: 'high' }
  const modelB = { ...profile, selection: 'rpnh:B', model: 'B' }
  class Models extends ScriptedPort {
    override async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
      const result = await super.request<T>(method, params)
      if (method === 'initialize') (result as Initialization).configuration.profiles = [modelA, modelB]
      return result
    }
  }
  const port = new Models(); port.row.model = modelA
  const frontend = await startDshFrontend(port); const browser = await browserFor(frontend)
  try {
    const page = await browser.newPage({ locale: 'en-US' }); page.setDefaultTimeout(7000)
    page.on('pageerror', (error: Error) => console.error('PAGE_ERROR', error))
    await page.goto(frontend.url)
    await page.getByText('Answer 1', { exact: true }).waitFor()
    await page.getByRole('button', { name: /^Select model/ }).click()
    console.log('MODEL_MENU', (await page.locator('body').innerText()).slice(-1500))
    await page.getByRole('menuitem', { name: /^Model/ }).click()
    await page.getByRole('menuitemradio', { name: 'Offline / B', exact: true }).click()
    await page.getByRole('button', { name: /^Select model, current Offline \/ B$/ }).waitFor()
    const reads = port.calls.filter(call => call.method === 'snapshot').length
    for (let attempt = 0; attempt < 100 && port.calls.filter(call => call.method === 'snapshot').length < reads + 2; attempt++) {
      await new Promise(done => setTimeout(done, 25))
    }
    assert.ok(port.calls.filter(call => call.method === 'snapshot').length >= reads + 2)
    await page.getByRole('textbox', { name: /^Message or run a task/ }).fill('selected native B')
    await page.getByRole('textbox', { name: /^Message or run a task/ }).press('Enter')
    await page.getByText('Native result: selected native B', { exact: true }).waitFor()
    const submission = port.calls.find(call => call.method === 'submit')!
    assert.equal(submission.params.selectionId, modelB.selection)
    assert.equal(Object.hasOwn(submission.params, 'reasoningEffort'), false)
  } finally { await browser.close(); await frontend.close() }
})
test('actual CLI uses inherited FD3/FD4 and exits after owner EOF', async () => {
  const port = new ScriptedPort()
  const child = spawn(process.execPath, ['--import', 'tsx', fileURLToPath(new URL('./frontend.ts', import.meta.url)),
    '--read-fd', '3', '--write-fd', '4', '--no-open-browser'], {
    cwd: fileURLToPath(new URL('../', import.meta.url)),
    env: { ...process.env, TMPDIR: resolve(process.env.TMPDIR ?? process.cwd()) },
    stdio: ['ignore', 'pipe', 'pipe', 'pipe', 'pipe'] })
  const reader = child.stdio[4]! as NodeJS.ReadableStream
  const writer = child.stdio[3]! as NodeJS.WritableStream
  let buffer = ''; let stderr = ''; let stdout = ''
  child.stderr!.on('data', chunk => { stderr += chunk })
  reader.on('data', chunk => {
    buffer += chunk
    let end: number
    while ((end = buffer.indexOf('\n')) >= 0) {
      const frame = JSON.parse(buffer.slice(0, end)); buffer = buffer.slice(end + 1)
      void port.request(frame.method, frame.params).then(result => {
        writer.write(JSON.stringify({ protocol: PROTOCOL, id: frame.id, result }) + '\n')
      })
    }
  })
  const exited = new Promise<number | null>(done => child.once('exit', done))
  try {
    const url = await new Promise<string>((done, reject) => {
      const timeout = setTimeout(() => reject(new Error(`CLI startup timeout: ${stderr}`)), 15000)
      child.stdout!.on('data', chunk => {
        stdout += chunk
        const match = stdout.match(/http:\/\/127\.0\.0\.1:\d+\/\S*/)
        if (match) { clearTimeout(timeout); done(match[0]) }
      })
      child.once('exit', code => { clearTimeout(timeout); reject(new Error(`CLI exited ${code}: ${stderr}`)) })
    })
    assert.match(stdout, /bound-main-session/)
    assert.equal(port.calls.some(call => ['submit', 'abort', 'command'].includes(call.method)), false)
    assert.equal((await fetch(url, { redirect: 'manual' })).status, 303)
    writer.end()
    assert.equal(await exited, 0, stderr)
    await assert.rejects(fetch(url))
  } finally { if (!(writer as any).destroyed) writer.end(); if (child.exitCode === null) child.kill('SIGTERM') }
})
