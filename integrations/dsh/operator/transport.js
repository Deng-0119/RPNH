import { spawn } from 'node:child_process'
import { isAbsolute } from 'node:path'

export const READ_OPERATIONS = Object.freeze(['list', 'status', 'result', 'net', 'checkpoints'])
export const CONTROL_OPERATIONS = Object.freeze(['stop', 'resume', 'reopen'])
export const PROTOCOL = 'rpnh/dsh-operator/v1'

export function validateArguments(operation, args) {
  if (![...READ_OPERATIONS, ...CONTROL_OPERATIONS].includes(operation)) throw new Error('unknown RPNH operation')
  const fields = operation === 'list' ? [] : operation === 'reopen'
    ? ['task_id', 'checkpoint_version_id', 'reason'] : ['task_id']
  if (!args || typeof args !== 'object' || Array.isArray(args)
      || Object.keys(args).length !== fields.length || Object.keys(args).some(key => !fields.includes(key))) {
    throw new Error('RPNH arguments must contain exactly the declared fields')
  }
  for (const key of fields) {
    if (typeof args[key] !== 'string' || !args[key].trim() || args[key] !== args[key].trim()) {
      throw new Error(`${key} requires nonempty trimmed text`)
    }
  }
}

/** Own only this one transport process. Never signal a task's process group. */
export function invokeBridge(config, operation, args, signal, spawnProcess = spawn) {
  validateArguments(operation, args)
  if (!signal || typeof signal.addEventListener !== 'function') throw new Error('caller AbortSignal is required')
  if (signal.aborted) return Promise.reject(new Error('RPNH request cancelled before dispatch; NOT_DISPATCHED'))
  if (typeof config.python !== 'string' || !isAbsolute(config.python)
      || typeof config.controlRoot !== 'string' || !isAbsolute(config.controlRoot)) {
    throw new Error('configure absolute python and controlRoot')
  }
  const control = CONTROL_OPERATIONS.includes(operation)
  if (control && config.enableControl !== true) throw new Error('RPNH control tools are disabled')
  return new Promise((resolve, reject) => {
    let child, output = '', errorOutput = '', dispatched = false, aborted = false, settled = false
    const failure = (message) => new Error(`${message}; ${control && dispatched ? 'OUTCOME_UNKNOWN: inspect original task; do not retry automatically' : 'NO_RESULT'}`)
    const finish = (error, result) => {
      if (settled) return
      settled = true
      signal.removeEventListener('abort', abort)
      if (error) reject(error)
      else resolve(result)
    }
    const abort = () => {
      aborted = true
      // The bridge is disposable; TaskControl workers use their own sessions.
      // SIGTERM only this child, not a negative process group or owner channel.
      child?.kill('SIGTERM')
    }
    const argv = ['-I', '-m', 'cpn.dsh.operator', '--control-root', config.controlRoot]
    if (config.enableControl === true) argv.push('--allow-control')
    try {
      child = spawnProcess(config.python, argv, { shell: false, stdio: ['pipe', 'pipe', 'pipe'] })
    } catch (error) {
      finish(failure(`RPNH bridge could not start: ${error.code || error.name}`)); return
    }
    signal.addEventListener('abort', abort, { once: true })
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', data => { output += data })
    child.stderr.on('data', data => { errorOutput += data })
    child.on('error', error => finish(failure(`RPNH bridge failed to start: ${error.code || error.name}`)))
    child.stdin.on('error', () => { /* close determines outcome; never replay */ })
    child.on('close', (code) => {
      if (aborted) { finish(failure('RPNH wait cancelled; this is not task rollback')); return }
      if (code !== 0) {
        let detail = 'check the configured Python installation and original task state'
        try {
          const error = JSON.parse(errorOutput)
          if (error.protocol === PROTOCOL && typeof error.message === 'string') detail = error.message
        } catch { /* Do not copy arbitrary child stderr into model context. */ }
        finish(failure(`RPNH bridge exited ${code}: ${detail}`)); return
      }
      try {
        const result = JSON.parse(output)
        if (result?.protocol !== PROTOCOL || result.operation !== operation
            || !Object.hasOwn(result, 'result')) throw new Error('invalid response envelope')
        finish(null, result)
      } catch { finish(failure('RPNH bridge returned no valid complete response')) }
    })
    if (signal.aborted) { abort(); return }
    dispatched = true
    child.stdin.end(JSON.stringify({ operation, arguments: args }))
  })
}

