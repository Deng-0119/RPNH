import test, {afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {checkpointApp, response, deferred} from './checkpoint-test-harness.mjs';
import {setLanguage} from '../../../cpn/frontend/static/i18n.mjs';
import {EN} from '../../../cpn/frontend/static/messages.mjs';
import {selectorForFrame} from '../../../cpn/frontend/static/checkpoint-view.mjs';
import {nodeKey, edgeKey} from '../../../cpn/frontend/static/model.mjs';
import {contributionPath, contributionScope, normalizeContributions, contributionOverlay,
    contributionNodeStyle, contributionColor, CONTRIBUTION_UNKNOWN, CONTRIBUTION_OVERLAP} from '../../../cpn/frontend/static/user-contributions.mjs';
const fixture=JSON.parse(readFileSync(new URL('./comparison-fixture.json',import.meta.url),'utf8'));
const ref=(type,id)=>({entity_type:type,logical_id:'resource:'+id.repeat(32),version_id:'resource_version:'+id.repeat(32)});
const actorA='actor:'+ 'a'.repeat(32),actorB='actor:'+ 'b'.repeat(32);
const sessionA=ref('user_collaboration_session/v1','a'),sessionB=ref('user_collaboration_session/v1','b');
const current=()=>{const frame=structuredClone(fixture.right.frame);frame.position.mode='live';
    frame.net.nodes.push({id:'t',label:'Step',kind:'transition',category:'execution',hidden_by_default:false,inputs:['p','resource'],outputs:['out']},
        {id:'out',label:'Output',kind:'place',category:'place',hidden_by_default:false,active_token_count:0,tokens:[]},
        {id:'resource',label:'Resource',kind:'place',category:'resource',hidden_by_default:true,active_token_count:1,tokens:[]});
    frame.net.edges=[['in','p','t','consume'],['out','t','out','produce'],['resource','resource','t','read']].map(([id,source,target,mode])=>
        ({id,source,target,kind:'arc',mode,weight:1,outcome:null,hidden_by_default:id==='resource',resource:id==='resource'}));
    return frame;};
function payload(frame=current()) {
    const a={actor:actorA,session_ref:sessionA,proposal_ref:ref('user_collaboration_proposal/v1','c'),
        base_revision_ref:ref('collaboration_net_revision/v1','d'),candidate_revision_ref:ref('collaboration_net_revision/v1','e'),
        element_id:'step',locator:'transitions.step',change:'modified',status:'adopted',fields:['operation']};
    const b={...structuredClone(a),actor:actorB,session_ref:sessionB,proposal_ref:ref('user_collaboration_proposal/v1','f'),status:'proposed'};
    return {schema_version:'rpnh/user_contributions/v1',selector:selectorForFrame(frame),source:{task_id:frame.source.task_id,source_id:'test-source-identity',task_ref:{entity_type:'task/v1',logical_id:frame.source.task_id,version_id:'task_version:'+ '4'.repeat(32)}},
        coverage:{status:'partial',reason:'No authorship assertion'},actors:[{actor:actorA,session_ref:sessionA},{actor:actorB,session_ref:sessionB}],
        nodes:frame.net.nodes.slice(0,3).map((node,i)=>({node_id:node.id,state:['changed','overlap','inherited'][i],changes:i===1?[a,b]:[a]})),
        actions:[{role:'adopted',actor:null,session_ref:null,evidence_ref:ref('user_collaboration_receipt/v1','4'),node_ids:[frame.net.nodes[0].id],status:'recorded'},
            {role:'executed',actor:null,session_ref:null,evidence_ref:ref('transition_firing/v1','5'),node_ids:[frame.net.nodes[0].id],status:'settled'}],
        selected_revision_ref:ref('collaboration_net_revision/v1','e'),adoption_receipt_ref:ref('user_collaboration_receipt/v1','4')};
}
afterEach(()=>setLanguage('en'));
const walk=node=>[node,...node.children.flatMap(walk)];
const panel=h=>h.document.getElementById('user-contributions-panel');
const control=(h,name)=>walk(panel(h)).find(node=>node.dataset.contributionAction===name);
function event(h,type,persisted=false){const value=new Event(type);Object.defineProperty(value,'persisted',{value:persisted});h.window.dispatchEvent(value);}
async function boot({late=null, locale='en', input=null}={}) {
    setLanguage(locale);const frame=input??current(),requests=[];let contribution=()=>late?.promise??response(payload(frame));
    const h=checkpointApp(async(path,options)=>{
        requests.push({path,...options});
        if(path.startsWith('/api/v2/user-contributions?'))return contribution();
        if(path==='/api/v1/dashboard')return response(frame);
        if(path==='/api/v1/history')return response({schema_version:'rpnh/dashboard_history/v1',source:frame.source,coverage:'current_net_canonical_checkpoints',next_before:null,end_reason:'initial_checkpoint',
            items:[{cursor:frame.position.cursor,checkpoint_ref:frame.net.marking.checkpoint_ref}]});
        throw Error('Unexpected '+path);
    },{startup:true});
    await h.ready;
    h.setLayout(async graph=>({width:1200,height:400,
        nodes:new Map(graph.nodes.map((n,i)=>[n.id,{x:40+i*250,y:40,width:n.kind==='place'?56:224,height:n.kind==='place'?56:126,ports:[]} ])),
        edges:new Map(graph.edges.map(e=>[e.id,{sources:['source'],targets:['target'],sections:[{startPoint:{x:10,y:10},endPoint:{x:200,y:10}}]}]))}));
    // The real renderer focuses via the library geometry API; supply only that
    // method in the existing no-browser JointJS double.
    const viewer=h.api.viewer();
    const get=viewer.graph.getCell.bind(viewer.graph);
    viewer.graph.getCell=id=>{const cell=get(id);if(cell)cell.getBBox=()=>({center:()=>({x:100,y:100})});return cell;};
    return {...h,requests,frame,setResponse:fn=>{contribution=fn;}};
}

test('exact selector path preserves both complete refs and canonical decimal cut',()=>{
    const selected=selectorForFrame(current()), path=contributionPath(selected),url=new URL(path,'http://localhost');
    assert.equal(url.pathname,'/api/v2/user-contributions');assert.equal(url.searchParams.get('cut'),String(selected.cut));
    for(const key of ['net_ref','checkpoint_ref'])assert.deepEqual(JSON.parse(url.searchParams.get(key)),selected[key]);
    for(const cut of ['15',1.5,0,-1,NaN])assert.throws(()=>contributionPath({...selected,cut}));
    assert.throws(()=>contributionPath({...selected,extra:'not allowed'}));
});

test('normalizer accepts only recorded proposer/session pairs and keeps adoption/execution separate',()=>{
    const frame=current(),raw=payload(frame),value=normalizeContributions(raw,selectorForFrame(frame),frame);
    assert.deepEqual(value,raw);assert.equal(value.nodes[0].changes[0].actor,actorA);
    assert.equal(value.actions[1].actor,null);assert.equal(value.actions[1].role,'executed');
    raw.nodes[0].changes[0].fields.push('tampered');assert.deepEqual(value.nodes[0].changes[0].fields,['operation']);
    const changed=structuredClone(frame);changed.source.run_dir+='-other';assert.notEqual(contributionScope(frame),contributionScope(changed));
    assert.equal(contributionScope({}),null);
});

test('normalizer rejects substitution, malformed references, forged actors and non-PN node mappings',()=>{
    const frame=current();
    const mutations=[
        p=>p.selector.cut++,p=>p.selector.net_ref.version_id='net_instance_version:'+ '9'.repeat(32),p=>p.source.run_dir='unexpected-path',
        p=>p.source.task_id='other',p=>p.schema_version='unknown',p=>p.coverage.status='verified',
        p=>p.actors.push(p.actors[0]),p=>p.actors[0].session_ref={entity_type:'user_collaboration_session/v1',logical_id:'not-exact',version_id:'head'},
        p=>p.nodes[0].changes[0].actor='invented user',p=>p.nodes[0].changes[0].session_ref=sessionB,
        p=>p.nodes[0].changes[0].proposal_ref=p.nodes[0].changes[0].base_revision_ref,
        p=>p.nodes[0].changes[0].candidate_revision_ref={...p.nodes[0].changes[0].candidate_revision_ref,extra:'no'},
        p=>p.nodes[0].changes[0].status='authored',p=>p.nodes[0].state='verified_author',p=>p.nodes.push(p.nodes[0]),
        p=>p.nodes[0].node_id='unknown-node',p=>p.actions[0].node_ids=['unknown-node'],p=>p.actions[0].role='authored',
        p=>p.actions[0].actor=actorA,p=>p.adoption_receipt_ref=p.selected_revision_ref,
        p=>p.selected_revision_ref=undefined,p=>p.nodes[0].changes[0].fields=[{}],
    ];
    for(const mutate of mutations){const p=structuredClone(payload(frame));mutate(p);assert.throws(()=>normalizeContributions(p,selectorForFrame(frame),frame),String(mutate));}
});

test('overlay leaves missing nodes unknown, colors overlap distinctly and dims nonmatching actors',()=>{
    const value=payload(),overlay=contributionOverlay(value,actorB),[first,second,third]=value.nodes;
    assert.equal(contributionNodeStyle(overlay,first.node_id).match,false);
    assert.equal(contributionNodeStyle(overlay,second.node_id).match,true);
    assert.equal(contributionNodeStyle(overlay,second.node_id).color,CONTRIBUTION_OVERLAP);
    assert.equal(contributionNodeStyle(overlay,third.node_id).color,contributionColor(actorA));
    assert.equal(contributionNodeStyle(overlay,third.node_id).dash,'7 3');
    assert.deepEqual(contributionNodeStyle(overlay,'missing'),{state:'unknown',color:CONTRIBUTION_UNKNOWN,match:false,dash:'2 4'});
    assert.equal(contributionNodeStyle(null,'missing'),null);assert.equal(contributionColor(actorA),contributionColor(actorA));
    assert.notEqual(contributionColor(actorA),contributionColor(actorB));
});

for(const locale of ['en','zh-CN'])test(`actual app colors full PN, focuses nodes, preserves resource control and restores styles in ${locale}`,async()=>{
    const h=await boot({locale}),viewer=h.api.viewer();
    const count=h.frame.net.nodes.length,edgeCount=h.frame.net.edges.length;
    h.document.getElementById('auto-refresh').checked=true;h.api.schedule();assert.equal(h.timers.size,1);
    await h.document.getElementById('user-contributions').onclick();await Promise.resolve();
    assert.equal(viewer.viewMode,'petri');assert.equal(viewer.snapshot.nodes.length,count);assert.equal(viewer.snapshot.edges.length,edgeCount);
    assert.equal(h.timers.size,0,'observation remains fixed while contribution evidence is open');
    const sent=h.requests.at(-1);assert.equal(sent.cache,'no-store');assert.equal(sent.method,undefined);assert.ok(sent.signal);
    assert.equal(panel(h).hidden,false);assert.equal(h.document.getElementById('user-contributions').getAttribute('aria-pressed'),'true');
    const [first,second]=payload().nodes;let cell=viewer.graph.getCell(nodeKey(first.node_id));
    assert.equal(cell.attributes.attrs['body/stroke'],contributionColor(actorA));
    const select=control(h,'actor');select.value=actorB;select.onchange();
    assert.equal(cell.attributes.attrs['root/opacity'],.15);
    assert.equal(viewer.graph.getCell(nodeKey(second.node_id)).attributes.attrs['body/stroke'],CONTRIBUTION_OVERLAP);
    assert.equal(viewer.snapshot.nodes.length,count,'filter dims, never deletes real nodes');
    const focus=control(h,'focus');await focus.onclick();
    assert.equal(h.api.state().selection.id,second.node_id);
    assert.match(panel(h).textContent,/user_collaboration_proposal\/v1/);
    assert.match(panel(h).textContent,/collaboration_net_revision\/v1/);
    // Rendering exact records uses text nodes, never HTML.
    assert.match(panel(h).textContent,locale==='en'?/Authorship is unknown/:/作者身份未知/);
    const resource=h.frame.net.nodes.find(n=>n.category==='resource');
    if(resource){h.document.getElementById('resources').onclick();assert.equal(viewer.graph.getCell(nodeKey(resource.id)).attributes.attrs['root/display'],'none');h.document.getElementById('resources').onclick();}
    panel(h).scrollTop=140;const disclosure=walk(panel(h)).find(node=>node.tagName==='details'&&node.dataset.contributionEvidence);disclosure.open=true;
    const disclosureKey=disclosure.dataset.contributionEvidence;
    const selected=h.api.contributionPanel.state.actor;setLanguage(locale==='en'?'zh-CN':'en');h.api.refreshLanguage();
    // Static localized SVG content may rebuild cells; inspect the current graph.
    cell=viewer.graph.getCell(nodeKey(first.node_id));
    assert.equal(panel(h).scrollTop,140);assert.equal(walk(panel(h)).find(node=>node.dataset.contributionEvidence===disclosureKey).open,true);
    assert.equal(h.api.contributionPanel.state.actor,selected);assert.equal(cell.attributes.attrs['body/stroke'],contributionColor(actorA));
    control(h,'close').onclick();assert.equal(panel(h).textContent,'');assert.equal(panel(h).hidden,true);
    assert.equal(cell.attributes.attrs['body/stroke'],cell.get('baseStroke'));assert.equal(cell.attributes.attrs['body/strokeDasharray'],cell.get('baseStrokeDash'));
    assert.equal(h.api.contributionPanel.state.value,null);assert.equal(h.timers.size,1);
    assert.equal(h.document.getElementById('user-contributions').getAttribute('aria-pressed'),'false');
});

for(const status of [400,403,409,413,501])test(`actual app clears loaded evidence on contribution HTTP ${status}`,async()=>{
    const h=await boot();await h.api.contributionPanel.open();
    h.setResponse(()=>response({error:'not usable'},status));await control(h,'refresh').onclick();
    assert.equal(h.api.contributionPanel.state.value,null);assert.equal(h.api.contributionPanel.state.loading,false);
    assert.ok(h.api.contributionPanel.state.error);assert.doesNotMatch(panel(h).textContent,/resource_version:/);
    for(const node of h.frame.net.nodes)assert.equal(h.api.viewer().graph.getCell(nodeKey(node.id)).attributes.attrs['body/stroke'],CONTRIBUTION_UNKNOWN);
    assert.equal(h.api.state().frame.net.nodes.length,h.frame.net.nodes.length,'main PN survives optional failure');
});

for(const close of ['close','mode','refresh','pagehide','popstate','source-change'])test(`late contribution callback cannot restore state after ${close}`,async()=>{
    const late=deferred(),h=await boot({late});const pending=h.api.contributionPanel.open();
    const request=h.requests.at(-1);
    if(close==='close')control(h,'close').onclick();
    if(close==='mode')await h.document.getElementById('mode-flow').onclick();
    if(close==='refresh')await h.document.getElementById('refresh').onclick();
    if(close==='pagehide'){event(h,'pagehide',true);event(h,'pageshow',true);}
    if(close==='popstate')event(h,'popstate');
    if(close==='source-change'){const replacement=structuredClone(h.api.state().frame);replacement.source.run_dir+='-other';await h.api.render(h.api.state().frame,h.api.state().timeline.serial,replacement);}
    assert.equal(request.signal.aborted,true);
    late.resolve(response(payload()));await pending;
    assert.equal(h.api.contributionPanel.state.open,false);assert.equal(h.api.contributionPanel.state.value,null);assert.equal(panel(h).textContent,'');
    assert.equal(h.document.getElementById('user-contributions').getAttribute('aria-pressed'),'false');
});

test('newer explicit contribution read defeats the older callback even when abort is ignored',async()=>{
    const late=deferred(),h=await boot({late}),pending=h.api.contributionPanel.open();
    const original=h.requests.at(-1);
    h.setResponse(()=>response(payload()));await h.api.contributionPanel.load();
    assert.equal(original.signal.aborted,true);const value=h.api.contributionPanel.state.value;
    late.resolve(response({error:'old permission response'},403));await pending;
    assert.equal(h.api.contributionPanel.state.value,value);assert.equal(h.api.contributionPanel.state.error,null);
});

test('source mismatch and invalid payload cannot color the graph',async()=>{
    const h=await boot(),bad=payload();bad.source.task_id='task:'+ '5'.repeat(32);h.setResponse(()=>response(bad));await h.api.contributionPanel.open();
    assert.equal(h.api.contributionPanel.state.error,'access_changed');
    const invalid=payload();invalid.nodes[0].changes[0].actor='fake';h.setResponse(()=>response(invalid));await h.api.contributionPanel.load();
    assert.equal(h.api.contributionPanel.state.error,'invalid');assert.equal(h.api.contributionPanel.state.value,null);
});

test('new UI text has English translations and equivalent interpolation slots',()=>{
    const source=readFileSync(new URL('../../../cpn/frontend/static/user-contributions.mjs',import.meta.url),'utf8');
    const keys=[...source.matchAll(/(?:translate|messageError)\('([^']+)'/g)].map(match=>match[1]);
    for(const key of keys){assert.ok(EN[key],key);assert.deepEqual(EN[key].match(/\{\d+\}/g),key.match(/\{\d+\}/g));}
    const html=readFileSync(new URL('../../../cpn/frontend/static/index.html',import.meta.url),'utf8');
    assert.match(html,/aria-controls="user-contributions-panel"/);assert.match(html,/aria-labelledby="user-contributions-title"/);
});

test('source identity is pinned across retries and the API envelope cannot reveal run paths',async()=>{
    const h=await boot();await h.api.contributionPanel.open();assert.doesNotMatch(panel(h).textContent,/display-only\/run/);
    const changed=payload();changed.source.source_id='different-authority';h.setResponse(()=>response(changed));await h.api.contributionPanel.load();
    assert.equal(h.api.contributionPanel.state.error,'access_changed');assert.equal(h.api.contributionPanel.state.value,null);
    // A failed refresh does not reset the authority anchor for this exact scope.
    await h.api.contributionPanel.load();assert.equal(h.api.contributionPanel.state.error,'access_changed');
    h.setResponse(()=>response(payload()));await h.api.contributionPanel.load();assert.ok(h.api.contributionPanel.state.value);
});

test('untrusted labels and locators remain plain text and repeated element changes are counted once',async()=>{
    const h=await boot(),raw=payload(),label='<img src=x onerror=alert(1)> {0}';
    raw.actors[0].actor=label;for(const node of raw.nodes)for(const change of node.changes)if(change.actor===actorA){change.actor=label;change.locator='<script>ignored</script>';}
    h.setResponse(()=>response(raw));await h.api.contributionPanel.open();h.api.select('node',raw.nodes[0].node_id);
    assert.match(panel(h).textContent,/4 mapped nodes · 2 distinct element-change records/);
    assert.ok(panel(h).textContent.includes(label));assert.ok(panel(h).textContent.includes('<script>ignored</script>'));
    assert.equal(walk(panel(h)).filter(node=>['script','img','a'].includes(node.tagName)).length,0);
    assert.match(panel(h).textContent,/Adoption record/);assert.match(panel(h).textContent,/Execution record/);
    assert.equal(raw.nodes[0].changes[0].actor,label);
});

test('large PN context survives actor filtering; only panel rows are paginated',async()=>{
    const input=current();for(let i=0;i<1200;i++)input.net.nodes.push({id:`context-${i}`,label:`Context ${i}`,kind:'place',category:'place',hidden_by_default:false,active_token_count:0,tokens:[]});
    const h=await boot({input});await h.api.contributionPanel.open();
    assert.equal(h.api.viewer().snapshot.nodes.length,1204);assert.equal(h.api.viewer().snapshot.edges.length,3);
    assert.equal(walk(panel(h)).filter(node=>node.dataset.contributionAction==='focus').length,80);
    control(h,'more').onclick();assert.equal(walk(panel(h)).filter(node=>node.dataset.contributionAction==='focus').length,160);
    const actor=control(h,'actor');actor.value=actorB;actor.onchange();
    assert.equal(walk(panel(h)).filter(node=>node.dataset.contributionAction==='focus').length,1);
    assert.equal(h.api.viewer().snapshot.nodes.length,1204);assert.equal(h.api.viewer().snapshot.edges.length,3);
    assert.equal(h.api.viewer().graph.getCell(nodeKey('context-1199')).attributes.attrs['root/opacity'],.15);
    assert.equal(h.api.viewer().graph.getCell(nodeKey('context-1199')).attributes.attrs['body/stroke'],CONTRIBUTION_UNKNOWN);
    assert.equal(h.api.viewer().graph.getCell(edgeKey('in')).attributes.attrs['root/display'],null);
});

test('standalone unknown contribution response never invents actors or author colors',async()=>{
    const h=await boot(),unknown=payload();Object.assign(unknown,{coverage:{status:'unknown',reason:'No host records'},actors:[],nodes:[],actions:[],selected_revision_ref:null,adoption_receipt_ref:null});
    h.setResponse(()=>response(unknown));await h.api.contributionPanel.open();
    assert.equal(control(h,'actor').children.length,1);assert.match(panel(h).textContent,/Contribution coverage unknown/);
    for(const node of h.frame.net.nodes)assert.equal(h.api.viewer().graph.getCell(nodeKey(node.id)).attributes.attrs['body/stroke'],CONTRIBUTION_UNKNOWN);
});


test('quick Petri then contributions retains the final complete graph after delayed layout',async()=>{
    const h=await boot(),late=deferred(),requests=[];
    h.setLayout(graph=>{requests.push(graph);return late.promise;});
    const pendingMode=h.api.mode('petri');
    await h.api.contributionPanel.open();
    assert.equal(h.api.contributionPanel.state.open,true);
    assert.ok(h.api.contributionPanel.state.value);
    assert.equal(requests.length,2,'new contribution ticket requests the same still-pending graph');
    h.api.layoutRuns(2);
    const graph=requests.at(-1);
    late.resolve({width:48000,height:400,
        nodes:new Map(graph.nodes.map((n,i)=>[n.id,{x:40+i*12640,y:40,width:n.kind==='place'?56:270,height:n.kind==='place'?56:142,ports:[]}])),
        edges:new Map(graph.edges.map(e=>[e.id,{sources:['source'],targets:['target'],sections:[{startPoint:{x:10,y:10},endPoint:{x:200,y:10}}]}]))});
    await pendingMode;await Promise.resolve();
    const viewer=h.api.viewer();
    assert.equal(viewer.viewMode,'petri');
    assert.equal(viewer.snapshot.nodes.length,h.frame.net.nodes.length);
    assert.equal(viewer.snapshot.edges.length,h.frame.net.edges.length);
    assert.equal(h.document.getElementById('paper').dataset.ready,'true');
    assert.equal(h.document.getElementById('paper').dataset.layoutRuns,'2');
    assert.match(h.document.getElementById('health').textContent,/Layouts: 2/);
    assert.ok(viewer.viewport().scale<.03,'fit is allowed to show a very long complete net at small scale');
    const row=control(h,'focus');await row.onclick();
    assert.equal(h.api.state().selection.id,row.dataset.nodeId);
    assert.ok(viewer.viewport().scale>=.55,'actual node focus restores readable scale without changing topology');
    assert.equal(viewer.snapshot.nodes.length,h.frame.net.nodes.length);
    assert.match(panel(h).textContent,/user_collaboration_proposal\/v1/);
});
