/** Adversarial scheduling with genuine DSH services, real Registry and PN. */
import { it, expect } from 'vitest'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { execFileSync } from 'node:child_process'
import { SessionId } from '@deepseek-ai/dsh-session'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { createApplication } from './app.ts'
import { RegistryAgent } from './agent.ts'
import { OwnerClient, REVISION, type JsonRecord } from './bridge.ts'

const options = { provider: 'rpnh-offline', model: 'deterministic-v1' }
const python = execFileSync('sh', ['-c', 'command -v python3']).toString().trim()
const offline = { execution: { kind: 'offline' as const } }
const message = (text: string) => createUserMessage({ source: { kind: 'user' }, content: [{ type: 'text', text }] })
const deferred = <T>() => {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}

it('never dispatches an effect after the owner transport is closed', async () => {
  const client = Object.create(OwnerClient.prototype) as any
  let calls = 0
  client.id = 'closed'
  client.closed = true
  client.active = undefined
  client.host = async () => { calls++; return {} }
  client.receive({ kind: 'effect', id: 'late', ticket: {
    session_id: 'closed', upstream_revision: REVISION,
  } })
  await Promise.resolve()
  expect(calls).toBe(0)
})

it('cancels before the tool side effect, cold-reopens halted, and explicitly resumes the same registered request', async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'rpnh-dsh-cancel-'))
  const root = join(temporary, 'long-session-root-'.repeat(8))
  expect(Buffer.byteLength(join(root, 'stopped', 'owner.sock'))).toBeGreaterThanOrEqual(108)
  const config = { root, python, data: [2, 3, 7], allowRequest: true,
    tools: ['read_dataset', 'sum_values'], ...offline }
  const first = await createApplication(config)
  let second: Awaited<ReturnType<typeof createApplication>> | undefined
  try {
    const handle = await first.agents.create({ sessionId: SessionId('stopped'), agentOptions: options })
    const a = handle.agent as RegistryAgent
    const statuses: string[] = []
    first.on('agent/status', ({ agent, status }) => { if (agent === a) statuses.push(status) })
    const host = a.client.host
    let stopped = false
    a.client.host = async (frame, signal) => {
      if (!stopped && frame.ticket.kind === 'tool') {
        stopped = true
        a.cancel({ kind: 'user' })
        signal.throwIfAborted()
      }
      return await host(frame, signal)
    }
    a.followup(message('read and sum'))
    await a.whenIdle()
    expect(a.lastOutcome?.status, a.lastError).toBe('stopped_by_owner')
    expect(statuses).toEqual(['running', 'idle'])
    expect(first.rpnhCapabilities.counters).toMatchObject({ model: 1, tools: 0 })
    expect(a.session.snapshotEvents().filter(e => e.type === 'assistant/message')).toEqual([])
    await expect(first.sessions.flush(a.session)).rejects.toThrow('not terminal')
    const oldHistory = await a.client.request('history')
    const firstTickets = [...first.rpnhCapabilities.counters.tickets]
    await handle.dispose()
    await first.fiber.dispose()

    // Different application/owner process; the changed default data MUST NOT
    // replace the already registered snapshot while resuming.
    second = await createApplication({ ...config, data: [999] })
    const reopened = await second.agents.resume({ resumeSessionId: SessionId('stopped'), agentOptions: options })
    const b = reopened.agent as RegistryAgent
    expect(() => b.followup(message('not a recovery'))).toThrow('explicit recovery')
    expect(second.rpnhCapabilities.counters).toMatchObject({ model: 0, tools: 0 })
    expect((await b.client.request('history')).active_turn_ref).toEqual(oldHistory.active_turn_ref)
    const outcome = await b.resumeActive()
    await b.whenIdle()
    expect(outcome.answer?.text).toBe('Sum: 12')
    expect(second.rpnhCapabilities.counters).toMatchObject({ model: 2, tools: 2 })
    expect(new Set([...firstTickets, ...second.rpnhCapabilities.counters.tickets]).size).toBe(5)
    await second.sessions.flush(b.session)
    expect((await b.client.request('history')).active).toBeNull()
    await reopened.dispose()
  } finally {
    await second?.fiber.dispose()
    await first.fiber.dispose()
    await rm(temporary, { recursive: true, force: true })
  }
}, 300_000)

it('interleaves two registered requests without mixing tickets, numeric snapshots, or histories', async () => {
  const root = await mkdtemp(join(tmpdir(), 'rpnh-dsh-overlap-'))
  const config = { root, python, data: [2, 3, 7], allowRequest: true,
    tools: ['read_dataset', 'sum_values'], ...offline }
  const ctx = await createApplication(config)
  const release = deferred<void>()
  const held = deferred<JsonRecord>()
  try {
    const one = await ctx.agents.create({ sessionId: SessionId('one'), agentOptions: options })
    const two = await ctx.agents.create({ sessionId: SessionId('two'), agentOptions: options })
    const a = one.agent as RegistryAgent; const b = two.agent as RegistryAgent
    const host = a.client.host
    let paused = false
    a.client.host = async (frame, signal) => {
      if (!paused) { paused = true; held.resolve(frame); await release.promise }
      return await host(frame, signal)
    }
    a.followup(message('sum request one'))
    const frame = await held.promise
    await expect(b.client.host(frame, new AbortController().signal)).rejects.toThrow('foreign execution')
    expect(ctx.rpnhCapabilities.counters).toMatchObject({ model: 0, tools: 0 })
    expect(() => a.runMaintenance(async () => undefined)).toThrow('not idle')
    config.data = [10, 20]
    b.followup(message('sum request two'))
    await b.whenIdle()
    expect(b.lastOutcome?.answer?.text, b.lastError).toBe('Sum: 30')
    expect(a.status).toBe('running')
    release.resolve()
    await a.whenIdle()
    expect(a.lastOutcome?.answer?.text, a.lastError).toBe('Sum: 12')
    expect(ctx.rpnhCapabilities.counters).toMatchObject({ model: 6, tools: 4 })
    expect(new Set(ctx.rpnhCapabilities.counters.tickets).size).toBe(10)
    const ah = await a.client.request('history'); const bh = await b.client.request('history')
    expect(ah.committed_history[0].user_input.data).toEqual([2, 3, 7])
    expect(bh.committed_history[0].user_input.data).toEqual([10, 20])
    expect(ah.committed_history[0].user_input.request_id).not.toBe(bh.committed_history[0].user_input.request_id)
    await ctx.sessions.flush(a.session); await ctx.sessions.flush(b.session)
    await one.dispose(); await two.dispose()
  } finally {
    release.resolve()
    await ctx.fiber.dispose()
    await rm(root, { recursive: true, force: true })
  }
}, 300_000)
