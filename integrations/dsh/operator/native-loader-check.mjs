import {boot,loadOverlayPatches} from '@deepseek-ai/dsh-app-boot'
import {writeFile,readFile} from 'node:fs/promises'
import {resolve} from 'node:path'
import assert from 'node:assert/strict'
// Caller configures an isolated DSH_HOME and installed candidate interpreter.
if (!process.env.DSH_HOME || !process.env.RPNH_TEST_PYTHON || !process.env.RPNH_TEST_FIXTURE) throw new Error('Set DSH_HOME, RPNH_TEST_PYTHON and RPNH_TEST_FIXTURE')
process.env.RPNH_PYTHON=process.env.RPNH_TEST_PYTHON
const fixture=JSON.parse(await readFile(process.env.RPNH_TEST_FIXTURE,'utf8'))
process.env.RPNH_CONTROL_ROOT=fixture.control_root
const config=resolve('minimal-native.yml')
await writeFile(config,`- id: system-prompt\n  name: '@deepseek-ai/dsh-system-prompt'\n- id: tools\n  name: '@deepseek-ai/dsh-tools'\n- id: native-tools\n  name: '@deepseek-ai/dsh-tool-executor-native'\n`)
const patches=loadOverlayPatches('rpnh-loader-check',resolve('node_modules/dsh-rpnh-operator/cordis.patch.yml'))
const ctx=await boot('rpnh-loader-check',config,patches)
try {
 const names=ctx.tools.schemas().map(x=>x.name);assert.equal(names.filter(x=>x.startsWith('rpnh_task_')).length,5)
 const rows={}
 for(const op of ['list','status','result','net','checkpoints']) {
  const args=op==='list'?{}:{task_id:fixture.task_id}
  const batch=await ctx.tools.execute({calls:[{type:'tool-call',id:`fixture-${op}`,name:`rpnh_task_${op}`,arguments:JSON.stringify(args)}],signal:new AbortController().signal})
  const r=batch.results[0].result
  assert.equal(r.isError,false,JSON.stringify(r));rows[op]=r.value
 }
 assert.equal(rows.list.result.length,1)
 assert.equal(rows.list.result[0].task_id,fixture.task_id)
 assert.deepEqual(rows.status.result,rows.list.result[0])
 assert.equal(rows.status.result.registry.execution_status,'terminal')
 assert.equal(rows.net.result.schema_version,'rpnh/net_view/v1')
 assert.equal(rows.net.result.source.run_dir,rows.status.result.run_dir)
 assert.ok(rows.net.result.source.net_ref)
 assert.deepEqual(rows.result.result,fixture.expected_result)
 assert.deepEqual(rows.checkpoints.result,fixture.expected_checkpoints)
 if (process.env.RPNH_TEST_OUTPUT) await writeFile(process.env.RPNH_TEST_OUTPUT,JSON.stringify(rows,null,2))
 console.log('PASS actual DSH app-boot/Loader + installed bundle patch; five tools; genuine static Registry exact result/checkpoint reads. No worker/provider/IPC lifecycle.')
} finally {await ctx.fiber.dispose()}
