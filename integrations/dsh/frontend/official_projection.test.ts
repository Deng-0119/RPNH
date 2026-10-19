import test from 'node:test'
import assert from 'node:assert/strict'
import { Session, SessionId } from '@deepseek-ai/dsh-session'
import { officialModelCatalog, officialOpening, officialProjections, officialRecords, officialSummary } from './official_projection.ts'
import type { Initialization, Profile, SessionRow } from './rpc.ts'
const profile: Profile = {selection:'route/A',provider:'example',provider_name:'Example',model:'A',reasoning_effort:'high',supported_reasoning_efforts:['low','high'],default_reasoning_effort:'low',ready:true}
const row: SessionRow = {id:'bound',created:1000,updated:2000,model:profile,error:null,turns:[{ordinal:1,key:'one',text:'Original Registry input',kind:'main',model:profile,model_evidence:'frontend-request/v3',state:'committed',created:1100,updated:1900,turn_ref:null,attempt:null,answer:{reply:'Original Registry answer',protocol_valid:true},children:[]}]}
const initial: Initialization = {configuration:{default_selection:profile.selection,default_reasoning_effort:'low',profiles:[profile,{...profile,selection:'route/B',model:'B',reasoning_effort:null,supported_reasoning_efforts:[],default_reasoning_effort:null},{...profile,selection:'route/C',model:'C',ready:false}]},session:row}
test('official catalog preserves exact RPNH selection and per-model effort capability',()=>{
 const catalog=officialModelCatalog(initial)
 assert.deepEqual(catalog.default,{provider:'rpnh',model:'route/A',reasoningEffort:'high'})
 assert.deepEqual(catalog.groups[0]!.models.map(m=>m.id),['route/A','route/B'])
 assert.equal(catalog.groups[0]!.models[1]!.reasoning,undefined)
 assert.equal(catalog.failures[0]!.id,'route/C')
})
test('official history keeps Registry times and forms valid native Session events',()=>{
 const opening=officialOpening(row)
 assert.equal(opening.type,'snapshot')
 if(opening.type!=='snapshot')throw new Error('snapshot required')
 assert.deepEqual(opening.records.map(r=>r.event.time),[1100,1100,1900,1900])
 assert.equal(opening.header.createdAt,1000)
 assert.equal(opening.header.version,4)
 const header={...opening.header,version:4 as const}
 const session=Session.fromRestore(SessionId(row.id),opening.records.map(r=>r.event) as any,header,0 as any,'detached')
 assert.deepEqual(session.snapshotEvents().slice(0,4),opening.records.map(r=>r.event))
 assert.equal(session.snapshotEvents().at(-1)?.type,"session/end-seed")
 assert.deepEqual(officialRecords(row),opening.records)
})
test('paused Registry checkpoint remains open and does not project native success',()=>{
 const paused: SessionRow={...row,turns:[{...row.turns[0]!,state:'stopped_by_owner',answer:null}]}
 const records=officialRecords(paused)
 assert.deepEqual(records.map(r=>r.event.type),['turn/start','user/message'])
 assert.equal(officialSummary(paused).running,false)
 assert.equal(officialSummary({...row,turns:[]}).projections?.asOfSeq,-1)
})
test('unknown historical model is never attributed to the current profile',()=>{
 const current={...profile,selection:'route/B',provider:'other',model:'B',reasoning_effort:null}
 const unknown: SessionRow={...row,model:current,turns:[{...row.turns[0]!,model:current,model_evidence:'unavailable'}]}
 const values=officialProjections(unknown)
 assert.equal((values.modelSelection as any).lastUsed,null)
 assert.equal((values.modelSelection as any).next.model,'route/B')
 const answer=officialRecords(unknown).find(record=>record.event.type==='assistant/message')!
 assert.deepEqual((answer.event.data as any).message.source,{kind:'model',provider:'unknown',model:'unknown'})
 assert.equal((answer.event.data as any).message.content[0].text,'Original Registry answer')
})
test('RPNH rollback is a cancelled turn rather than an orphaned DSH crash',()=>{
 const rolledBack: SessionRow={...row,turns:[{...row.turns[0]!,state:'interrupted',answer:null}]}
 const records=officialRecords(rolledBack)
 assert.equal(records.some(record=>record.event.type==='assistant/message'),false)
 const end=records.at(-1)!.event
 assert.equal(end.type,'turn/end')
 if(end.type!=='turn/end')throw new Error('closed turn required')
 assert.deepEqual(end.data.reason,{kind:'aborted',reason:{kind:'user'}})
})
