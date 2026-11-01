import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { EventEmitter, getEventListeners } from 'node:events'
import { PassThrough } from 'node:stream'
import { invokeBridge, validateArguments } from './transport.js'

const config = { python: '/owner/venv/bin/python', controlRoot: '/tmp', enableControl: true }
const signal = () => new AbortController().signal
function program(code, observe = () => {}) {
  return (command, argv, options) => {
    observe(command, argv, options)
    return spawn(process.execPath, ['--input-type=module', '-e', code], options)
  }
}
const echo = `let s='';for await(const c of process.stdin)s+=c;const q=JSON.parse(s);console.log(JSON.stringify({protocol:'rpnh/dsh-operator/v1',operation:q.operation,result:q.arguments}));`

test('exact arguments reject unknown keys, paths, empty strings and unknown operations', () => {
  for (const [op, args] of [['list',{root:'/tmp'}], ['status',{task_id:''}], ['status',{task_id:' a'}], ['status',{task_id:'a',python:'sh'}], ['start',{}]]) {
    assert.throws(() => validateArguments(op,args))
  }
})
test('fixed argv and literal task id never use shell; exact JSON survives', async () => {
  const value = await invokeBridge(config,'status',{task_id:'task-$(touch bad)'},signal(),program(echo, (command,argv,options) => {
    assert.equal(command,'/owner/venv/bin/python'); assert.equal(options.shell,false)
    assert.deepEqual(argv,['-I','-m','cpn.dsh.operator','--control-root','/tmp','--allow-control'])
  }))
  assert.equal(value.result.task_id,'task-$(touch bad)')
})
test('pre-abort performs no spawn', async () => {
  const abort = new AbortController(); abort.abort()
  let calls=0
  await assert.rejects(invokeBridge(config,'resume',{task_id:'task-a'},abort.signal,() => { calls++ }),/NOT_DISPATCHED/)
  assert.equal(calls,0)
})
test('during mutation cancellation joins child close and returns unknown without retry', async () => {
  const abort = new AbortController(); let child, calls=0
  const runner = (cmd,argv,options) => {
    calls++; child=program(`process.stdin.resume();setInterval(()=>{},1000)`)(cmd,argv,options)
    child.once('spawn',()=>abort.abort()); return child
  }
  await assert.rejects(invokeBridge(config,'resume',{task_id:'task-a'},abort.signal,runner),/OUTCOME_UNKNOWN/)
  assert.equal(calls,1); assert.notEqual(child.signalCode,null)
})
test('successful-looking exit without reply is unknown for mutation, not completion', async () => {
  await assert.rejects(invokeBridge(config,'stop',{task_id:'task-a'},signal(),program(`process.stdin.resume()`)),/OUTCOME_UNKNOWN/)
})
test('wrong-operation response rejected; no retry', async () => {
  await assert.rejects(invokeBridge(config,'status',{task_id:'task-a'},signal(),program(`process.stdin.resume();console.log(JSON.stringify({protocol:'rpnh/dsh-operator/v1',operation:'other',result:{}}))`)),/no valid complete response/)
})
test('read cancellation does not claim rollback or success', async () => {
  const abort = new AbortController()
  const runner = (cmd,argv,options) => {
    const child=program(`process.stdin.resume();setInterval(()=>{},1000)`)(cmd,argv,options)
    child.once('spawn',()=>abort.abort());return child
  }
  await assert.rejects(invokeBridge(config,'net',{task_id:'task-a'},abort.signal,runner),/NO_RESULT/)
})
test('missing Python executable reports clear failure without retry', async () => {
  await assert.rejects(invokeBridge({...config,python:'/nonexistent/rpnh/python'},'list',{},signal()),/ENOENT/)
})

test('interpreter must be an absolute owner configuration', () => {
  assert.throws(() => invokeBridge({...config,python:'python3'},'list',{},signal()), /absolute python/)
})

test('cancellation reaps a bridge ignoring SIGTERM without retry or task-group signal', async () => {
  const abort = new AbortController()
  let child, calls = 0, deadline
  const sent = []
  const runner = (cmd, argv, options) => {
    calls++
    child = program(`process.on('SIGTERM',()=>{});process.stdin.resume();console.log('ready');setInterval(()=>{},1000)`)(cmd, argv, options)
    const kill = child.kill.bind(child)
    child.kill = signal => { sent.push(signal); return kill(signal) }
    child.stdout.once('data', () => abort.abort())
    return child
  }
  const request = invokeBridge(config, 'resume', { task_id: 'task-a' }, abort.signal, runner)
  try {
    await assert.rejects(Promise.race([request, new Promise((_, reject) => {
      deadline = setTimeout(() => reject(new Error('bridge did not settle after cancellation')), 4000)
    })]), /OUTCOME_UNKNOWN/)
    assert.equal(calls, 1)
    assert.deepEqual(sent, ['SIGTERM', 'SIGKILL'])
    assert.equal(child.signalCode, 'SIGKILL')
    assert.deepEqual(getEventListeners(abort.signal, 'abort'), [])
  } finally {
    clearTimeout(deadline)
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL')
    await request.catch(() => {})
  }
})

test('abort during spawn but before stdin dispatch is NOT_DISPATCHED', async () => {
  const abort = new AbortController()
  let calls = 0, writes = 0
  const runner = (cmd, argv, options) => {
    calls++
    const child = program(`process.stdin.resume();setInterval(()=>{},1000)`)(cmd, argv, options)
    const end = child.stdin.end.bind(child.stdin)
    child.stdin.end = (...args) => { writes++; return end(...args) }
    abort.abort()
    return child
  }
  await assert.rejects(invokeBridge(config, 'resume', { task_id: 'task-a' }, abort.signal, runner), /NOT_DISPATCHED/)
  assert.equal(calls, 1)
  assert.equal(writes, 0)
  assert.deepEqual(getEventListeners(abort.signal, 'abort'), [])
})


test('bridge exit clears escalation before a delayed stdio close', async () => {
  const abort = new AbortController(), sent = []
  const child = new EventEmitter()
  Object.assign(child, { stdout: new PassThrough(), stderr: new PassThrough(),
    stdin: new PassThrough(), exitCode: null, signalCode: null,
    kill: signal => { sent.push(signal); return true } })
  const request = invokeBridge(config, 'stop', { task_id: 'task-a' }, abort.signal, () => child)
  // Keep the close event pending after exit to model inherited pipe lifetime.
  abort.abort()
  child.exitCode = 0
  child.emit('exit', 0, null)
  await new Promise(resolve => setTimeout(resolve, 1100))
  child.emit('close', 0, null)
  await assert.rejects(request, /OUTCOME_UNKNOWN/)
  assert.deepEqual(sent, ['SIGTERM'])
  assert.deepEqual(getEventListeners(abort.signal, 'abort'), [])
})
