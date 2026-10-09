"""Summarize completed, unchanged stage gates; never execute candidates."""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path('<WORKSPACE>').resolve()
TASK = ROOT / 'task-complete-v5-20261009'
BASE = '1f191645c4d60c8b190d42e9fad99c85e8981c03'

def read(rel):
    return json.loads((TASK / rel).read_text())

def write(rel, value):
    path = TASK / rel
    assert path.resolve().is_relative_to(ROOT) and not path.is_symlink()
    if path.exists():
        assert path.stat().st_nlink == 1
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def git(repo, *args):
    return subprocess.check_output(['git', *args], cwd=repo, text=True).strip()

def source_check(stage, wt, prior, shape):
    repo = ROOT / '.v26' / wt
    old = read(prior)
    rows = old['files']
    if isinstance(rows, dict):
        rows = [dict(path=k, **v) for k, v in rows.items()]
    actual = []
    for row in rows:
        path = repo / row['path']
        assert path.resolve().is_relative_to(ROOT) and path.is_file() and not path.is_symlink(), path
        h = sha(path)
        assert h == row['sha256'], path
        if 'executable' in row:
            assert bool(path.stat().st_mode & 0o111) == row['executable'], path
        elif 'mode' in row:
            expected = int(row['mode'], 8) if isinstance(row['mode'], str) else row['mode']
            assert path.stat().st_mode & 0o777 == expected, path
        actual.append(dict(path=row['path'], sha256=h, size=path.stat().st_size,
                           executable=bool(path.stat().st_mode & 0o111)))
    assert git(repo, 'rev-parse', 'HEAD') == BASE
    target = f'stages/{stage}/source-after.json'
    write(target, dict(utc=datetime.now(timezone.utc).isoformat(), worktree=str(repo), head=BASE,
                      scope=shape, source_before=prior, files=actual, source_unchanged=True))
    return target, len(actual)

def window(label, record, junit):
    rec = read(record)
    code = rec['exit_code']
    tree = ET.parse(TASK / junit)
    assert tree.getroot().tag in ('testsuites', 'testsuite')
    cases, statuses = [], Counter()
    for c in tree.iter('testcase'):
        name = c.attrib['name']
        cls = c.get('classname', '')
        # Repository relative IDs; package-independent files keep their own test filename.
        file = c.get('file') or cls.rsplit('.', 1)[-1] + '.py'
        file = Path(file).name
        node = file + '::' + name
        outcome = 'FAIL' if c.find('failure') is not None else 'ERROR' if c.find('error') is not None else 'SKIP' if c.find('skipped') is not None else 'PASS'
        statuses[outcome] += 1
        cases.append(dict(id=node, junit_classname=cls, junit_name=name, outcome=outcome))
    assert code == 0 and cases and statuses == Counter(PASS=len(cases)), (label, code, statuses)
    return dict(label=label, command_record=record, junit=junit, exit_code=code,
                command=rec['command'], cwd=rec['cwd'], started_utc=rec.get('started_at_utc'),
                finished_utc=rec.get('finished_at_utc'), environment=rec.get('environment'),
                cases=cases, executions=len(cases), outcomes=dict(statuses))

CONFIG = {
 'S1': [('resume-S1-author','core/resume-S1-author','core/S1/resume-author'),
        ('S1-independent','core/S1-independent','core/S1/independent'),
        ('resume-S1-regression','core/resume-S1-regression','core/S1/resume-regression')],
 'H7_core': [('resume-core-author','core/resume-core-author','core/H7_core/resume-author'),
             ('resume-core-independent','core/resume-core-independent','core/H7_core/resume-independent')],
 'H7_history': [('resume-history-author','core/resume-history-author','core/H7_history/resume-author'),
                ('resume-history-independent','core/resume-history-independent','core/H7_history/review-resume-exact/review')],
 'H7_lowering': [(f'resume-lower-{n}',f'core/resume-lower-{n}',f'core/H7_lowering/resume-{x}')
                  for n,x in [('new','new'),('compiler','compiler'),('core','core'),('independent','independent')]] +
                 [('resume-lower-restored-six','core/resume-lower-restored-six','core/H7_lowering/restored-six')],
 'R1_v2_full': [('resume-r1-author','rsi/resume-r1-author','rsi/resume-r1-author'),
                ('resume-r1-independent-registry','rsi/resume-r1-independent-registry','rsi/resume-r1-independent-registry')],
 'R2': [(x,'rsi/'+x,'rsi/'+x) for x in ['resume-r2-r1-compiler','resume-r2-author-step','resume-r2-independent','resume-r2-native']],
 'Codex_reader': [('reader-offline','codex/reader-offline','codex/reader-offline')],
 'Codex_effort': [(x,'codex/'+x,'codex/'+x) for x in ['effort-offline','effort-review','effort-socket','resume-effort-bridge']],
 'Codex_history': [(x,'codex/'+x,'codex/'+x) for x in ['resume-history-offline','resume-history-registry']],
 'Codex_0161': [],
 'DSH': [(x,'dsh/'+x,'dsh/'+x) for x in ['10-python-codec','11-launcher-source','resume-owner-socket']],
 'OpenCode': [(x,'opencode/'+x,'opencode/'+x) for x in ['11-g1-worktree','13-independent-worktree']],
}

def main():
    assert read('stages/core/resume-queue-state.json')['status'] == 'COMMANDS_FINISHED_REQUIRES_ACCEPTANCE'
    assert read('stages/client-resume-state.json')['status'] == 'COMMANDS_FINISHED_REQUIRES_ACCEPTANCE'
    assert (TASK / 'RESOURCE_MONITOR_STOP').exists(), 'Stop monitor before freezing results'
    after = {}
    for n, stage in enumerate(('S1','H7_core','H7_history','H7_lowering'), 1):
        after[stage] = source_check('core/'+stage, 'c'+str(n), f'stages/core/{stage}/source-before.json',
                                    'all original tracked baseline and declared candidate files')[0]
    for lane, wt in [('dsh','d'),('opencode','o')]:
        after[lane] = source_check(lane, wt, f'stages/{lane}/source-post-apply.json',
                                  'full received stage source inventory, excluding runtime caches')[0]
    # Reader retained its complete original identity; client queue supplies explicit before/after inventories.
    after['Codex_reader'] = source_check('codex', 'x1', 'stages/codex/reader-after.identity.json',
                                       'full original reader inventory')[0]
    after['Codex_effort'] = 'stages/codex/resume-effort-bridge-source-after.json'
    after['Codex_history'] = 'stages/codex/resume-history-registry-source-after.json'
    rs = read('stages/rsi/lane.json')
    for key, wt in [('R1_v2_full','r1'),('R2','r2')]:
        repo = ROOT/'.v26'/wt
        assert git(repo,'diff','--name-only') == ''
        after[key] = f'stages/rsi/resume-{wt}-final.identity.json'
        for row in read(after[key])['changed_files']:
            path=repo/row['path']
            assert path.resolve().is_relative_to(ROOT) and not path.is_symlink()
            assert sha(path)==row['sha256'],path

    manifest = read('rpnh-complete-local-bundle-v5/templates/result-manifest.template.json')
    manifest.update(run_id='rpnh-v5-local-20261009T011312Z-resumed',
                    utc_start=read('receipt.json')['received_at_utc'], utc_end=datetime.now(timezone.utc).isoformat(),
                    overall_status='PARTIAL_STAGE_VALIDATION_COMPLETE', evidence_only=True)
    receipt = read('receipt.json')
    source_zip = Path('<INPUT_DIRECTORY>/RPNH_Complete_Local_Bundle_v5_Transfer_20261009.zip')
    manifest['bundle'].update(filename=source_zip.name, size_bytes=source_zip.stat().st_size,
                              sha256=sha(source_zip), static_verifier_exit=0,
                              static_verifier_record='logs/bundle-integrity.json')
    repo = ROOT/'RPNH-main'
    assert git(repo,'status','--porcelain') == ''
    assert git(repo,'rev-parse','HEAD') == BASE
    manifest['checkout'].update(head=BASE, branch=git(repo,'branch','--show-current'),
                                local_origin_main=git(repo,'rev-parse','origin/main'), dirty_inventory=[],
                                note='Local d92ff37 fast-forwarded to exact 1f19164 after reviewing three documentation/evidence changes; no candidate applied to main.')
    manifest['environment'] = read('LOCAL_ENVIRONMENT.json')
    manifest['environment']['native_capabilities'] = {
        'AF_UNIX':'PASS in S1, R2 and eligible adapter fixtures; see actual per-stage verdicts',
        'stock_codex_0155_0161':'NOT_RUN; no existing exact trusted binary identified; installed metadata 0.160 is not a pin substitute',
        'stock_opencode':'NOT_RUN; no exact trusted stock Linux ELF identified',
        'DSH_node24_pnpm':'NOT_RUN; installed Node22/20 and workspace22.18 do not supply original Node24/pnpm gate',
        'historical_A1':'Not retested; this task does not clear earlier UID/namespace blockage'}
    accepted, stats = [], {}
    for stage in manifest['stages']:
        key = stage['id']
        windows = [window(label,'stages/'+record+'.json','stages/'+junit+'.xml')
                   for label,record,junit in CONFIG[key]]
        ids = [case['id'] for w in windows for case in w['cases']]
        duplicates = sorted(k for k,n in Counter(ids).items() if n>1)
        stats[key] = dict(executions=len(ids), distinct=len(set(ids)))
        stage.update(status='PASS', reason='All listed finite windows passed; no broader certification inferred',
                     input_identity={'baseline_main':BASE,'selected_patch_sha256':stage['patch_sha256']},
                     source_after_identity=after.get(key,after.get(stage['lane'])),
                     commands=[{k:v for k,v in w.items() if k!='cases'} for w in windows], test_ids=sorted(set(ids)), overlap_test_ids=duplicates,
                     windows=windows, test_counts=stats[key], scope='exact frozen stage; not combined product')
        stage['source_before_identity'] = (f"stages/core/{key}/source-before.json" if stage['lane']=='core' else {
            'R1_v2_full':'stages/rsi/resume-r1-author-before.identity.json',
            'R2':'stages/rsi/resume-r2-r1-compiler-before.identity.json',
            'Codex_reader':'stages/codex/reader-before.identity.json',
            'Codex_effort':'stages/codex/effort-offline-before.identity.json',
            'Codex_history':'stages/codex/resume-history-offline-source-before.json',
            'Codex_0161':'stages/codex/x3-base-lock.json',
            'DSH':'stages/dsh/source-post-apply.json',
            'OpenCode':'stages/opencode/source-post-apply.json'}[key])
        stage['counts'] = dict(dispatch=None,popen=None,worker=None,child=None,model=0,
                               note='No aggregate invented for pure/test-double/native windows; explicit per-scene counts and refs retained in native evidence')
        stage['evidence_files'] = sorted(set([w['command_record'] for w in windows]+[w['junit'] for w in windows]+([stage['source_after_identity']] if stage['source_after_identity'] else [])))
        accepted.extend(w['junit'] for w in windows)
        if key=='S1':
            stage.update(status='FAIL',reason='Offline100 distinct/112 executions pass; final native a004 nine pass/one g7 failure. Same-world no-replacement control passes. Dynamic adoption precedes terminal projection, whose exact producing-firing provenance remains on old net. Not attributed to S1 regression without baseline comparison.',
                         native_result='stages/core/S1/native/result.json',
                         causal_control='stages/core/S1/native/g7-control-comparison.json',
                         offline_result='stages/core/S1/OFFLINE_RESULT.json')
            stage['deselected_test_ids'] = ['Original author71 includes unchanged fresh-Python cold Registry read; not zero-subprocess D0','regression -k excludes two native execution nodes; no broad suite claim']
        elif key.startswith('H7_'):
            stage.update(native_status='UNIMPLEMENTED',reason='PASS only for listed existing D0 slice; full public/native H7 path unimplemented')
            if key=='H7_history':
                stage['source_scope_split']='247 on full c3 with matching frozen994 projection; 51 on original strict994 curated snapshot and unchanged source guard'
            stage['deselected_test_ids'] = [a for w in windows for a in w['command'] if str(a).startswith('--deselect=')]
        elif key=='R1_v2_full':
            stage.update(status='PARTIAL_PACKAGE_GAP',reason='Author/compiler150 and independent Registry3 PASS. Independent compiler33 NOT_RUN: frozen successor imports absent test_inert_profile_independent; original collection error retained; no helper fabricated.', missing_gate_status='NOT_RUN',
                         completed_windows_status='PASS',missing_gate='stages/rsi/resume-r1-independent-compiler.xml')
        elif key=='R2':
            stage.update(native_status='PASS_FINITE_4',reason='R1 compatibility150, step11, independent5 and original OwnerEventLoop AF_UNIX4 PASS; R1 package gap not erased',
                         native_readback='stages/rsi/lane.json')
        elif key=='Codex_0161':
            stage.update(status='BLOCKED',reason='Frozen1041 prerequisite:1039 match, only ARCHITECTURE.md and ARCHITECTURE_ZH.md mismatch current H2a reader additions. No apply/downstream gate. Exact unchanged package static verifier passes after temporary Git discovery isolation; original failures retained.',
                         source_after_identity=None,stock_status='NOT_RUN',lock='stages/codex/x3-base-lock.json')
        elif key.startswith('Codex'):
            stage['stock_status']='NOT_RUN; no existing trusted exact0.155/0.161 binary, no download/default upgrade'
            stage['deselected_test_ids'] = ['effort original42 excludes socket and bridge; both now separately passed' if key=='Codex_effort' else 'history119 excludes absent-long-root socket; stock runtime not covered' if key=='Codex_history' else 'none']
        elif key=='DSH':
            stage.update(reason='Python codec39, launcher9 and owner/AF_UNIX fixture34 PASS; two detached factory revision-label probes separate',
                         node_status='NOT_RUN:Node24/pnpm/exact upstream unprepared',real_session_lifecycle='NOT_RUN')
            stage['counts'].update(model=None,real_model_calls=0,
                note='Synthetic registered provider/model events occur in fixtures; no aggregate logical model count measured. Real provider/model calls0.')
        elif key=='OpenCode':
            stage.update(reason='G1 274 and independent33 PASS; repeated G1 package window adds no distinct coverage',
                         stock_G2_G3='NOT_RUN; no exact trusted stock executable',
                         simulated_failure_slot='Initial standalone slot INCONCLUSIVE(empty/missing exit); passing G1 includes negative gate-support checks, not a substitute standalone acceptance')
        if key in ('R1_v2_full','R2'):
            stage['identity_scope']='Changed candidate6/11 files have before/after exact SHA+mode; tracked baseline has no git diff and exact HEAD1f19164. No claim of an independently captured full pre-test inventory for this RSI lane.'
    expected={'S1':(112,100),'H7_core':(139,139),'H7_history':(298,298),'H7_lowering':(774,774),
              'R1_v2_full':(153,153),'R2':(170,170),'Codex_reader':(32,32),'Codex_effort':(48,48),
              'Codex_history':(162,162),'Codex_0161':(0,0),'DSH':(82,82),'OpenCode':(307,307)}
    for k,(executions,distinct) in expected.items():
        assert stats[k] == dict(executions=executions,distinct=distinct), (k,stats[k])
    native=read('stages/core/S1/native/result.json')
    assert native['case_status_counts'] == {'PASS':9,'FAIL':1,'NOT_RUN':0},native['case_status_counts']
    accepted += [str(p.relative_to(TASK)) for p in sorted((TASK/'stages/core/S1/native/a004').glob('*.xml'))]
    accepted += ['stages/core/S1/native/control001/control.xml']
    for p in accepted: ET.parse(TASK/p)
    manifest['combined'].update(status='NOT_RUN',reason='Taskbook assigns stage-first results; no final all-lane combination gate or product merge')
    manifest.update(privacy_checked=True,retained_failures='Original received failures, interrupted/NUL/empty records, collection errors and native a001-a004 retained; public prefix transforms recorded, full originals local',
                    historical_scope='Earlier A1 blockage and benchmark scores unchanged',
                    test_counts_rule='Do not sum distinct counts across different stage versions. RSI union separately173 distinct/323 executions; S1 native10 and separate control1 do not enlarge offline100.')
    samples=[json.loads(s) for s in (TASK/'RESOURCE_SAMPLES.jsonl').read_text().splitlines() if s.strip()]
    resource={'minimum_D_available_bytes':min(s['D_available_bytes'] for s in samples),
              'minimum_memory_available_bytes':min(s['memory_available_bytes'] for s in samples),
              'peak_task_runtime_bytes':max(s['task_and_runtime_bytes'] for s in samples),
              'all_samples_within_budget':all(s['within_budget'] for s in samples),
              'reserve_D_bytes':25*2**30,'ceiling_task_runtime_bytes':8*2**30,'reserve_memory_bytes':4*2**30,
              'max_heavy_test_jobs':2,'native_runtimes':'Byte-verified compressed originals local only; removed expanded task-owned duplicates',
              'note':'ext4 free space is not host D: free space; no WSL VHDX cleanup/compaction claim'}
    manifest['resources']=resource
    manifest['publication_budget_bytes']=128*2**20
    write('RESOURCE_FINAL.json',resource)
    write('result-manifest.json',manifest)
    lines=['# RPNH V5 本地分阶段验证结果','',
       '当前任务中可运行的阶段验证已完成，整体为 **PARTIAL**。本次只发布分阶段结果、冻结候选补丁和经过内容复核的证据；产品代码未合并这些候选。完整 H7 原生链仍未实现，最终跨 lane 组合未运行。','',
       '## 实测结果','',
       '| 阶段 | 不同用例 / 执行次数 | 结论与范围 |','|---|---:|---|',
       '| S1 | 离线100 /112；原生10；另对照1 | 离线全通过。最终原生9通过、g7失败；无替换对照通过。 |',
       '| H7 core |139 /139 |现有 D0 首片通过；不代表完整 H7。|',
       '| H7 history |298 /298 |247在完整工作树、51在原严格994文件快照；两种范围明确分开。|',
       '| H7 lowering |774 /774 |原768（新声明28、编译器516、核心177、独立47）通过；另补齐原缺示例导致无法收集的精确六项。|',
       '| R1 v2 |153 /153 |作者/编译器150、独立Registry3通过；独立compiler33因包缺helper未运行。|',
       '| R2 |170 /170 |兼容150、step11、独立5、真实OwnerEventLoop/AF_UNIX4通过；RSI跨两阶段去重173、执行323。|',
       '| Codex reader |32 /32 |原冻结读取用例通过。|',
       '| Codex effort |48 /48 |原42、独立4、本地socket1、未改bridge1通过；不是stock认证。|',
       '| Codex history |162 /162 |原119及原精确Registry/MainSession43通过。|',
       '| Codex 0.161 |0 /0 |前置1041锁仅两份ARCHITECTURE文档不匹配，应用及后续门禁BLOCKED。|',
       '| DSH |82 /82 |codec39、launcher9、owner34通过；另两项factory探针。Node24/pnpm及真正上游Session门未运行。|',
       '| OpenCode |307 /307 |G1 274、独立33通过；重复窗口不增覆盖。G2/G3 stock门未运行。|','',
       '每个版本分别计数，不把各行相加为产品组合覆盖。完整原命令、UTC、cwd、环境、真实exit、JUnit用例ID、重叠与明确排除范围见 [结构化结果](result-manifest.json)。','',
       '## 必须保留的失败与阻断','',
       'S1 g7 在合法HOST成功和待处理动态替换交界失败：RunOwner.succeed先采用新网络，终态产物仍绑定旧网络的精确producing firing，随后Harness终态校验抛出 `ResourceIntegrityFault: terminal product differs from exact published producing firing`。同一冻结夹具不做替换可正常terminal，排除了这个例子的通用夹具/terminal不兼容；没有基线回归对照，不能归因于S1补丁本身。原生g7仍为FAIL，不能用事后只读分析或对照PASS替代。','',
       '[最终原生结果](stages/core/S1/native/result.json)、[原始g7失败JUnit](stages/core/S1/native/a004/g7.xml)、[直接provenance分析](stages/core/S1/native/g7-direct-analysis.json)、[唯一因果对照](stages/core/S1/native/g7-control-comparison.json) 保留exact refs和真实AF_UNIX/OwnerEventLoop条件。HOST Future在owner进程内受控完成，不宣称并行HOST线程或外部业务worker。','',
       'Codex 0.161锁的1039文件匹配，只有 `docs/ARCHITECTURE.md` 与 `docs/ARCHITECTURE_ZH.md` 因当前H2a reader说明不匹配。原1041/1044清单、补丁和旧文档未改，未强行应用。包静态verifier的首次失败另因临时目录向上发现工作区metadata Git，git apply跳过了六条路径；限定临时Git发现边界后，原verifier及所有原hash检查通过。这不解除checkout锁或stock门。见 [Codex复核](local/reviews/codex-recovery-audit.md)。','',
       'R1所选v2包的独立compiler测试导入缺失的 `test_inert_profile_independent`。保留实际collection错误，33项标NOT_RUN，没有从另一lane/旧包拼helper。DSH没有原Node24/pnpm环境；Codex没有可核实的现存0.155/0.161精确stock二进制（0.160元数据不替代），OpenCode没有可核实的精确stock Linux ELF。没有下载、安装、登录、默认版本升级或真实模型调用。','',
       '第一次磁盘中断留下的点号、NUL尾、空JUnit及缺exit记录均保持INCONCLUSIVE；重跑使用新名称，旧记录不覆盖。S1早期探针的输出端口/Authority比较错误及一次collection缩进错误另列为探针问题，最终a004仍保留真实g7失败。OpenCode首次补丁白名单解析错误已有严格unified格式校验纠正，旧诊断保留；其单独simulated-failure槽仍不宣称完成。早先A1权限阻断和业务benchmark分数未重写。','',
       '## 输入、身份与资源','',
       f'输入 `{source_zip.name}`：{source_zip.stat().st_size} bytes，SHA256 `{sha(source_zip)}`。外层ZIP、tar、48成员、13原ZIP与候选索引经原静态verifier核验；该PASS仅为包完整性。精确完整基线为 `{BASE}`，产品基线d92ff37；先审查远端三项文档/旧证据更新再快进，没有reset。','',
       '十二候选均核对原patch身份；其中十一项通过old blobs、apply --check与postimages后隔离应用，Codex 0.161因前置锁不符未应用；core四个完整工作树的冻结979/992/994/999投影匹配，全量before/after清单仍另列。只读history的51项保留原994锁，不混称全部298在完整树运行。Codex effort独立x2只含effort；x3为reader+effort+history。所有适用完整源码与原清单检查通过，生成pyc缓存另列，不计为源码。','',
       f'恢复期资源采样：D盘最低可用{resource["minimum_D_available_bytes"]/2**30:.2f} GiB、可用内存最低{resource["minimum_memory_available_bytes"]/2**30:.2f} GiB、任务+运行目录峰值{resource["peak_task_runtime_bytes"]/2**30:.2f} GiB。最多两路重测试、D盘25 GiB保留、内存4 GiB保留、任务8 GiB上限，全部采样在预算内。已结束且不含特殊文件的本任务runtime先压缩、逐文件/符号链接校验，再移除展开副本；完整原件只留本地归档，未碰历史任务、外部worktree或WSL虚拟磁盘。ext4释放不冒充等量返还D盘。','',
       '当前受管环境的euid/祖先UID以 [实际记录](local/LOCAL_ENVIRONMENT.json) 为准；不把它泛化为普通WSL UID1000。Python为已有3.13.12环境，pytest8.4.2/jsonschema4.26.0；Node现存22/20。所有新真实provider/model、收费调用、Actions、下载安装均0。','',
       '## 交付边界','',
       '本次GitHub新增内容仅为该证据目录。十二冻结根补丁作为候选证据，历史失败patch另标记，未合入产品。公开文本只作记录在案的私有路径前缀替换；原件/包含件SHA、大小、字节和行范围见publication-manifest。空文件、畸形XML/JSON和NUL原始记录保留，并有明确非替代的escaped视图。实际DB、私有profile、凭据、binary、venv/cache及压缩runtime不上传；无新返回ZIP。','',
       'H7 public inventory/native issuer/peer/receipt/reservation/wrappers/child completion为UNIMPLEMENTED，N01–N36是未来验证合同；不借此次D0通过写成已完成。最终组合需独立定义选入版本、新清单和门禁，本次NOT_RUN。','']
    p=TASK/'RESULTS_ZH.md'; assert p.resolve().is_relative_to(ROOT);p.write_text('\n'.join(lines))
    write('FINAL_GATE_SUMMARY.json',dict(final=True,publication_approved=True,privacy_review_complete=True,evidence_only=True,
                results_summary='RESULTS_ZH.md',result_manifest='result-manifest.json',
                final_control_json=['result-manifest.json','RESOURCE_FINAL.json'],final_accepted_junit=accepted))
    print(json.dumps(dict(status='RESULTS_WRITTEN_REQUIRE_REVIEW',stages=stats,accepted_junits=len(accepted))))

if __name__ == '__main__':
    main()
