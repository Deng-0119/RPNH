import { Context } from '@deepseek-ai/cordis'
import AgentLoop from '@deepseek-ai/dsh-agent-loop'
import type { AgentMachine } from '@deepseek-ai/dsh-agent-loop'
import { agentEvents } from '@deepseek-ai/dsh-agent'
import type { AgentOptions, AgentCancelCause, InboxTarget } from '@deepseek-ai/dsh-agent'
import type { Session, SessionId, UserMessage } from '@deepseek-ai/dsh-session'
import { createScope } from '@deepseek-ai/dsh-scope'
import type { Scope } from '@deepseek-ai/dsh-scope'
import { ReactLoopInbox } from '../packages/core/agent-loop/src/inbox.ts'
import { REVISION, type OwnerClient, type JsonRecord } from './bridge.ts'
import { appendTurn } from './projection.ts'
import './capabilities.ts'

/** DSH lifecycle and queues above the existing RPNH execution scheduler. */
export class RegistryAgent implements AgentMachine {
  readonly scope: Scope
  readonly ctx: Context
  readonly inbox: ReactLoopInbox
  readonly client: OwnerClient
  private phase: 'idle' | 'running' | 'maintenance' = 'idle'
  private activity: Promise<void> | undefined
  private controller: AbortController | undefined
  private wakeRequested = false
  private disposed = false
  private halted = false
  private projected = 0
  lastOutcome: JsonRecord | undefined
  /** Transport failures are transient UI diagnostics, not formal Session events. */
  lastError: string | undefined
  constructor(loopCtx: Context, readonly id: SessionId, readonly options: AgentOptions, readonly session: Session) {
    const selected = loopCtx.rpnhBridge.model
    if (options.provider !== selected.provider || options.model !== selected.model || options.reasoningEffort !== undefined || options.maxTokens !== undefined) throw new Error('select the exact launcher-managed profile; no implicit model fallback or per-turn route override')
    this.scope = createScope(loopCtx, this); this.ctx = this.scope.ctx
    this.inbox = new ReactLoopInbox(loopCtx.sessionProjections, session, agentEvents(loopCtx, this))
    this.client = loopCtx.rpnhBridge.client(id, false)
    if (!this.client.historySnapshot) throw new Error('Registry history must be read before driver construction')
    this.halted = this.client.historySnapshot.active !== null
    this.client.host = loopCtx.rpnhCapabilities.forAgent(this)
    this.projected = session.snapshotEvents().filter(e => e.type === 'turn/end' && ['completed','blocked'].includes(e.data.reason.kind)).length
  }
  get status(): 'idle' | 'running' { return this.phase === 'running' ? 'running' : 'idle' }
  send(message: UserMessage, target: InboxTarget, wakeup: boolean): void {
    if (this.disposed) throw new Error('agent is disposed')
    if (this.halted) throw new Error('Registry request needs explicit recovery, not a new prompt')
    if (target === 'next-step' && this.phase === 'running') throw new Error('mid-flight steering requires a registered edit and is not enabled in this profile')
    this.inbox.append(target, message)
    if (wakeup) { this.wakeRequested = true; this.wake() }
  }
  followup(message: UserMessage): void { this.send(message, 'next-turn', true) }
  steer(message: UserMessage): void { this.send(message, 'next-step', true) }
  inject(message: UserMessage): void { this.send(message, 'next-step', false) }
  cancel(cause: AgentCancelCause, options: { keepInbox?: boolean } = {}): void {
    if (cause.kind === 'disposed') this.disposed = true
    if (this.phase === 'idle') return
    if (!options.keepInbox) this.inbox.clear()
    this.wakeRequested = false
    this.controller?.abort(cause)
    this.client.cancel()
  }
  async whenIdle(): Promise<void> { while (this.activity) await this.activity }
  runMaintenance<T>(task: (signal: AbortSignal) => Promise<T>): Promise<T> {
    if (this.phase !== 'idle' || this.disposed) throw new Error('agent is not idle for maintenance')
    this.phase = 'maintenance'; this.controller = new AbortController()
    const operation = Promise.resolve().then(() => task(this.controller!.signal))
    this.activity = operation.then(() => undefined, () => undefined).finally(() => {
      this.activity = undefined; this.phase = 'idle'; this.controller = undefined; this.wake()
    })
    return operation
  }
  private wake(): void {
    if (this.phase !== 'idle' || !this.wakeRequested || this.disposed || this.halted || !this.inbox.hasPending) return
    this.wakeRequested = false; this.phase = 'running'; this.controller = new AbortController()
    this.activity = this.ctx.agents.withInitiator(this, () => this.drive()).finally(() => {
      this.activity = undefined; this.phase = 'idle'; this.controller = undefined
      agentEvents(this.ctx, this).emit('agent/status', { status: 'idle' })
      if (this.inbox.hasPending && !this.halted) this.wakeRequested = true
      this.wake()
    })
    agentEvents(this.ctx, this).emit('agent/status', { status: 'running' })
  }
  private async drive(): Promise<void> {
    const turn = this.projected + 1
    this.lastError = undefined; this.lastOutcome = undefined
    try {
      const messages = this.inbox.claim('next-turn', turn)
      if (!messages.length) return
      const config = this.ctx.rpnhBridge.config
      const route = this.ctx.rpnhBridge.model
      const request = { session_id: this.id, request_id: messages.at(-1)!.id, upstream_revision: REVISION,
        route, messages,
        header: this.session.header, data: config.data,
        policy: { allow_request: config.allowRequest, tools: config.tools },
        ...(config.execution.kind === 'configured'
          ? { execution_profile: config.execution.profile }
          : {}) }
      this.lastOutcome = await this.client.request('turn', request)
      if (this.lastOutcome.status !== 'terminal') { this.halted = true; return }
      await this.refresh()
    } catch (error) {
      this.halted = true
      this.lastError = String(error)
    }
  }
  private async refresh(): Promise<void> {
    const history = await this.client.request('history')
    for (const record of history.committed_history.slice(this.projected)) appendTurn(this.session, record)
    this.projected = history.committed_history.length
  }
  /** Explicit recovery, never inferred from idle or UI history. */
  async resumeActive(): Promise<JsonRecord> {
    return await this.runMaintenance(async () => {
      const outcome = await this.client.request('resume')
      this.lastOutcome = outcome
      if (outcome.status === 'terminal' || outcome.status === 'idle') {
        this.halted = false; this.lastError = undefined; await this.refresh()
      }
      return outcome
    })
  }
}
export class RegistryAgentLoop extends AgentLoop {
  static inject = [...AgentLoop.inject, 'rpnhBridge', 'rpnhCapabilities', 'sessionPersistence']
  protected override createMachine(ctx: Context, id: SessionId, options: AgentOptions, session: Session): AgentMachine {
    return new RegistryAgent(ctx, id, options, session)
  }
}
