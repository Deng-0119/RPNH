import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { decodeToolResult, encodeToolResult, projectMessage, projectHeader, toolResultText,
  LEGACY_DSH_REVISION as old, CANDIDATE_DSH_REVISION as latest } from './message-codec.ts'
import { projectHistory, projectTurnEvents, assertExecutionRevision } from './history-codec.ts'
const fixture = JSON.parse(readFileSync(new URL('../fixtures/message-codec.v1.json', import.meta.url), 'utf8'))
for (const entry of fixture.valid) {
  for (const [key, revision] of [['v3', old], ['v4', latest]]) {
    test(`tool roundtrip ${entry.name} ${key}`, () => {
      const value = decodeToolResult(entry[key], revision, { requireIdentity: true })
      assert.deepEqual(encodeToolResult(value, revision), entry[key])
      assert.deepEqual(encodeToolResult(value, key === 'v3' ? latest : old), entry[key === 'v3' ? 'v4' : 'v3'])
    })
  }
  test(`detached V3->V4 ${entry.name}`, () => assert.deepEqual(projectMessage(entry.v3, old, latest), entry.v4))
}
for (const entry of fixture.invalid) {
  test(`reject ${entry.name}`, () => assert.throws(() => decodeToolResult(entry.message, entry.revision, { requireIdentity: true })))
}
test('history absence stays absent; execution requires error boolean and text subset', () => {
  assert.equal('isError' in projectMessage(fixture.valid[2].v3, old, latest), false)
  assert.throws(() => decodeToolResult(fixture.valid[2].v3, old, { requireError: true }))
  assert.throws(() => toolResultText(fixture.valid[4].v4, latest))
})
test('reserved extensions cannot replace identity or source', () => {
  const canonical = decodeToolResult(fixture.valid[0].v3, old)
  for (const key of ['id', 'source', 'role', 'toolCallId', 'content', 'isError']) {
    assert.throws(() => encodeToolResult({ ...canonical, resultExtensions: { [key]: 'forged' } }, latest))
    assert.throws(() => encodeToolResult({ ...canonical, messageExtensions: { [key]: 'forged' } }, latest))
  }
})
test('upstream owner namespacing retains V3 extension fields without identity collision', () => {
  const message = structuredClone(fixture.valid[0].v3)
  message.note = 'message extension'; message.content[0].id = 'result extension'
  const output = projectMessage(message, old, latest)
  assert.equal(output.id, 'result-1'); assert.equal(output['plugin:message:note'], 'message extension')
  assert.equal(output['plugin:result:id'], 'result extension')
})
test('source migration preserves context and metadata, including prototype-like keys', () => {
  for (const [plugin, expected] of [['compact', 'compact-checkpoint'], ['agent-instructions', 'agent-instructions'], ['custom', 'plugin:custom'], ['__proto__', 'plugin:__proto__']]) {
    const message = { id: 'ctx', role: 'user', source: { kind: 'plugin', plugin, form: 'notice', summary: 'keep' }, content: [{ type: 'text', text: 'context' }] }
    const output = projectMessage(message, old, latest)
    assert.deepEqual(output.content, message.content); assert.deepEqual(output.source, { kind: expected, form: 'notice', summary: 'keep' })
    assert.deepEqual(projectMessage(message, old, old), message)
  }
  for (const source of [{ kind: '' }, {}, { kind: 'plugin', plugin: 4 }]) {
    assert.throws(() => projectMessage({ id: 'ctx', role: 'user', source, content: [] }, old, latest))
  }
})
const header = { version: 3, id: 's1', createdAt: 10, isSeeded: false }
function turn(status = 'completed') {
  const raw = '{ "values" : [2, 3] }'
  const tool = { id: 'call-1', name: 'managed_sum', arguments: raw }
  const message = { id: 'a1', role: 'assistant', source: { kind: 'model', provider: 'p', model: 'm' }, content: [
    { type: 'reasoning', text: 'legitimate reasoning' }, { type: 'text', text: 'Calling tool' }, { type: 'tool-call', ...tool }] }
  const calls: any[] = [{ ticket: { kind: 'model', upstream_revision: old }, observation: { message, chunks: [{ secret: 'CHUNK_NOT_A_MESSAGE' }] }, arguments: { private: 'ARGS_NOT_A_MESSAGE' } }]
  if (status === 'completed') {
    calls.push({ ticket: { kind: 'tool', upstream_revision: old }, arguments: { call: { ...tool, raw_arguments: raw, arguments: { values: [2, 3] } } },
      observation: { message: structuredClone(fixture.valid[0].v3), isError: false, raw_value: 'RAW_NOT_A_MESSAGE' } })
    calls.push({ ticket: { kind: 'model', upstream_revision: old }, observation: { message: { id: 'a2', role: 'assistant', source: message.source, content: [{ type: 'text', text: 'done' }] } } })
  }
  return { ordinal: 1, user_input: { session_id: 's1', upstream_revision: old, header: structuredClone(header),
    messages: [{ id: 'u1', role: 'user', source: { kind: 'user' }, content: [{ type: 'text', text: 'sum' }] }], data: 'DATA_NOT_A_MESSAGE', policy: 'POLICY_NOT_A_MESSAGE' },
    answer: { status, calls, private: 'ANSWER_NOT_A_MESSAGE' }, extra: 'RECORD_NOT_A_MESSAGE' }
}
function history(status = 'completed') { return { session_id: 's1', active: null, committed_history: [turn(status)] } }
function freeze(value: any): any { if (value && typeof value === 'object') { for (const v of Object.values(value)) freeze(v); Object.freeze(value) }; return value }
test('full candidate history preserves legitimate messages and only existing slots', () => {
  const input = freeze(history()); const before = JSON.stringify(input)
  const output = projectHistory(input, latest)
  assert.equal(output.header.version, 4); assert.equal(output.events[0].time, 10)
  assert.equal(output.events.find(e => e.type === 'tool/call')!.data.arguments, '{ "values" : [2, 3] }')
  const result = output.events.find(e => e.type === 'tool/result')!.data.message
  assert.deepEqual(result, fixture.valid[0].v4)
  const text = JSON.stringify(output)
  assert.ok(text.includes('legitimate reasoning')); assert.ok(text.includes('结果 5')); assert.ok(!text.includes('NOT_A_MESSAGE'))
  assert.equal(JSON.stringify(input), before)
  output.header.id = 'changed'; assert.equal(input.committed_history[0].user_input.header.id, 's1')
})
test('legacy projection stays V3 with identical selected tool/user/model content', () => {
  const output = projectHistory(history(), old)
  assert.deepEqual(output.header, header)
  assert.deepEqual(output.events.find(e => e.type === 'tool/result')!.data.message, fixture.valid[0].v3)
})
test('denied pending tool call is committed blocked history without synthetic result', () => {
  const output = projectHistory(history('denied'), latest)
  assert.equal(output.events.some(e => e.type === 'tool/result'), false)
  assert.equal(output.events.at(-1)!.data.reason.kind, 'blocked')
  assert.ok(output.events.some(e => e.type === 'assistant/message'))
})
test('known error observation cannot become a success by omitting message error flag', () => {
  const input = history(); input.committed_history[0].answer.calls[1].observation.isError = true
  delete input.committed_history[0].answer.calls[1].observation.message.content[0].isError
  assert.throws(() => projectHistory(input, latest), /error status/)
})
for (const [name, mutate] of [
  ['mixed revision', (h: any) => { h.committed_history[0].answer.calls[0].ticket.upstream_revision = latest }],
  ['wrong call', (h: any) => { h.committed_history[0].answer.calls[1].arguments.call.id = 'wrong' }],
  ['changed raw arguments', (h: any) => { h.committed_history[0].answer.calls[1].arguments.call.raw_arguments = '{"values":[2,3]}' }],
  ['missing result in completed', (h: any) => { h.committed_history[0].answer.calls.splice(1) }],
  ['wrong header', (h: any) => { h.committed_history[0].user_input.header.version = 4 }],
  ['wrong session', (h: any) => { h.session_id = 'other' }],
  ['unknown role', (h: any) => { h.committed_history[0].answer.calls[0].observation.message.role = 'developer' }],
] as const) test(`history rejects ${name}`, () => { const input = history(); mutate(input); assert.throws(() => projectHistory(input, latest)) })
test('header and history support is directional; source mismatch never guessed', () => {
  assert.deepEqual(projectHeader(header, old, old), header)
  assert.equal(projectHeader(header, old, latest).version, 4)
  assert.throws(() => projectHeader({ ...header, version: 4 }, latest, old))
  assert.throws(() => projectMessage(fixture.valid[0].v4, latest, old))
  assert.throws(() => projectHeader(header, 'latest', latest))
})
test('read projection does not authorize new-revision execution or reopen effects', () => {
  const input: any = history(); input.active = { user_input: structuredClone(input.committed_history[0].user_input), state: 'interrupted' }
  const before = JSON.stringify(input)
  assert.equal(projectHistory(input, latest).events.length, projectHistory(history(), latest).events.length)
  assertExecutionRevision(input, old); assert.throws(() => assertExecutionRevision(input, latest))
  assert.equal(JSON.stringify(input), before)
  assert.throws(() => assertExecutionRevision(history(), latest))
})

test('V3 and V4 configured DTO projections are identical, including errors and raw arguments', async () => {
  const { providerMessages } = await import('./provider-message-codec.ts')
  const names = new Set(['sum'])
  for (const entry of fixture.valid.filter((e: any) => e.name !== 'absent' && e.name !== 'rich_content')) {
    const before = [{ role: 'user', content: [{ type: 'text', text: 'sum' }] },
      { role: 'assistant', content: [{ type: 'reasoning', text: 'Think. ' }, { type: 'text', text: 'Call.' },
        { type: 'tool-call', id: 'call-1', name: 'sum', arguments: '{ "x": 2 }' }] }]
    const v3 = providerMessages([...before, entry.v3], names, old)
    const v4 = providerMessages([...before, entry.v4], names, latest)
    assert.deepEqual(v3, v4)
    assert.equal(v4[1].tool_calls[0].function.arguments, '{ "x": 2 }')
    assert.equal(v4[1].content, 'Think. Call.')
    assert.throws(() => providerMessages([...before, entry.v3], names, latest))
    assert.throws(() => providerMessages([...before, entry.v4], names, old))
    assert.throws(() => providerMessages(before, names, old), /no correlated result/)
    assert.throws(() => providerMessages([...before, entry.v4, entry.v4], names, latest))
  }
  assert.throws(() => providerMessages([{ role: 'developer', content: [{ type: 'text', text: 'x' }] }], names, latest))
})


test('header field constraints match both pinned upstream validators', () => {
  for (const invalid of [{ seedLength: 0 }, { cwd: 'relative' }, { cwd: 0 }, { cwd: null },
    { parentSession: false }, { parentSession: null }, { origin: '' }, { origin: false },
    { delegationDepth: -1 }, { delegationDepth: 1.2 }, { delegationDepth: '1' },
    { agentPreset: 0 }, { agentPreset: null }]) {
    assert.throws(() => projectHeader({ ...header, ...invalid }, old, latest))
  }
  const valid = { ...header, cwd: '/fixture/workspace', delegationDepth: 0, parentSession: '', agentPreset: '' }
  assert.deepEqual(projectHeader(valid, old, old), valid)
  assert.equal(projectHeader(valid, old, latest).version, 4)
})
