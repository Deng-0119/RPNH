/** UI-only bridge to RPNH's shared updater; never runs package-manager commands. */
import { spawn } from 'node:child_process'
import { isAbsolute } from 'node:path'

export const UPDATE_PATH = '/api/rpnh-operator/update'
export const UPDATE_CHOICE_PATH = UPDATE_PATH + '/choose'
const choices = new Set(['update', 'later', 'skip'])
const releaseTag = value => typeof value === 'string' && /^v[0-9][0-9A-Za-z.+-]*$/.test(value)
const unavailable = () => ({ schema_version: 'rpnh/update_status/v1', entry: 'dsh-operator', offer: null, reason: 'unavailable' })

/** Execute only the installed shared update CLI, with bounded output and lifetime. */
export function runUpdateCommand(config, args, signal) {
  if (!isAbsolute(config.python)) return Promise.reject(new Error('An absolute installed Python is required'))
  if (!(args.length === 2 && args[0] === 'status' && args[1] === '--automatic')
      && !(args.length === 5 && args[0] === 'choose' && args[1] === '--release' && releaseTag(args[2])
        && args[3] === '--choice' && choices.has(args[4]))) return Promise.reject(new Error('Invalid update operation'))
  return new Promise((resolve, reject) => {
    const child = spawn(config.python, ['-I', '-m', 'cpn.rpnh.update_cli', ...args, '--frontend', 'dsh-operator', '--json'],
      { stdio: ['ignore', 'pipe', 'pipe'], shell: false, signal })
    const chunks = []
    let size = 0, failure, killTimer
    const stop = () => { child.kill(); killTimer ??= setTimeout(() => child.kill('SIGKILL'), 1000) }
    const timeout = setTimeout(() => { failure = new Error('Update check timed out'); stop() }, 10000)
    child.stdout.on('data', chunk => {
      size += chunk.length
      if (size > 65536) { failure = new Error('Update response exceeded its limit'); stop() }
      else chunks.push(chunk)
    })
    child.stderr.on('data', () => {})
    child.on('error', error => { failure = error })
    child.once('close', code => {
      clearTimeout(timeout); clearTimeout(killTimer)
      if (failure || code !== 0) { reject(failure ?? new Error('Shared update CLI unavailable')); return }
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))) } catch { reject(new Error('Invalid shared update response')) }
    })
  })
}

function status(value) {
  if (!value || value.schema_version !== 'rpnh/update_status/v1' || value.entry !== 'dsh-operator') throw new Error('Invalid update status')
  if (value.offer !== null && (!value.offer || typeof value.offer.tag !== 'string'
      || !releaseTag(value.offer.tag) || value.offer.mode !== 'manual'
      || !['rpnh_version', 'upstream_version', 'upstream_revision', 'channel'].every(key =>
        typeof value.offer[key] === 'string' && value.offer[key].length > 0 && value.offer[key].length <= 128)
      || !Array.isArray(value.offer.choices) || value.offer.choices.length !== 3
      || !['update', 'later', 'skip'].every(choice => value.offer.choices.includes(choice)))) throw new Error('Invalid update offer')
  return value
}

/** Attach one startup check and authenticated owner-only actions to native Connection. */
export function installUpdateNotice(ctx, config, invoke = runUpdateCommand) {
  ctx.inject(['connection'], scope => {
    const abort = new AbortController()
    scope.effect(() => () => abort.abort(), 'rpnh-operator: update helper lifecycle')
    let current = unavailable(), pending = false
    const initial = invoke(config, ['status', '--automatic'], abort.signal).then(value => { current = status(value) }).catch(() => {})
    // Alpha's HTTP bridge cannot attach a streaming body to GET; keep the
    // read route buffered and only the bounded POST route streaming.
    scope.effect(() => scope.connection.fetch.register({
      path: UPDATE_PATH, methods: ['GET'], requestBody: 'buffered',
      async fetch() { await initial; return Response.json(current, { headers: { 'cache-control': 'no-store' } }) },
    }), 'rpnh-operator: owner-only update status')
    scope.effect(() => scope.connection.fetch.register({
      path: UPDATE_CHOICE_PATH, methods: ['POST'], requestBody: 'streaming',
      async fetch(request) {
        await initial
        const headers = { 'cache-control': 'no-store' }
        if (request.headers.get('content-type')?.split(';')[0] !== 'application/json') return new Response('JSON required', { status: 415, headers })
        if (Number(request.headers.get('content-length') ?? 0) > 2048) return new Response('Request too large', { status: 413, headers })
        const parts = []; let bytes = 0
        const deadline = Date.now() + 5000
        const reader = request.body?.getReader()
        if (reader) {
          while (true) {
            let chunk, timer
            try {
              chunk = await Promise.race([reader.read(), new Promise((_, reject) => {
                timer = setTimeout(() => reject(new Error('Body deadline exceeded')), Math.max(0, deadline - Date.now()))
              })])
            } catch {
              void reader.cancel().catch(() => {})
              return new Response('Request body timed out or was aborted', { status: 408, headers })
            } finally { clearTimeout(timer) }
            const { value, done } = chunk
            if (done) break
            bytes += value.byteLength
            if (bytes > 2048) { void reader.cancel().catch(() => {}); return new Response('Request too large', { status: 413, headers }) }
            parts.push(Buffer.from(value))
          }
        }
        const body = Buffer.concat(parts).toString('utf8')
        let input
        try { input = JSON.parse(body) } catch { return new Response('Invalid JSON', { status: 400, headers }) }
        if (!input || typeof input !== 'object' || Array.isArray(input)
            || Object.keys(input).sort().join(',') !== 'choice,release' || !choices.has(input.choice)
            || typeof input.release !== 'string') return new Response('Invalid choice', { status: 400, headers })
        if (!current.offer || input.release !== current.offer.tag) return new Response('Offer changed', { status: 409, headers })
        if (pending) return new Response('Choice already pending', { status: 409, headers })
        pending = true
        try {
          // The shared CLI revalidates the offer and owns persisted preferences.
          const result = await invoke(config, ['choose', '--release', input.release, '--choice', input.choice], abort.signal)
          if (!result || result.schema_version !== 'rpnh/update_choice/v1' || result.entry !== 'dsh-operator'
              || result.release !== input.release || result.choice !== input.choice
              || result.result !== ({ update: 'manual', later: 'deferred', skip: 'skipped' })[input.choice]
              || input.choice === 'update' && typeof result.instructions !== 'string') throw new Error('Invalid choice result')
          current = { ...current, offer: null, reason: input.choice }
          return Response.json(result, { headers })
        } catch { return new Response('Update choice unavailable; no installation was performed here.', { status: 503, headers }) }
        finally { pending = false }
      },
    }), 'rpnh-operator: owner-only update notice')
  })
}

