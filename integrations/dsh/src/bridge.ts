import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { Context, Service } from '@deepseek-ai/cordis'
export const REVISION = 'ddefc45fbc7f8e46dd73185e68295696d1297887'
export const PROTOCOL = 'rpnh/dsh/v1'
export const MANAGED_TOOL_RESULT_LIMIT = 16 * 1024 * 1024
export const LIMIT = 4 * MANAGED_TOOL_RESULT_LIMIT + 4 * 1024 * 1024
export type JsonRecord = Record<string, any>
export type PublicExecutionProfile = Readonly<{
  schema_version: 'rpnh/dsh_execution_profile/v2' | 'rpnh/dsh_execution_profile/v3'
  profile: string
  selection_id: string
  provider: string
  provider_display_name: string
  model_condition: string
  adapter_kind: 'external_provider' | 'local_process'
  transport_kind: string
  timeout_seconds: number
  max_output_tokens: number
  max_response_bytes: number
  reasoning_effort?: string | null
  supported_reasoning_efforts?: readonly string[]
  default_reasoning_effort?: string | null
}>
export type BridgeExecution = (
  | Readonly<{ execution: Readonly<{ kind: 'offline' }> }>
  | Readonly<{ execution: Readonly<{
      kind: 'configured'
      selectionPath: string
      profile: PublicExecutionProfile
    }> }>
)
export type ManagedToolSelection = Readonly<{ name: string; selector: string }>
export interface BridgeProcessConfig {
  root: string
  python: string
  execution?: BridgeExecution['execution']
  pluginConfigPath?: string
  managedTools?: readonly ManagedToolSelection[]
  managedBindingsPath?: string
  attemptBudget?: number | null
}
export interface BridgeRuntimeConfig extends BridgeProcessConfig {
  data: number[]
  allowRequest: boolean
  tools: string[]
}
export type BridgeConfig = BridgeRuntimeConfig & BridgeExecution
export type SelectedModel = Readonly<{ provider: string; model: string }>
export function selectedModel(config: BridgeConfig): SelectedModel {
  return Object.freeze(config.execution.kind === 'offline'
    ? { provider: 'rpnh-offline', model: 'deterministic-v1' }
    : { provider: config.execution.profile.provider, model: config.execution.profile.model_condition })
}
export type EffectHost = (request: JsonRecord, signal: AbortSignal) => Promise<JsonRecord>

/** Private bidirectional pipe, never an asynchronous authority/log replica. */
export class OwnerClient {
  readonly process: ChildProcessWithoutNullStreams
  private pending = new Map<string, { resolve: (v: any) => void; reject: (e: Error) => void }>()
  private buffer = Buffer.alloc(0)
  private active: AbortController | undefined
  private closed = false
  private readyPromise: Promise<void>
  private stderr = ''
  /** Last read-only Registry observation; never a source of durable truth. */
  historySnapshot: JsonRecord | undefined
  host: EffectHost = async () => { throw new Error('DSH effect host is not installed') }
  constructor(readonly id: string, config: BridgeProcessConfig, create: boolean) {
    if (!/^[A-Za-z0-9_-]{1,100}$/.test(id)) throw new Error('invalid session id')
    const executionArguments = config.execution?.kind === 'offline'
      ? ['--offline']
      : config.execution?.kind === 'configured'
        ? ['--execution-path', config.execution.selectionPath]
        : []
    const managedToolArguments = config.pluginConfigPath === undefined
      ? []
      : ['--plugin-config', config.pluginConfigPath,
        ...(config.managedBindingsPath === undefined
          ? (config.managedTools ?? []).flatMap(tool => [
              '--managed-tool', `${tool.name}=${tool.selector}`])
          : ['--managed-bindings', config.managedBindingsPath])]
    const attemptBudgetArguments = config.attemptBudget === undefined
      ? []
      : ['--attempt-budget', config.attemptBudget === null ? 'unmetered' : String(config.attemptBudget)]
    this.process = spawn(config.python, ['-u', '-m', 'cpn.dsh.server', '--root', config.root,
      '--session', id, ...executionArguments, ...managedToolArguments,
      ...attemptBudgetArguments,
      ...(create ? ['--create'] : [])], { stdio: ['pipe', 'pipe', 'pipe'] })
    this.readyPromise = new Promise<void>((resolve, reject) => { this.pending.set('ready', { resolve, reject }) })
    this.process.stderr.on('data', chunk => { this.stderr = (this.stderr + chunk.toString()).slice(-4096) })
    this.process.on('error', error => this.fail(error))
    this.process.on('exit', code => this.fail(new Error(`Registry owner exited (${code}); no replay. ${this.stderr}`)))
    this.process.stdout.on('data', chunk => {
      if (this.closed) return
      this.buffer = Buffer.concat([this.buffer, chunk])
      if (this.buffer.length > LIMIT && !this.buffer.includes(10)) { this.fail(new Error('oversized frame')); return }
      for (;;) {
        if (this.closed) return
        const i = this.buffer.indexOf(10)
        if (i < 0) break
        const line = this.buffer.subarray(0, i); this.buffer = this.buffer.subarray(i + 1)
        try {
          if (line.length > LIMIT) throw new Error('oversized frame')
          const frame = JSON.parse(line.toString('utf8'))
          if (frame.protocol !== PROTOCOL) throw new Error('protocol mismatch')
          this.receive(frame)
        } catch (error) {
          this.fail(error instanceof Error ? error : new Error(String(error)))
          return
        }
      }
    })
  }
  private receive(frame: JsonRecord): void {
    if (this.closed) return
    if (frame.kind === 'effect') {
      if (this.active !== undefined || frame.ticket.session_id !== this.id || frame.ticket.upstream_revision !== REVISION) {
        this.fail(new Error('unexpected, overlapping, or mismatched execution')); return
      }
      const controller = new AbortController(); this.active = controller
      void this.host(frame, controller.signal).then(value => ({ ticket: frame.ticket, value }),
        error => ({ ticket: frame.ticket, error: String(error) })).then(result => {
          this.active = undefined
          if (!this.closed) this.write({ kind: 'effect-result', id: frame.id, result })
        }).catch(error => this.fail(
          error instanceof Error ? error : new Error(String(error))))
      return
    }
    const id = frame.kind === 'ready' ? 'ready' : frame.id
    const pending = this.pending.get(id)
    if (!pending) { this.fail(new Error('unknown reply correlation')); return }
    this.pending.delete(id)
    if (frame.error) pending.reject(new Error(`${frame.error.type}: ${frame.error.message}`))
    else pending.resolve(frame.result)
  }
  private write(frame: JsonRecord): void {
    if (this.closed) throw new Error('Registry owner is closed')
    const data = JSON.stringify({ protocol: PROTOCOL, ...frame }) + '\n'
    if (Buffer.byteLength(data) > LIMIT) throw new Error('outbound request is too large')
    this.process.stdin.write(data)
  }
  async request(method: string, request?: JsonRecord): Promise<any> {
    await this.readyPromise
    if (this.closed) throw new Error('Registry owner is closed')
    const id = randomUUID()
    const result = await new Promise<any>((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      try { this.write({ kind: 'command', id, method, ...(request ? { request } : {}) }) }
      catch (error) { this.pending.delete(id); reject(error) }
    })
    if (method === 'history') this.historySnapshot = result
    return result
  }
  cancel(): void {
    if (this.closed) return
    this.write({ kind: 'cancel' })
    this.active?.abort(new Error('owner canceled execution'))
  }
  private fail(error: Error): void {
    if (this.closed) return
    this.closed = true
    this.buffer = Buffer.alloc(0)
    this.active?.abort(error)
    for (const p of this.pending.values()) p.reject(error)
    this.pending.clear()
    this.process.kill('SIGTERM')
  }
  async close(): Promise<void> {
    if (this.closed) return
    this.process.stdin.end()
    await new Promise<void>(resolve => this.process.once('exit', () => resolve()))
  }
}

declare module '@deepseek-ai/cordis' { interface Context { rpnhBridge: RegistryBridge } }
export class RegistryBridge extends Service {
  readonly clients = new Map<string, OwnerClient>()
  readonly model: SelectedModel
  constructor(ctx: Context, readonly config: BridgeConfig) {
    super(ctx, 'rpnhBridge')
    this.model = selectedModel(config)
    ctx.effect(() => () => Promise.all([...this.clients.values()].map(c => c.close())).then(() => undefined))
  }
  client(id: string, create: boolean): OwnerClient {
    let c = this.clients.get(id)
    if (!c) { c = new OwnerClient(id, this.config, create); this.clients.set(id, c) }
    return c
  }
}
