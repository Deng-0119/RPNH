import test from 'node:test'
import assert from 'node:assert/strict'
import { projectionFrames, projectionStyle } from './projection_frames.ts'

test('rc.5 and rc.1 group a committed event into one projection delta; alpha.2 is unchanged', () => {
  const values = { status: 'stopped_by_owner', workingDirectory: '/fixture' }
  for (const version of ['0.2.2-rc.1', '0.2.2-rc.5']) {
    assert.deepEqual(projectionFrames(version, 7, values), [{ type: 'projection', seq: 7, changes: [
      { key: 'status', val: 'stopped_by_owner' }, { key: 'workingDirectory', val: '/fixture' },
    ] }])
  }
  assert.deepEqual(projectionFrames('0.2.2-alpha.2', 7, values), [
    { type: 'projection', key: 'status', value: 'stopped_by_owner', seq: 7 },
    { type: 'projection', key: 'workingDirectory', value: '/fixture', seq: 7 },
  ])
})

test('unreviewed versions fail closed rather than inheriting an r.c. schema', () => {
  for (const version of ['0.2.2-rc.4', '0.2.2-rc.6', '0.2.2']) {
    assert.equal(projectionStyle(version), undefined)
    assert.throws(() => projectionFrames(version, 7, {}), /Unsupported/)
  }
})
