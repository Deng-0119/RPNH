"""One Chromium acceptance path for main and any v1 projection provider.

Uses main's native integer-loop Registry, initial compiled structure and clearly
labelled synthetic UI cases. Optional --snapshot inputs exercise the same view;
no backend-specific renderer, dispatch adapter or provider is imported here.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import platform
import re
import selectors
import subprocess
import sys
import tempfile
import time
import traceback
import math

# Fixture creation is separate from the reader. The same main-only writer and
# browser assertions run on the main baseline and optional enriched hosts.
from net_viewer_fixture import make_run, make_agent_run
from cpn.rpnh.registry._registry import _RegistryCore


@contextmanager
def server(args):
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf8", cwd=tempfile.gettempdir())
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("viewer server did not become ready")
        line = proc.stdout.readline()
        match = re.search(r"http://127\.0\.0\.1:\d+/", line)
        if not match:
            raise RuntimeError("viewer did not print its local endpoint: " + line)
        yield match.group()
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)
        proc.stdout.close(); proc.stderr.close()


def json_server(path):
    return [sys.executable, "-I", "-u", "-c",
            "import json,sys;from pathlib import Path;from cpn.frontend.server import serve_projection;"
            "p=Path(sys.argv[1]);serve_projection(lambda:json.loads(p.read_text()),open_browser=False)", str(path)]


def dashboard_json_server(path):
    code = """import json, sys
from pathlib import Path
from cpn.frontend.server import serve_projection
class Fixture:
    def __call__(self): return self.dashboard()['net']
    def dashboard(self, cursor=None):
        if cursor is not None: raise ValueError('Synthetic fixture has no history')
        return json.loads(Path(sys.argv[1]).read_text())
serve_projection(Fixture(), open_browser=False)
"""
    return [sys.executable, '-I', '-u', '-c', code, str(path)]


def readability_fixture(crossing=False):
    def node(id, kind):
        return dict(id=id,label=id,kind=kind,category='place' if kind=='place' else 'execution',hidden_by_default=False)
    def arc(id, source, target, mode='consume'):
        return dict(id=id,source=source,target=target,kind='arc',mode=mode,weight=1,outcome=None,hidden_by_default=False)
    if crossing:
        nodes=[node('data'+str(i),'place') for i in range(4)]+[node('step'+str(i),'transition') for i in range(4)]
        nodes[3]['category']='resource'
        edges=[arc(p['id']+'-'+t['id'],p['id'],t['id']) for p in nodes[:4] for t in nodes[4:]]
        boundaries=dict(entry=[],exit=[],terminal_rules=[])
    else:
        nodes=[node('input','place'),node('prepare','transition'),node('handoff1','place'),node('handoff2','place'),node('review','transition'),node('output','place')]
        edges=[arc('input-prepare','input','prepare'),arc('prepare-h1','prepare','handoff1','produce'),arc('prepare-h2','prepare','handoff2','produce'),arc('h1-review','handoff1','review'),arc('h2-review','handoff2','review'),arc('review-output','review','output','produce')]
        boundaries=dict(entry=[dict(name='request',place='input')],exit=[dict(name='result',place='output')],terminal_rules=[])
    source=dict(mode='registry_current',run_dir='synthetic-readability-only',net_ref={'version_id':'readability-v1'},verified_head_ordinal=0)
    net=dict(schema_version='rpnh/net_view/v1',source=source,summary={},nodes=nodes,edges=edges)
    return dict(schema_version='rpnh/dashboard/v1',net=net,source=source,boundaries=boundaries,
        agent_nodes=[] if crossing else [{'transition_id':name,'source':'synthetic_agent_fixture'} for name in ['prepare','review']],
        transition_bindings=[],presentation=dict(title='Readability fixture',description='Synthetic geometry and provenance checks; not an execution.',nodes={},status='default'),
        position=dict(mode='live',cursor=0,latest_head=0),coverage=dict(history='unsupported',firings='not_provided'),change={})


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf8")
    temporary.replace(path)


def synthetic(size=1):
    nodes = []
    edges = []
    for i in range(size):
        nodes.extend([
            {"id": f"p{i}", "label": f"输入库所 {i}", "kind": "place", "category": "place", "hidden_by_default": False, "active_token_count": 1, "tokens": []},
            {"id": f"t{i}", "label": f"测试转换 {i} <script>alert(1)</script>", "kind": "transition", "category": "execution", "hidden_by_default": False,
             "runtime": {"status": "settled", "firing_count": 2, "firings": [
                 {"status": "outcome_unknown", "publication_state": "PROVISIONAL", "admission_ordinal": i*3+1, "firing_ref": {"version_id": f"pending-{i}"}},
                 {"status": "settled", "business_outcome": "deny", "admission_ordinal": i*3+2, "firing_ref": {"version_id": f"settled-{i}"}}]}}])
        for name, source, target, mode, outcome in [
            ("consume", f"p{i}", f"t{i}", "consume", None),
            ("again", f"t{i}", f"p{i}", "produce", "again"),
            ("interrupted", f"t{i}", f"p{i}", "produce", "interrupted")]:
            edges.append({"id": f"{name}{i}", "source": source, "target": target, "kind": "arc", "mode": mode, "weight": 1, "outcome": outcome, "hidden_by_default": False})
        if i:
            edges.append({"id": f"chain{i}", "source": f"t{i-1}", "target": f"p{i}", "kind": "arc", "mode": "produce", "weight": 1, "outcome": "complete", "hidden_by_default": False})
    return {"schema_version": "rpnh/net_view/v1", "source": {"mode": "registry_current", "run_dir": "synthetic-rendering-only", "net_ref": {"version_id": "synthetic-v1"}},
            "summary": {}, "nodes": nodes, "edges": edges, "execution": {"head_ordinal": 1000}}


def assert_canvas_size(page):
    """A populated DOM is not sufficient: the SVG must occupy the host."""
    page.wait_for_function("""() => {
      const host = document.querySelector('#canvas-wrap');
      const paper = document.querySelector('#paper');
      return host.clientWidth > 100 && host.clientHeight > 100 &&
        Math.abs(host.clientWidth - paper.clientWidth) <= 1 &&
        Math.abs(host.clientHeight - paper.clientHeight) <= 1;
    }""")
    return page.locator('#paper').bounding_box()



def viewport_transforms(page):
    """Check populated layers and actual captions, whether inline or detached."""
    layers = page.locator("#paper .joint-viewport").evaluate_all("""elements =>
      elements.filter(el => el.childElementCount > 0).map(el => {
        const m = el.getCTM();
        return {layer: el.getAttribute('class'), matrix: m ? [m.a, m.b, m.c, m.d, m.e, m.f] : null};
      })
    """)
    labels = page.locator("#paper .label[data-edge-id]").evaluate_all("""elements =>
      elements.map(el => {
        const m = el.getCTM();
        return {layer: 'caption:' + el.dataset.edgeId, matrix: m ? [m.a, m.b, m.c, m.d, m.e, m.f] : null};
      })
    """)
    assert layers and labels, "populated graph layers and actual arc captions must be measured"
    records = layers + labels
    for record in records:
        matrix = record["matrix"]
        assert matrix and all(math.isfinite(v) for v in matrix), records
        assert matrix[0] > 0 and matrix[3] > 0, records
        # Captions have their own translation, but must share graph scale.
        assert matrix[:4] == layers[0]["matrix"][:4], records
    return records


def assert_place_labels(page):
    """Verify actual rendered text below and centered on every place body."""
    positions = page.locator("#paper .node").evaluate_all("""nodes => nodes
      .filter(node => node.querySelector('circle[joint-selector="body"]'))
      .map(node => {
        const body = node.querySelector('circle[joint-selector="body"]').getBoundingClientRect();
        const text = node.querySelector('text[joint-selector="title"]').getBoundingClientRect();
        return {id: node.dataset.id, body: {x: body.x, y: body.y, width: body.width, height: body.height},
          label: {x: text.x, y: text.y, width: text.width, height: text.height}};
      })""")
    assert positions
    for item in positions:
        body, label = item["body"], item["label"]
        assert label["y"] >= body["y"] + body["height"] - 1, item
        assert abs(label["x"] + label["width"]/2 - body["x"] - body["width"]/2) <= 2, item
    return positions


def click_arc(page, edge_id):
    """Click a real visible point of the path; never dispatch or force events."""
    points = page.locator('.edge-group').evaluate_all("""(groups, id) => {
      const group = groups.find(g => g.dataset.id === id);
      const path = group?.querySelector('path[joint-selector="line"]');
      if (!path) return [];
      const matrix = path.getScreenCTM(), length = path.getTotalLength();
      return Array.from({length: 19}, (_, i) => {
        const p = path.getPointAtLength(length * (i + 1) / 20);
        const screen = new DOMPoint(p.x, p.y).matrixTransform(matrix);
        const hit = document.elementFromPoint(screen.x, screen.y)?.closest('.edge-group');
        return hit?.dataset.id === id ? {x: screen.x, y: screen.y} : null;
      }).filter(Boolean);
    }""", edge_id)
    assert points, f"arc has no visible pointer target: {edge_id}"
    page.mouse.click(points[0]["x"], points[0]["y"])
    page.locator('#tab-evidence').click()
    assert edge_id in page.locator('#detail').inner_text()


def main():
    from playwright.sync_api import sync_playwright, expect
    from cpn.frontend.dashboard import RegistryDashboard, topology_digest
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--snapshot',action='append',type=Path,default=[])
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    report={'passed':False,'cases':[],'page_errors':[],'outbound_requests':[],
            'python':platform.python_version(),'scope':'installed wheel / HTTP / CSP / Chromium; deterministic native Registry, no model'}
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);run=root/'run';initial,before,final,catalog=make_run(run)
        reader=RegistryDashboard(run,catalog=catalog);history=reader.history()['items']
        manifest={'schema_version':'rpnh/presentation/v1','topology_digest':topology_digest(final),
          'title':'数据处理任务','description':'接收输入，逐轮处理，满足条件后交付结果。',
          'nodes':{'loop.input':{'name':'接收任务','description':'任务输入与下一轮的数据。'},
                   'loop.step':{'name':'迭代处理','type':'计算步骤','description':'按既定规则处理数据，决定继续还是输出结果。'},
                   'loop.done':{'name':'交付结果','description':'满足声明条件后的结果出口。'}}}
        presentation=root/'presentation.json';save(presentation,manifest)
        head_before=reader._open().event_store.max_ordinal()
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,**({'executable_path':os.environ['RPNH_BROWSER']} if os.environ.get('RPNH_BROWSER') else {}))
            page=browser.new_page(viewport={'width':1440,'height':1000},locale='zh-CN')
            page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
            page.on('request',lambda r:report['outbound_requests'].append(r.url) if not re.match(r'^http://127\.0\.0\.1:\d+/',r.url) else None)
            page.add_init_script("window.__csp=[];document.addEventListener('securitypolicyviolation',e=>window.__csp.push(e.violatedDirective))")
            def ready(check_default=False):
                page.wait_for_selector('#paper[data-ready="true"]',timeout=45000)
                page.locator('#auto-refresh').uncheck()
                if check_default:
                    expect(page.locator('html')).to_have_attribute('lang','en')
                    expect(page.locator('#language')).to_have_value('en')
                    expect(page.locator('#mode-overview')).to_have_text('Overview')
                    expect(page.locator('#mode-overview')).to_have_attribute('aria-pressed','true')
                    expect(page.locator('#mode-flow')).to_have_text('Detailed flow')
                    expect(page.locator('#source-badge')).to_have_text('Current state')
                    expect(page.locator('#search')).to_have_attribute('placeholder','Find a step, name or ID')
                    page.locator('#help').click()
                    expect(page.locator('#guide h2')).to_have_text('Understanding this run')
                    assert not re.search(r'[\u3400-\u9fff]',page.locator('#guide').inner_text())
                    page.locator('#close-help').click()
                    page.screenshot(path=str(out/'language-default-english.png'))
                # Existing semantic/browser checks remain explicit Chinese tests.
                page.locator('#language').select_option('zh-CN')
                expect(page.locator('html')).to_have_attribute('lang','zh-CN')
                assert_canvas_size(page)
            def mode(name):
                page.locator('#mode-'+name).click()
                expect(page.locator('#paper')).to_have_attribute('data-view-mode',name)
            def shot(name):page.screenshot(path=str(out/name))
            try:
                command=[sys.executable,'-I','-u','-m','cpn.frontend.dashboard','--run',str(run),'--presentation',str(presentation),'--no-open']
                with server(command) as url:
                    page.goto(url);ready(check_default=True)
                    assert page.locator('.node').count()==0
                    expect(page.locator('#canvas-empty')).to_contain_text('此流程没有声明的智能体')
                    assert not page.locator('#resources').is_visible()
                    page.locator('#open-petri-empty').click();mode('flow')
                    expect(page.locator('#run-title')).to_have_text('数据处理任务')
                    expect(page.locator('#metric-settled')).to_have_text('3')
                    assert page.locator('.node').count()==len(final['nodes'])
                    expect(page.locator('#time-slider')).to_be_enabled()
                    expect(page.locator('#time-position')).to_contain_text('4 / 4')
                    shot('dashboard-flow.png')
                    page.locator('.node[data-id="loop.step"]').click()
                    expect(page.locator('#detail-title')).to_contain_text('迭代处理')
                    expect(page.locator('#detail')).not_to_contain_text('node_synopsis')
                    shot('dashboard-detail.png')
                    page.locator('#tab-evidence').click()
                    page.locator('#detail summary').filter(has_text='精确执行绑定').click()
                    expect(page.locator('#detail')).to_contain_text('operation_binding_ref')
                    page.locator('#tab-executions').click()
                    expect(page.locator('#detail')).to_contain_text('已结算')
                    original=page.locator('.node[data-id="loop.step"]').get_attribute('transform')
                    runs=page.locator('#paper').get_attribute('data-layout-runs')
                    page.locator('#time-slider').focus();page.keyboard.press('Home')
                    expect(page.locator('#paper')).to_have_attribute('data-head',str(history[0]['cursor']))
                    expect(page.locator('#metric-settled')).to_have_text('0')
                    expect(page.locator('#metric-active')).to_have_text('—')
                    assert page.locator('.node[data-id="loop.step"]').get_attribute('transform')==original
                    assert page.locator('#paper').get_attribute('data-layout-runs')==runs
                    # Switching language must not change history, viewport, selection,
                    # resource visibility, exact evidence or replay controls.
                    page.locator('#tab-evidence').click()
                    page.locator('#resources').click()
                    page.locator('#zoom-in').click()
                    def interaction_state():
                        return page.evaluate('''() => ({
                          head:document.querySelector('#paper').dataset.head,
                          layouts:document.querySelector('#paper').dataset.layoutRuns,
                          mode:document.querySelector('#paper').dataset.viewMode,
                          slider:document.querySelector('#time-slider').value,
                          resources:document.querySelector('#resources').getAttribute('aria-pressed'),
                          selected:[...document.querySelectorAll('.node[aria-pressed="true"]')].map(x=>x.dataset.id),
                          positions:[...document.querySelectorAll('.node')].map(x=>[x.dataset.id,x.getAttribute('transform')]),
                          matrices:[...document.querySelectorAll('#paper .joint-viewport')].filter(x=>x.childElementCount).map(x=>{const m=x.getCTM();return [m.a,m.b,m.c,m.d,m.e,m.f]}),
                          raw:[...document.querySelectorAll('#detail pre')].map(x=>x.textContent),
                          tab:document.querySelector('#tab-evidence').getAttribute('aria-pressed')
                        })''')
                    before_language=interaction_state()
                    page.locator('#language').select_option('en')
                    expect(page.locator('html')).to_have_attribute('lang','en')
                    expect(page.locator('#source-badge')).to_have_text('Historical checkpoint')
                    expect(page.locator('#detail-title')).to_have_text('迭代处理')
                    expect(page.locator('#detail')).to_contain_text('Exact execution bindings')
                    assert interaction_state()==before_language
                    shot('language-history-english.png')
                    page.locator('#language').select_option('zh-CN')
                    expect(page.locator('#detail')).to_contain_text('精确执行绑定')
                    assert interaction_state()==before_language
                    shot('language-history-chinese.png')
                    page.locator('#resources').click();page.locator('#fit').click()
                    page.locator('#clear-selection').click();shot('dashboard-history.png')
                    page.locator('#next').click()
                    expect(page.locator('#paper')).to_have_attribute('data-head',str(history[1]['cursor']))
                    expect(page.locator('#metric-settled')).to_have_text('1')
                    page.locator('#back-live').click()
                    expect(page.locator('#metric-settled')).to_have_text('3')
                    # Real keyboard/mouse navigation must cancel the pending 100ms
                    # slider read. It must not yank a later live choice into history.
                    page.locator('#time-slider').focus();page.keyboard.press('Home')
                    page.locator('#back-live').click()
                    page.wait_for_timeout(350)
                    expect(page.locator('#source-badge')).to_have_text('当前状态')
                    expect(page.locator('#metric-settled')).to_have_text('3')
                    expect(page.locator('#back-live')).to_have_attribute('aria-pressed','true')
                    assert page.locator('#observation-time').get_attribute('datetime')
                    assert '共 4 个检查点' in page.locator('#time-slider').get_attribute('aria-valuetext')
                    # Start playback then return live: an in-flight playback
                    # callback must not reschedule itself after this choice.
                    page.locator('#play').click();page.locator('#back-live').click()
                    page.wait_for_timeout(1650)
                    expect(page.locator('#source-badge')).to_have_text('当前状态')
                    expect(page.locator('#play')).to_have_text('播放')
                    mode('petri');assert_place_labels(page)
                    # Regression: Chinese must reach actual Petri SVG labels,
                    # not just HTML/flow captions. Raw technical symbols stay exact.
                    expect(page.locator('#mode-petri')).to_have_text('Petri 网')
                    expect(page.locator('.node[data-id="loop.step"] text[joint-selector="eyebrow"]')).to_have_text('转换')
                    assert any('消费' in s for s in page.locator('#paper .label').all_text_contents())
                    pn_state = interaction_state()
                    page.locator('#language').select_option('en')
                    expect(page.locator('#mode-petri')).to_have_text('PetriNet')
                    expect(page.locator('.node[data-id="loop.step"] text[joint-selector="eyebrow"]')).to_have_text('TRANSITION')
                    assert any('Consume' in s for s in page.locator('#paper .label').all_text_contents())
                    assert interaction_state()==pn_state
                    shot('readability-petri-english.png')
                    page.locator('#language').select_option('zh-CN')
                    assert interaction_state()==pn_state
                    shot('readability-petri-chinese.png')
                    assert page.locator('.edge-group').count()==len(final['edges'])
                    a=viewport_transforms(page);page.locator('#zoom-in').click();b=viewport_transforms(page)
                    assert b[0]['matrix'][0]>a[0]['matrix'][0]
                    page.locator('#fit').click();shot('dashboard-petri.png')
                    page.locator('.node[data-id="loop.step"]').focus();page.keyboard.press('Enter')
                    page.locator('#tab-evidence').click();expect(page.locator('#detail')).to_contain_text('loop.step')
                    mode('list');assert page.locator('#execution-table tbody tr').count()==3
                    page.locator('#execution-table tbody tr button').first.click()
                    expect(page.locator('#detail')).to_contain_text('业务结果')
                    mode('flow');page.locator('#clear-selection').click()
                    page.locator('#search').fill('迭代处理');page.keyboard.press('Enter')
                    expect(page.locator('#detail-title')).to_contain_text('迭代处理')
                    page.locator('#search').fill('');page.locator('#clear-selection').click()
                    page.set_viewport_size({'width':900,'height':850});assert_canvas_size(page);page.locator('#fit').click()
                    shot('dashboard-narrow.png');page.set_viewport_size({'width':1440,'height':1000})
                    assert not page.evaluate('window.__csp')
                    response=page.request.post(url+'api/v1/dashboard');assert response.status==405
                    # Reopen the page: history is from persisted Registry, not a tab cache.
                    page.reload();page.wait_for_selector('#paper[data-ready="true"]',timeout=45000)
                    expect(page.locator('#language')).to_have_value('zh-CN')
                    expect(page.locator('#mode-flow')).to_have_text('详细流程')
                    page.locator('#language').select_option('en');page.reload()
                    page.wait_for_selector('#paper[data-ready="true"]',timeout=45000)
                    expect(page.locator('#language')).to_have_value('en')
                    expect(page.locator('#mode-overview')).to_have_text('Overview')
                    expect(page.locator('#mode-overview')).to_have_attribute('aria-pressed','true')
                    expect(page.locator('#mode-flow')).to_have_text('Detailed flow')
                    ready();expect(page.locator('#time-position')).to_contain_text('4 / 4')
                    report['cases'].append('english_default_bilingual_switch_preserves_history_geometry_evidence_and_reload_preference')
                    report['cases'].append('native_persistent_checkpoints_dual_views_details_slider_keyboard_resize')
                # Standard installed command delegates only --view to the dashboard.
                with server([sys.executable,'-I','-u','-m','cpn.rpnh_cli','net','--run',str(run),'--view','--no-open']) as url:
                    page.goto(url);ready(check_default=True);expect(page.locator('#time-slider')).to_be_enabled()
                    assert page.locator('.node').count()==0
                    mode('flow')
                    page.locator('#language').select_option('en')
                    expect(page.locator('#run-title')).to_have_text('Run workflow')
                    expect(page.locator('.node[data-id="loop.input"]')).to_contain_text('Receive task')
                    shot('language-main-english.png')
                    page.set_viewport_size({'width':900,'height':850});assert_canvas_size(page)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    shot('language-main-english-narrow.png')
                    page.set_viewport_size({'width':1440,'height':1000})
                    report['cases'].append('standard_rpnh_net_view_dashboard_entry')
                path=root/'legacy.json';save(path,initial)
                with server(json_server(path)) as url:
                    page.goto(url);ready();mode('petri');assert_place_labels(page)
                    expect(page.locator('#time-slider')).to_be_disabled()
                    assert page.locator('.node').count()==len(initial['nodes'])
                    report['cases'].append('initial_v1_provider_without_fake_history')
                data=synthetic();save(path,data)
                with server(json_server(path)) as url:
                    page.goto(url);ready();mode('petri')
                    expect(page.locator('#metric-active')).to_have_text('1')
                    assert not page.evaluate("document.querySelector('#paper script')")
                    edge_id=data['edges'][0]['id'];page.locator('.node[data-id="t0"]').click();page.locator('#tab-evidence').click();click_arc(page,edge_id)
                    page.locator('#clear-selection').click()
                    # Data-layer failures retain the last good frame.
                    malformed=deepcopy(data);malformed['edges'][0]['target']='missing';save(path,malformed)
                    page.locator('#refresh').click();expect(page.locator('#health')).to_have_attribute('data-state','stale')
                    assert page.locator('.node').count()==2
                    stale=deepcopy(data);stale['execution']['head_ordinal']=999;save(path,stale)
                    page.locator('#refresh').click();expect(page.locator('#error-banner')).to_contain_text('较旧')
                    large=synthetic(100);large['execution']['head_ordinal']=1001;save(path,large)
                    page.locator('#refresh').click();expect(page.locator('.node')).to_have_count(200,timeout=45000)
                    page.locator('#fit').click();assert_canvas_size(page);assert_place_labels(page)
                    bounds=page.locator('.node').evaluate_all("els=>els.map(e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom}})")
                    box=page.locator('#paper').bounding_box()
                    assert all(x['x']>=box['x']-2 and x['right']<=box['x']+box['width']+2 and x['y']>=box['y']-2 and x['bottom']<=box['y']+box['height']+2 for x in bounds)
                    page.locator('#search').fill('t50');page.keyboard.press('Enter');page.locator('.node[data-id="t50"]').click()
                    expect(page.locator('#detail')).to_contain_text('t50')
                    shot('dashboard-large-pn.png');report['cases'].append('legacy_unknown_firing_malicious_text_stale_bad_snapshot_200_nodes_399_arcs')
                resource=synthetic();resource['nodes'].append({'id':'capacity','label':'共享容量','kind':'place','category':'resource','hidden_by_default':True,'active_token_count':2})
                resource['edges'].append({'id':'read-capacity','source':'capacity','target':'t0','kind':'arc','mode':'read','weight':1,'outcome':None,'hidden_by_default':True,'resource':True})
                save(path,resource)
                with server(json_server(path)) as url:
                    page.goto(url);ready()
                    for m in ['flow','petri']:
                        mode(m);page.locator('#resources').evaluate('(e)=>e.blur()')
                        if page.locator('#resources').get_attribute('aria-pressed')=='true':page.locator('#resources').click()
                        caption=page.locator('#paper .label[data-edge-id="read-capacity"]');place=page.locator('.node[data-id="capacity"]')
                        assert not caption.is_visible() and not place.is_visible()
                        count=page.locator('#paper').get_attribute('data-layout-runs')
                        page.locator('#resources').click();assert caption.is_visible() and place.is_visible()
                        page.locator('#fit').click();caption.click();page.locator('#tab-evidence').click();expect(page.locator('#detail')).to_contain_text('read-capacity')
                        page.locator('#resources').click();assert not caption.is_visible() and not place.is_visible()
                        assert page.locator('#paper').get_attribute('data-layout-runs')==count
                    report['cases'].append('resource_button_node_arc_caption_in_both_modes')
                # Real main declaration: 3 Agents, 2 mechanical steps, 6 places.
                # No model/executor is dispatched; never label this as live Agent execution.
                agents_dir=root/'agent-run';agent_catalog=make_agent_run(agents_dir)
                agent_reader=RegistryDashboard(agents_dir,catalog=agent_catalog)
                agent_frame=agent_reader.dashboard();agent_before=agent_frame['source']['verified_head_ordinal']
                agent_manifest={'schema_version':'rpnh/presentation/v1','topology_digest':topology_digest(agent_frame['net']),
                    'title':'Research and review','description':'Registered workflow: Planner → Reviewer → Writer. No model calls in this acceptance fixture.',
                    'nodes':{'planner.run':{'name':'Planner','description':'Plan the work and prepare the request.'},
                             'reviewer.run':{'name':'Reviewer','description':'Check the material and decide what to deliver.'},
                             'writer.run':{'name':'Writer','description':'Compose the final response.'}}}
                agent_presentation=root/'agent-presentation.json';save(agent_presentation,agent_manifest)
                with server([sys.executable,'-I','-u','-m','cpn.frontend.dashboard','--run',str(agents_dir),'--presentation',str(agent_presentation),'--no-open']) as url:
                    page.goto(url);ready();page.locator('#language').select_option('en')
                    assert page.locator('.node').evaluate_all('(ns)=>ns.map(n=>n.dataset.id).sort()')==['planner.run','reviewer.run','writer.run']
                    assert page.locator('.edge-group').count()==2
                    assert not page.locator('#resources').is_visible()
                    assert not page.locator('.run-metrics').is_visible()
                    # JointJS uses a transparent '-' inside hidden empty SVG text.
                    # Assert actual visible labels, not that internal placeholder.
                    expect(page.locator('#paper .label text:visible')).to_have_count(0)
                    expect(page.locator('.node [joint-selector="status"]:visible')).to_have_count(0)
                    assert not re.search(r'Place|Token|token|×|lookup|format',page.locator('#paper').inner_text())
                    page.locator('#fit').click();shot('agent-only-overview-english.png')
                    page.locator('.node[data-id="planner.run"]').click()
                    expect(page.locator('#detail')).to_contain_text('Reviewer')
                    expect(page.locator('#detail')).not_to_contain_text('lookup.run')
                    assert not page.locator('#tab-evidence').is_visible()
                    state=page.locator('.node').evaluate_all('(ns)=>ns.map(n=>[n.dataset.id,n.getAttribute("transform")])')
                    page.locator('#language').select_option('zh-CN')
                    expect(page.locator('#paper')).to_contain_text('智能体')
                    assert page.locator('.node').evaluate_all('(ns)=>ns.map(n=>[n.dataset.id,n.getAttribute("transform")])')==state
                    shot('agent-only-overview-chinese.png')
                    page.locator('#expand-overview').click()
                    expect(page.locator('#paper')).to_have_attribute('data-view-mode','petri')
                    assert page.locator('.node').count()==11 and page.locator('.edge-group').count()==15
                    assert page.locator('.node[data-id="lookup.run"]').is_visible()
                    page.locator('#clear-selection').click();page.locator('#fit').click();shot('agent-only-full-petri-comparison.png')
                    mode('overview');assert page.locator('.node').count()==3
                    page.locator('#search').fill('lookup');page.locator('#find-next').click()
                    expect(page.locator('#search-status')).to_have_text('0 个匹配')
                    page.locator('#search').fill('')
                    # Only Agents are searched; resource state from Petri mode is retained, not exposed.
                    mode('petri');page.locator('#resources').click();flag=page.locator('#resources').get_attribute('aria-pressed')
                    mode('overview');assert page.locator('.node').count()==3
                    assert not page.locator('#resources').is_visible()
                    mode('petri');assert page.locator('#resources').get_attribute('aria-pressed')==flag
                    mode('overview');page.set_viewport_size({'width':900,'height':850});page.locator('#fit').click();assert_canvas_size(page)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    shot('agent-only-overview-narrow.png');page.set_viewport_size({'width':1440,'height':1000})
                    assert agent_reader.dashboard()['source']['verified_head_ordinal']==agent_before
                    report['cases'].append('real_registered_agents_only_nonagents_hidden_language_search_resources_drilldown')
                # A relation can combine alternative hidden paths, without dumping
                # intermediate places or tokens into the Agent-only view.
                overview=readability_fixture();save(path,overview)
                with server(dashboard_json_server(path)) as url:
                    page.goto(url);ready();mode('overview')
                    assert page.locator('.node').count()==2
                    assert page.locator('.edge-group').count()==1
                    assert page.locator('.edge-group').first.get_attribute('data-original-count')=='4'
                    # Click the actual visible wire, not an empty label/forced event.
                    line=page.locator('.edge-group path[joint-selector="line"]')
                    point=line.evaluate('(p)=>{const a=p.getPointAtLength(p.getTotalLength()/2);const s=new DOMPoint(a.x,a.y).matrixTransform(p.getScreenCTM());return {x:s.x,y:s.y}}')
                    page.mouse.click(point['x'],point['y'])
                    expect(page.locator('#detail')).to_contain_text('下一位智能体')
                    assert page.locator('#detail [data-original-edge]').count()==0
                    page.locator('#expand-overview').click()
                    expect(page.locator('#paper')).to_have_attribute('data-view-mode','petri')
                    assert page.locator('.node').count()==6 and page.locator('.edge-group').count()==6
                    mode('overview');assert page.locator('.node').count()==2
                    report['cases'].append('agent_successor_relation_hides_intermediates_and_opens_exact_petri')
                crossing=readability_fixture(crossing=True);save(path,crossing)
                with server(dashboard_json_server(path)) as url:
                    page.goto(url);ready();mode('petri');page.locator('#resources').click()
                    page.locator('#fit').click()
                    def bridges():
                        return page.locator('.edge-group').evaluate_all('els=>els.reduce((n,e)=>n+Number(e.dataset.bridgeCount||0),0)')
                    count=bridges();assert count>0
                    paths_before=page.locator('.edge-group path[joint-selector="line"]').evaluate_all('els=>els.map(e=>e.getAttribute("d"))')
                    assert any('C' in d for d in paths_before)
                    # Real path hit testing and exact arc selection remain intact.
                    bridge_id=page.locator('.edge-group').evaluate_all('els=>els.find(e=>Number(e.dataset.bridgeCount)>0).dataset.id')
                    click_arc(page,bridge_id)
                    expect(page.locator('#detail')).to_contain_text(bridge_id)
                    page.locator('#clear-selection').click();shot('readability-wire-bridges.png')
                    positions=page.locator('.node').evaluate_all('els=>els.map(e=>[e.dataset.id,e.getAttribute("transform")])')
                    page.locator('#line-bridges').uncheck();assert bridges()==0
                    paths_off=page.locator('.edge-group path[joint-selector="line"]').evaluate_all('els=>els.map(e=>e.getAttribute("d"))')
                    assert paths_off != paths_before
                    assert sum(d.count('C') for d in paths_off) < sum(d.count('C') for d in paths_before)
                    assert page.locator('.edge-group').evaluate_all('els=>els.every(e=>Number(e.dataset.gapCount||0)===0)')
                    page.locator('#line-bridges').check();assert bridges()==count
                    assert page.locator('.node').evaluate_all('els=>els.map(e=>[e.dataset.id,e.getAttribute("transform")])')==positions
                    page.locator('#resources').click();hidden_count=bridges();assert hidden_count<=count
                    page.locator('#resources').click();assert bridges()==count
                    page.locator('#language').select_option('en')
                    assert page.locator('.edge-group path[joint-selector="line"]').evaluate_all('els=>els.map(e=>e.getAttribute("d"))')==paths_before
                    page.set_viewport_size({'width':900,'height':850});page.locator('#fit').click();assert_canvas_size(page)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    shot('readability-overview-controls-narrow.png');page.set_viewport_size({'width':1440,'height':1000})
                    assert not page.evaluate('window.__csp')
                    report['cases'].append('actual_elk_crossings_bridges_gap_toggle_visibility_localization_and_hit_targets')
                for index,path in enumerate(args.snapshot):
                    data=json.loads(path.read_text())
                    with server(json_server(path.resolve())) as url:
                        page.goto(url);ready();mode('petri');assert page.locator('.node').count()==len(data['nodes']);assert page.locator('.edge-group').count()==len(data['edges']);assert_canvas_size(page)
                        shot(f'additional-{index}.png');report['cases'].append(f'additional_v1_{index}')
                assert reader._open().event_store.max_ordinal()==head_before
                assert not report['page_errors'] and not report['outbound_requests']
                report['passed']=True
            except BaseException as exc:
                report.update(failure=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc(),health=page.locator('#health').inner_text(timeout=1000) if page.locator('#health').count() else 'No page health element', page_url=page.url)
                shot('failure.png');raise
            finally:
                (out/'browser-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
                browser.close()
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
