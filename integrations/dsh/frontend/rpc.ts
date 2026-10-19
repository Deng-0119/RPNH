import { randomUUID } from 'node:crypto'
import { createReadStream, createWriteStream } from 'node:fs'
import type { Readable, Writable } from 'node:stream'

export const PROTOCOL = 'rpnh/dsh_frontend/v1'
export const MAX_FRAME_BYTES = 68 * 1024 * 1024
export interface Profile {
  selection: string; provider: string; provider_name: string; model: string
  reasoning_effort: string | null; supported_reasoning_efforts: string[]
  default_reasoning_effort: string | null; ready: boolean
}
export interface Turn {
  ordinal: number; key: string; text: string; kind: string; model: Profile
  state: string; created: number; updated: number; turn_ref: unknown; attempt: unknown
  model_evidence?: string
  answer: { reply: string; protocol_valid: boolean; task?: unknown } | null
  children: Array<{ task_id?: string; kind?: string }>
}
export interface SessionRow {
  id: string; created: number; updated: number; model: Profile; turns: Turn[]; error: unknown
}
export interface Initialization {
  configuration: { default_selection: string; default_reasoning_effort: string | null; profiles: Profile[] }
  session: SessionRow
}
export interface Selection { selectionId?: string; reasoningEffort?: string }
export interface RpcPort {
  request<T>(method: string, params?: Record<string, unknown>): Promise<T>
  readonly closed: Promise<Error>
  close(): void
}
export class RpcError extends Error {
  constructor(readonly code: string, message: string, readonly status = 400) { super(message) }
}

/** Private inherited pipes only: stdin/stdout remain the user's terminal. */
export class PipeRpc implements RpcPort {
  private readonly pending = new Map<string, { resolve(value: unknown): void; reject(error: Error): void }>()
  private parts: Buffer[] = []
  private size = 0
  private failure: Error | undefined
  private readonly closure = Promise.withResolvers<Error>()
  readonly closed = this.closure.promise
  constructor(private readonly input: Readable, private readonly output: Writable) {
    input.on('data', (chunk: Buffer) => this.receive(Buffer.from(chunk)))
    input.on('end', () => this.fail(new RpcError('pipe_closed', 'RPNH owner pipe closed.', 503)))
    input.on('close', () => this.fail(new RpcError('pipe_closed', 'RPNH owner pipe closed.', 503)))
    input.on('error', error => this.fail(error))
    output.on('error', error => this.fail(error))
    output.on('close', () => this.fail(new RpcError('pipe_closed', 'RPNH owner pipe closed.', 503)))
  }
  static fromFds(readFd: number, writeFd: number): PipeRpc {
    if (![readFd, writeFd].every(fd => Number.isSafeInteger(fd) && fd >= 3) || readFd === writeFd) {
      throw new Error('read-fd and write-fd must be distinct inherited descriptors >= 3')
    }
    return new PipeRpc(createReadStream('', { fd: readFd, autoClose: true }),
      createWriteStream('', { fd: writeFd, autoClose: true }))
  }
  request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (this.failure) return Promise.reject(this.failure)
    const id = randomUUID()
    const frame = Buffer.from(JSON.stringify({ protocol: PROTOCOL, id, method, params }))
    if (frame.length > MAX_FRAME_BYTES) return Promise.reject(new RpcError('frame_too_large', 'RPC frame exceeds 68 MiB.', 413))
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: value => resolve(value as T), reject })
      this.output.write(Buffer.concat([frame, Buffer.from('\n')]), error => { if (error) this.fail(error) })
    })
  }
  private receive(chunk: Buffer): void {
    if (this.failure) return
    let start = 0
    while (start < chunk.length) {
      const newline = chunk.indexOf(10, start)
      const end = newline < 0 ? chunk.length : newline
      const part = chunk.subarray(start, end)
      this.size += part.length
      if (this.size > MAX_FRAME_BYTES) { this.fail(new RpcError('frame_too_large', 'RPC reply exceeds 68 MiB.', 413)); return }
      this.parts.push(part)
      if (newline < 0) return
      try {
        const reply = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(this.parts, this.size)))
        this.parts = []; this.size = 0
        if (reply.protocol !== PROTOCOL || typeof reply.id !== 'string' || !this.pending.has(reply.id)
          || Object.hasOwn(reply, 'result') === Object.hasOwn(reply, 'error')) throw new Error('Invalid RPC reply envelope')
        const waiter = this.pending.get(reply.id)!
        if (Object.hasOwn(reply, 'error')) {
          if (typeof reply.error?.code !== 'string' || typeof reply.error?.message !== 'string') throw new Error('Invalid RPC error')
          this.pending.delete(reply.id)
          waiter.reject(new RpcError(reply.error.code, reply.error.message, reply.error.status))
        } else { this.pending.delete(reply.id); waiter.resolve(reply.result) }
      } catch (error) { this.fail(error instanceof Error ? error : new Error(String(error))); return }
      start = newline + 1
    }
  }
  private fail(error: Error): void {
    if (this.failure) return
    this.failure = error
    for (const waiter of this.pending.values()) waiter.reject(error)
    this.pending.clear(); this.parts = []; this.size = 0
    this.closure.resolve(error)
    this.input.destroy(); this.output.destroy()
  }
  close(): void { this.fail(new RpcError('pipe_closed', 'RPNH frontend closed.', 503)) }
}
