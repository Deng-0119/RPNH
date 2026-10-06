import test,{afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {comparisonPair,comparisonPath,comparisonChoices,normalizeComparison,comparisonAxes,
    ComparisonState,installComparisonPanel} from '../../../cpn/frontend/static/comparison-view.mjs';
import {setLanguage,t} from '../../../cpn/frontend/static/i18n.mjs';
import {checkpointApp,response,deferred} from './checkpoint-test-harness.mjs';
const fixture=JSON.parse(readFileSync(new URL('./comparison-fixture.json',import.meta.url),'utf8'));
const data=()=>structuredClone(fixture),pair=()=>comparisonPair(fixture.left_selector,fixture.right_selector);
const frame=()=>structuredClone(fixture.right.frame),items=()=>[fixture.left_selector,fixture.right_selector]
    .map(s=>({cursor:s.cut,checkpoint_ref:structuredClone(s.checkpoint_ref)}));
const source=()=>structuredClone(fixture.source);
afterEach(()=>setLanguage('en'));
const walk=node=>[node,...node.children.flatMap(walk)];
const button=(container,text)=>walk(container).find(n=>n.tagName==='button'&&n.textContent===text);
function payload(left,right) {
    return {...data(),left:structuredClone(left),right:structuredClone(right),
        left_selector:structuredClone(left.selector),right_selector:structuredClone(right.selector),
        axes:comparisonAxes(left.frame,right.frame)};
}
function panel(request) {
    const h=checkpointApp(async()=>{throw Error('unexpected app read');});
    const container=h.document.createElement('section'),events=new EventTarget();
    const result=installComparisonPanel(container,{request,translate:t,events});
    result.open(frame(),items());return {...result,container,events};
}

test('Python-produced comparison wire round-trips through strict JS normalization',()=>{
    assert.deepEqual(normalizeComparison(data(),pair(),source()),data());
    assert.match(comparisonPath(pair()),/^\/api\/v2\/comparison-view\?left_selector=/);
    assert.equal(comparisonChoices(frame(),items()).length,2);
    const value=normalizeComparison(payload(fixture.left,fixture.left),comparisonPair(fixture.left_selector,fixture.left_selector),source());
    assert.equal(value.coverage.status,'partial');
    assert.equal(value.axes.native_scores.coverage,'not_provided');
});
test('cross-net identity, guessed selectors and unsafe cut values are rejected',()=>{
    for(const change of [v=>v.cut=0,v=>v.cut=true,v=>v.cut=2**53,v=>v.extra=1,
        v=>v.net_ref.version_id='net_instance_version:'+'a'.repeat(32)]) {
        const selected=structuredClone(fixture.left_selector);change(selected);
        assert.throws(()=>comparisonPair(selected,fixture.right_selector));
    }
});
test('omitted differences, false availability, false scoring and mixed cuts fail closed',()=>{
    const changes=[v=>v.global_atomic_snapshot=true,v=>v.comparability='satisfied_for_declared_claim',
        v=>v.axes.tokens.rows=[],v=>v.axes.native_scores.coverage='complete_in_declared_scope',
        v=>v.coverage.status='complete',v=>v.right.capture.head_ordinal++,
        v=>v.right.frame.source.task_id='foreign',v=>v.right.frame.net.marking.checkpoint_ref=fixture.left_selector.checkpoint_ref,
        v=>v.axes.tokens.rows[0].reason_source='recorded_decision',
        v=>v.axes.tokens.rows[0].classification='deleted',v=>v.right.coverage.marking='unavailable'];
    for(const mutate of changes){const value=data();mutate(value);assert.throws(()=>normalizeComparison(value,pair(),source()));}
});
test('nested private extensions never enter client state',()=>{
    const changes=[v=>v.right.private_extension={body:'SECRET'},
        v=>v.right.frame.net.nodes[0].config={prompt:'SECRET'},
        v=>v.right.frame.net.nodes[0].tokens[0].resource_ref={resource_id:'r',resource_version_id:'v',body:'SECRET'},
        v=>v.right.frame.net.marking.private='SECRET',v=>v.right.frame.net.summary.private='SECRET',
        v=>v.right.frame.source.extra='SECRET',v=>v.right.frame.presentation={prompt:'SECRET'},
        v=>v.right.frame.boundaries.entry.push({name:'x',port:'x',place:'p',private:'SECRET'})];
    for(const mutate of changes) {
        const state=new ComparisonState();state.show(frame(),items());const ticket=state.begin();
        const value=data();mutate(value);assert.throws(()=>state.accept(ticket,value));assert.equal(state.value,null);
    }
});
test('missing provided field remains unknown instead of deletion or equality',()=>{
    const left=structuredClone(fixture.left),right=structuredClone(fixture.right);delete right.frame.net.nodes[0].capacity;
    const value=normalizeComparison(payload(left,right),pair(),source());
    const row=value.axes.definition.rows.find(r=>r.field==='capacity');
    assert.equal(row.classification,'unavailable');assert.equal(row.right_fact.status,'not_provided');
});
test('same resource at a different exact token occurrence is never paired by name',()=>{
    const left=structuredClone(fixture.left),right=structuredClone(fixture.right);
    right.frame.net.nodes[0].tokens[0].token_ref.version_id='petri_token_version:'+'a'.repeat(32);
    const value=normalizeComparison(payload(left,right),pair(),source());
    assert.deepEqual(new Set(value.axes.tokens.rows.map(r=>r.classification)),new Set(['present_left_only','present_right_only']));
});
for(const language of ['en','zh-CN'])test(`panel renders declared gaps and HTML-looking data as text in ${language}`,async()=>{
    setLanguage(language);const value=data();const p=panel(async()=>value);
    await button(p.container,t('读取比较')).onclick();
    assert.equal(p.state.value.comparison_mode,'descriptive');
    assert.match(p.container.textContent,language==='en'?/Not provided; remains unknown/:/未提供，保持未知/);
    assert.match(p.container.textContent,language==='en'?/No winner or causal claim/:/不判断优胜或因果/);
    // Label changes remain ordinary text even when they contain executable-looking markup.
    const changed=data();changed.right.frame.net.nodes[0].label='<script>alert(1)</script>';
    changed.axes=comparisonAxes(changed.left.frame,changed.right.frame);
    p.close();const other=panel(async()=>changed);await button(other.container,t('读取比较')).onclick();
    assert.match(other.container.textContent,/<script>alert\(1\)<\/script>/);
    assert.equal(walk(other.container).some(n=>['script','img','iframe'].includes(n.tagName)),false);
});
test('repeated Read comparison clicks make one read and disclose no half-pair',async()=>{
    const wait=deferred();let calls=0;const p=panel(async()=>{calls++;return wait.promise;});
    const read=button(p.container,'Read comparison'),pending=read.onclick();await read.onclick();
    assert.equal(calls,1);assert.equal(p.state.value,null);assert.equal(p.state.loading,true);
    wait.resolve(data());await pending;assert.equal(p.state.loading,false);assert.equal(p.state.value.right_selector.cut,30);
});
test('Close/cancel clears output, options and source and rejects delayed data',async()=>{
    const wait=deferred();const p=panel(async()=>wait.promise),pending=button(p.container,'Read comparison').onclick();
    button(p.container,'Close comparison').onclick();wait.resolve(data());await pending;
    assert.equal(p.state.open,false);assert.equal(p.state.value,null);assert.deepEqual(p.state.options,[]);
    assert.equal(p.state.source,null);assert.equal(p.container.textContent,'');
});
test('new selection invalidates pending read; later old response cannot overwrite it',async()=>{
    const old=deferred(),newer=deferred();let calls=0;const p=panel(async()=>++calls===1?old.promise:newer.promise);
    const first=button(p.container,'Read comparison').onclick();
    const selects=walk(p.container).filter(n=>n.tagName==='select');selects[1].value='0';selects[1].onchange();
    assert.equal(p.state.value,null);const second=button(p.container,'Read comparison').onclick();
    newer.resolve(payload(fixture.left,fixture.left));await second;
    old.resolve(data());await first;assert.equal(p.state.value.right_selector.cut,20);assert.equal(calls,2);
});
for(const status of [403,409,503])test(`refresh HTTP ${status} clears previous comparison without cached fallback`,async()=>{
    let calls=0;const p=panel(async()=>{if(++calls===1)return data();const e=new Error('private');e.status=status;throw e;});
    await button(p.container,'Read comparison').onclick();assert.ok(p.state.value);
    const pending=button(p.container,'Read comparison').onclick();assert.equal(p.state.value,null);await pending;
    assert.equal(p.state.value,null);assert.equal(p.state.loading,false);assert.match(p.container.textContent,/previous results were cleared/);
});
for(const event of ['popstate','pagehide','pageshow'])test(`${event} discards result and late completion without history writes`,async()=>{
    const wait=deferred();const p=panel(async()=>wait.promise);const pending=button(p.container,'Read comparison').onclick();
    p.events.dispatchEvent(new Event(event));wait.resolve(data());await pending;
    assert.equal(p.state.open,false);assert.equal(p.state.value,null);assert.equal(p.container.textContent,'');
});
test('actual app opens loaded exact choices, stops polling, and newer navigation clears comparison',async()=>{
    const late=deferred();const h=checkpointApp(async path=>{
        if(path.startsWith('/api/v2/comparison-view'))return late.promise;
        if(path.startsWith('/api/v1/dashboard'))return response(frame());
        if(path==='/api/v1/history')return response({schema_version:'rpnh/dashboard_history/v1',source:frame().source,items:items(),coverage:'current_net_canonical_checkpoints',next_before:null,end_reason:'initial_checkpoint'});
        throw Error('unexpected request '+path);
    });
    h.api.install(frame());h.api.history({schema_version:'rpnh/dashboard_history/v1',source:frame().source,items:items(),coverage:'current_net_canonical_checkpoints',next_before:null,end_reason:'initial_checkpoint'});
    await h.api.render();h.document.getElementById('auto-refresh').checked=true;h.api.schedule();assert.ok(h.timers.size);
    h.api.openComparison();assert.equal(h.timers.size,0);assert.equal(h.api.comparisonPanel.state.options.length,2);
    const pending=button(h.document.getElementById('comparison-panel'),'Read comparison').onclick();
    await h.api.returnToLive();late.resolve(response(data()));await pending;
    assert.equal(h.api.comparisonPanel.state.value,null);assert.equal(h.api.comparisonPanel.state.open,false);
    assert.equal(h.document.getElementById('comparison-panel').textContent,'');
});


test('unsupported initial view comparison does not stop an existing live refresh choice',async()=>{
    const h=checkpointApp(async()=>{throw Error('unsupported compare must not read');});
    const value=frame();value.source.mode=value.net.source.mode='initial_configured';
    delete value.source.net_ref;delete value.net.source.net_ref;delete value.net.marking.checkpoint_ref;
    value.position.mode='live';value.coverage.history='unsupported';
    h.api.install(value);h.document.getElementById('auto-refresh').checked=true;h.api.schedule();
    const before=h.timers.size;assert.equal(before,1);h.api.openComparison();
    assert.equal(h.timers.size,before);assert.equal(h.document.getElementById('auto-refresh').checked,true);
    assert.equal(h.api.comparisonPanel.state.open,false);assert.match(h.document.getElementById('error-banner').textContent,/no exact checkpoint/);
});


test('correlated private capture extensions are rejected at every envelope level',()=>{
    const value=data();
    for(const capture of [value.capture,value.left.capture,value.right.capture])capture.private_extension={credential:'PRIVATE-CAPTURE'};
    assert.throws(()=>normalizeComparison(value,pair(),source()));
});

for(const sequence of [['popstate'],['pagehide','pageshow']])
for(const autoRefresh of [true,false])test(`${sequence.join('/')} clears comparison and preserves live refresh ${autoRefresh}`,async()=>{
    const late=deferred(), h=checkpointApp(async()=>late.promise);
    h.api.install(frame());await h.api.render();
    h.document.getElementById('auto-refresh').checked=autoRefresh;h.api.schedule();
    h.api.openComparison();assert.equal(h.timers.size,0);
    const pending=button(h.document.getElementById('comparison-panel'),'Read comparison').onclick();
    for(const event of sequence) {
        h.window.dispatchEvent(new Event(event));
        if(event==='pagehide')assert.equal(h.timers.size,0,'hidden page must not start polling');
    }
    late.resolve(response(data()));await pending;
    assert.equal(h.api.comparisonPanel.state.open,false);
    assert.equal(h.api.comparisonPanel.state.value,null);
    assert.equal(h.document.getElementById('comparison-panel').textContent,'');
    assert.equal(h.timers.size,autoRefresh?1:0,'restore the chosen live polling after navigation');
    assert.equal(h.document.getElementById('auto-refresh').checked,autoRefresh);
});
