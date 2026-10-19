import test from 'node:test'
import assert from 'node:assert/strict'
import { PassThrough } from 'node:stream'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { request as httpRequest } from 'node:http'
import { createFrontendApplication } from './agent.ts'
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
      if (params.name === 'rpnh-rollback') this.row.turns.pop()
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
test('native factory loads existing history read-only and follows the actual ordinal', async () => {
  const port = new ScriptedPort(); const app = await createFrontendApplication(port)
  try {
    assert.deepEqual(port.calls.map(call => call.method), ['initialize'])
    assert.equal(app.ctx.agents.get(app.machine.id), app.machine)
    assert.equal(app.ctx.sessions.get(app.machine.id), app.machine.session)
    assert.equal(app.ctx.get('sessionPersistence'), undefined)
    const result = await app.machine.submit('new prompt', 'stable-request')
    assert.equal(result.ordinal, 2)
    await app.machine.whenIdle()
    assert.equal(app.machine.row.turns.at(-1)!.state, 'committed')
    assert.equal(app.machine.session.snapshotEvents().filter(event => event.type === 'assistant/message').length, 2)
    assert.equal(port.calls.filter(call => call.method === 'submit').length, 1)
    assert.equal(port.calls.filter(call => call.method === 'snapshot').length, 3)
  } finally { await app.close() }
})
test('native cancellation reaches RPC and paused initialization never resumes', async () => {
  const port = new ScriptedPort(); port.autoComplete = false; port.row.turns.push(turn(2, 'stopped_by_owner'))
  const app = await createFrontendApplication(port)
  try {
    await app.machine.refresh(); await app.machine.whenIdle()
    assert.equal(port.calls.some(call => ['submit', 'command', 'abort'].includes(call.method)), false)
    await port.request('command', { name: 'rpnh-rollback', arguments: '', requestId: 'explicit-rollback' })
    await app.rebuild()
    await app.machine.submit('stop prompt', 'stop-request')
    app.machine.cancel({ kind: 'user' }); await app.machine.whenIdle()
    assert.equal(app.machine.row.turns.at(-1)!.state, 'stopped_by_owner')
    assert.equal(port.calls.filter(call => call.method === 'abort').length, 1)
  } finally { await app.close() }
})
test('real loopback Web serves official assets and bound API, rejects foreign authority, closes on EOF', async () => {
  await checkFrontendAssets()
  const port = new ScriptedPort(); const frontend = await startDshFrontend(port)
  try {
    const page = await (await fetch(frontend.url)).text()
    assert.match(page, /DSH · RPNH MainSession/)
    const token = page.match(/name="rpnh-token" content="([^"]+)"/)![1]
    const headers = { 'x-rpnh-token': token!, 'Content-Type': 'application/json' }
    const snapshot = await (await fetch(frontend.url + 'api/snapshot', { headers })).json()
    assert.equal(snapshot.session.id, 'bound-main-session')
    assert.deepEqual(snapshot.capabilities.tools, [])
    assert.equal((await fetch(frontend.url + 'api/snapshot')).status, 403)
    const invalid = await fetch(frontend.url + 'api/submit', { method: 'POST', headers,
      body: JSON.stringify({ text: 'do not run', requestId: 'x', sessionId: 'other' }) })
    assert.equal(invalid.status, 400)
    assert.equal(port.calls.some(call => call.method === 'submit'), false)
    const valid = await fetch(frontend.url + 'api/submit', { method: 'POST', headers,
      body: JSON.stringify({ text: 'web prompt', requestId: 'stable-web' }) })
    assert.equal(valid.status, 200)
    await frontend.app.machine.whenIdle()
    assert.equal(frontend.app.machine.row.turns.at(-1)!.answer!.reply, 'Native result: web prompt')
    const command = await fetch(frontend.url + 'api/command', { method: 'POST', headers,
      body: JSON.stringify({ name: 'rpnh-rollback', arguments: '', requestId: 'rollback' }) })
    assert.equal(command.status, 200)
    assert.equal(frontend.app.machine.session.snapshotEvents().filter(event => event.type === 'user/message').length, 1)
    port.close(); await frontend.close()
    await assert.rejects(fetch(frontend.url))
  } finally { await frontend.close() }
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
        const match = stdout.match(/http:\/\/127\.0\.0\.1:\d+\//)
        if (match) { clearTimeout(timeout); done(match[0]) }
      })
      child.once('exit', code => { clearTimeout(timeout); reject(new Error(`CLI exited ${code}: ${stderr}`)) })
    })
    assert.match(stdout, /bound-main-session/)
    assert.deepEqual(port.calls.map(call => call.method), ['initialize'])
    assert.equal((await fetch(url)).status, 200)
    writer.end()
    assert.equal(await exited, 0, stderr)
    await assert.rejects(fetch(url))
  } finally { if (child.exitCode === null) child.kill('SIGTERM') }
})

test('real browser uses official composer and chat widgets for input/history/stop/explicit commands', {
  skip: !process.env.RPNH_DSH_BROWSER, timeout: 30000,
}, async () => {
  const { chromium } = await import('../apps/web/node_modules/playwright/index.mjs')
  const port = new ScriptedPort(); const frontend = await startDshFrontend(port)
  const browser = await chromium.launch({ executablePath: process.env.RPNH_DSH_BROWSER, headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'] }).catch(async error => { await frontend.close(); throw error })
  const errors: string[] = []
  try {
    const page = await browser.newPage({ viewport: { width: 1200, height: 900 } })
    page.setDefaultTimeout(5000)
    page.on('pageerror', error => { errors.push(String(error)); console.error('Browser error:', error) })
    page.on('console', message => { if (message.type() === 'error') console.error('Browser console:', message.text().slice(0, 400)) })
    page.on('requestfailed', request => console.error('Browser request failed:', request.url(), request.failure()))
    await page.goto(frontend.url)
    await page.getByText('Cross frontend prompt 1', { exact: true }).waitFor()
    await page.getByText('Answer 1', { exact: true }).waitFor()
    assert.equal(port.calls.some(call => call.method === 'submit'), false)
    const input = page.getByRole('textbox', { name: 'Message' })
    await input.fill('browser prompt 中文'); await page.getByRole('button', { name: 'Send', exact: true }).click()
    await page.getByText('Native result: browser prompt 中文', { exact: true }).waitFor()
    port.autoComplete = false
    await input.fill('interrupt me'); await input.press('Enter')
    await page.getByText('interrupt me', { exact: true }).waitFor()
    await page.getByRole('button', { name: 'Stop', exact: true }).click()
    await page.getByText('Paused at the RPNH checkpoint. Use /rpnh-resume or /rpnh-rollback.', { exact: true }).waitFor()
    await page.reload()
    await page.getByText('Paused at the RPNH checkpoint. Use /rpnh-resume or /rpnh-rollback.', { exact: true }).waitFor()
    assert.equal(port.calls.some(call => call.method === 'command'), false)
    port.autoComplete = true
    await input.fill('/rpnh-resume'); await input.press('Enter')
    await page.getByText('Native result: interrupt me', { exact: true }).waitFor()
    assert.equal(port.calls.filter(call => call.method === 'submit').length, 2)
    assert.equal(port.calls.filter(call => call.method === 'command').length, 1)
    if (process.env.RPNH_DSH_SCREENSHOT) await page.screenshot({ path: process.env.RPNH_DSH_SCREENSHOT, fullPage: true })
    assert.deepEqual(errors, [])
    port.close(); await frontend.close()
    await page.getByText(/Disconnected:/).waitFor()
    assert.equal(await page.getByRole('button', { name: 'Send', exact: true }).isDisabled(), true)
  } finally { await browser.close(); await frontend.close() }
})

function rawHttp(url: string, headers: Record<string, string>, body?: string): Promise<{ status: number; text: string }> {
  return new Promise((resolveResponse, reject) => {
    const request = httpRequest(url, { method: body === undefined ? 'GET' : 'POST', headers }, response => {
      let text = ''
      response.setEncoding('utf8')
      response.on('data', chunk => { text += chunk })
      response.on('end', () => resolveResponse({ status: response.statusCode!, text }))
      response.on('error', reject)
    })
    request.on('error', reject)
    request.end(body)
  })
}

test('release blocker: foreign Host cannot obtain a page token or call RPC', async () => {
  const port = new ScriptedPort(); const frontend = await startDshFrontend(port)
  try {
    const page = await (await fetch(frontend.url)).text()
    const token = page.match(/name="rpnh-token" content="([^"]+)"/)![1]!
    const before = port.calls.length
    const responses = []
    for (const host of ['foreign.invalid', 'localhost:' + new URL(frontend.url).port, '127.0.0.1:1']) {
      responses.push(await rawHttp(frontend.url, { Host: host }))
      responses.push(await rawHttp(frontend.url + 'api/abort', {
        Host: host, 'x-rpnh-token': token, 'Content-Type': 'application/json',
      }, '{}'))
    }
    assert.deepEqual(responses.map(response => response.status), [403, 403, 403, 403, 403, 403])
    assert.ok(responses.every(response => !response.text.includes(token) && !response.text.includes('rpnh-token')))
    assert.equal(port.calls.length, before, 'rejected Host requests must not reach RPC')
  } finally { await frontend.close() }
})

test('release blocker: foreign Origin cannot read or mutate via a valid local token', async () => {
  const port = new ScriptedPort(); const frontend = await startDshFrontend(port)
  try {
    const page = await (await fetch(frontend.url)).text()
    const token = page.match(/name="rpnh-token" content="([^"]+)"/)![1]!
    const headers = { 'x-rpnh-token': token, 'Content-Type': 'application/json' }
    const before = port.calls.length
    const responses = []
    for (const origin of ['http://foreign.invalid', 'null', 'http://127.0.0.1:1', frontend.url.replace('http:', 'https:').slice(0, -1)]) {
      responses.push(await rawHttp(frontend.url + 'api/snapshot', { ...headers, Origin: origin }))
      responses.push(await rawHttp(frontend.url + 'api/abort', { ...headers, Origin: origin }, '{}'))
    }
    assert.deepEqual(responses.map(response => response.status), Array(8).fill(403))
    assert.equal(port.calls.length, before, 'rejected Origin requests must not reach RPC')
    const sameOrigin = { ...headers, Origin: new URL(frontend.url).origin }
    assert.equal((await rawHttp(frontend.url + 'api/snapshot', sameOrigin)).status, 200)
    assert.equal((await rawHttp(frontend.url + 'api/abort', sameOrigin, '{}')).status, 200)
    assert.equal(port.calls.length, before + 2)
  } finally { await frontend.close() }
})

test('release blocker: browser keeps model B without effort across polls after model A high', {
  skip: !process.env.RPNH_DSH_BROWSER, timeout: 30000,
}, async () => {
  const { chromium } = await import('../apps/web/node_modules/playwright/index.mjs')
  const modelA = { ...profile, selection: 'rpnh:A', model: 'A', reasoning_effort: 'high',
    supported_reasoning_efforts: ['low', 'high'], default_reasoning_effort: 'high' }
  const modelB = { ...profile, selection: 'rpnh:B', model: 'B' }
  class ModelPort extends ScriptedPort {
    override async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
      const result = await super.request<T>(method, params)
      if (method !== 'initialize') return result
      const initial = result as Initialization
      initial.configuration = { default_selection: modelA.selection, default_reasoning_effort: 'high', profiles: [modelA, modelB] }
      return initial as T
    }
  }
  const port = new ModelPort(); port.row.model = modelA
  const frontend = await startDshFrontend(port)
  const browser = await chromium.launch({ executablePath: process.env.RPNH_DSH_BROWSER, headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'] }).catch(async error => { await frontend.close(); throw error })
  try {
    const page = await browser.newPage()
    page.setDefaultTimeout(5000)
    await page.goto(frontend.url)
    const model = page.getByRole('combobox', { name: 'Model', exact: true })
    const reasoning = page.getByRole('combobox', { name: 'Reasoning', exact: true })
    await reasoning.waitFor()
    assert.equal(await reasoning.inputValue(), 'high')
    await model.selectOption(modelB.selection)
    const before = port.calls.filter(call => call.method === 'snapshot').length
    for (let index = 0; index < 2; index++) {
      const response = await page.waitForResponse(response => response.url().endsWith('/api/snapshot'))
      await response.finished()
    }
    await page.evaluate(() => new Promise<void>(resolveFrame => requestAnimationFrame(() => resolveFrame())))
    assert.ok(port.calls.filter(call => call.method === 'snapshot').length >= before + 2)
    assert.equal(await model.inputValue(), modelB.selection)
    assert.equal(await reasoning.count(), 0)
    await page.getByRole('textbox', { name: 'Message' }).fill('model B no effort')
    await page.getByRole('button', { name: 'Send', exact: true }).click()
    await page.getByText('Native result: model B no effort', { exact: true }).waitFor()
    const submits = port.calls.filter(call => call.method === 'submit')
    assert.equal(submits.length, 1)
    assert.equal(submits[0]!.params.selectionId, modelB.selection)
    assert.equal(Object.hasOwn(submits[0]!.params, 'reasoningEffort'), false,
      `model B must omit effort; actual submit: ${JSON.stringify(submits[0]!.params)}`)
  } finally { await browser.close(); await frontend.close() }
})
