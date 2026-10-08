"""Assemble finite H1 validation results from completed native command records."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat
import xml.etree.ElementTree as ET

ROOT = Path('<WORKSPACE>')
TASK = ROOT / 'task-owner-entry-validation-20261008'
PACKET = TASK / 'RPNH_Owner_Entry_Local_Validation_20261008'

def read(name):
    return json.loads((TASK/name).read_text())

def main():
    for label in ['preflight','native-focused','native-agent-task','native-cli','native-readback',
                  'readback-before','readback-after','cli-readback-audit','agent-task-ledgers']:
        assert read('logs/'+label+'.json')['exit_code'] == 0, label
    windows = []
    ids = []
    for name, count in [('native-focused',36),('native-agent-task',3)]:
        parsed = ET.parse(TASK/'logs'/ (name+'.xml'))
        suite = parsed.find('testsuite')
        assert int(suite.attrib['tests']) == count
        assert all(int(suite.attrib[k]) == 0 for k in ['failures','errors','skipped'])
        tests = [case.attrib['classname'].replace('.','/')+'.py::'+case.attrib['name']
                 for case in parsed.iter('testcase')]
        assert len(tests) == count
        ids.extend(tests)
        windows.append({'id':name,'status':'PASS','tests':count,'time_seconds':float(suite.attrib['time']),
                        'junit':'logs/'+name+'.xml','test_ids':tests})
    expected = json.loads((PACKET/'review/repo-relative-test-inventory.json').read_text())
    expected_ids = set(expected['final_focused_product_suite']) | set(expected['additional_existing_product_tests'])
    assert len(ids) == len(set(ids)) == 39
    assert set(ids) == expected_ids
    manifest = json.loads((PACKET/'file-manifest.json').read_text())
    for row in manifest:
        for repo in [ROOT/'.o26/s', ROOT/'RPNH-main']:
            assert hashlib.sha256((repo/row['path']).read_bytes()).hexdigest() == row['final_sha256']
    sockets = [str(p) for name in ['f','a','c'] for p in (ROOT/'.o26'/name).rglob('*')
               if stat.S_ISSOCK(p.lstat().st_mode)]
    assert not sockets, sockets
    ledgers = read('logs/agent-task-ledgers.stdout.log')
    assert [r['scripted_logical_ledger'] for r in ledgers['runs']] == [[2,0],[3,0],[3,0]]
    inventory = {'status':'PASS_NATIVE_FINITE','unique_count':39,'executed_count':39,'test_ids':sorted(ids),
        'windows':windows,'cloud_expected_ids_match':True,'native_pipeline_model_counts':[0,0],
        'post_classic_test_scripted_ledgers':[[2,0],[3,0],[3,0]],'real_provider_requests':0,
        'no_pipe_options_or_plugin':True,'pytest_addopts_and_plugins_not_set':True,
        'residual_native_sockets':sockets,'qualification':'Includes pure tests;39is not39socket integrations. '
            'Post-testclassic ledgers include each test explicitresume. No extra/repeated tests added.'}
    (TASK/'test-inventory.json').write_text(json.dumps(inventory,indent=2)+'\n')
    layered = {'schema_version':'rpnh/owner_entry_local_native_validation/v1','status':'PASS_NATIVE_FINITE',
        'validated_at_utc':datetime.now(timezone.utc).isoformat(),
        'tested_base':'674252feb836f631c162979f177d1fe91f22559f',
        'delivery_parent':'ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4',
        'patch_sha256':'71261d059d205b375334450aef281702a1c573562c6e905a08d411b907749a6c',
        'frozen_seven_source_files_match':True,'product_repairs':[],
        'layers':[
            {'id':'package_source_apply','status':'PASS','evidence':['package-verification.json','source-verification.json','logs/preflight.json']},
            {'id':'cloud_native_attempt','status':'BLOCKED_ENV','scope':'HistoricalAF_UNIXEPERM retained;not localgate','evidence':['cloud-handoff/native-attempt.log']},
            {'id':'cloud_pipe_semantics','status':'PASS','scope':'Historical39unique/50executions acrosssuccessfulbatches;not native'},
            {'id':'local_default_focused','status':'PASS','tests':36,'evidence':['logs/native-focused.xml']},
            {'id':'local_classic_agent_task','status':'PASS','tests':3,'evidence':['logs/native-agent-task.xml','logs/agent-task-ledgers.stdout.log']},
            {'id':'local_cli_and_process_readback','status':'PASS','evidence':['native-cli-readback-audit.json']},
            {'id':'real_provider_docker_actions_business','status':'NOT_RUN','scope':'IntentionallyoutsideassignedH1scope'}],
        'scope_limits':['Not fullrepository','NoH2a/otheroptimizationsteps','OldA PARTIAL_ENV retainedwithoutretest',
                        'Focusedearlystop usescallbackinjection;classic3 useactualOSSIGINT;OSrestoredhandleridentity notseparatelyprobed'],
        'delivery_authority':'Standing owner authorization permits commit/push after accepted validation; no extraZIP requested.'}
    (TASK/'LAYERED_MANIFEST.json').write_text(json.dumps(layered,indent=2)+'\n')
    report = f'''# H1 owner 执行入口原生补验

结论：**PASS_NATIVE_FINITE**。默认原生 focused36项与经典AgentTask中断/恢复3项全部通过，共39个不同repo-relative node IDs、39次执行，无失败/错误/跳过。没有为本地验收修改冻结七文件；没有新增测试或重跑来叠加覆盖。focused窗口包含纯单元检查，不能将39项都称为socket集成。

输入包为 RPNH_Owner_Entry_Local_Validation_20261008，实际验证日期2026-10-09（Asia/Singapore）。外层ZIP SHA、CRC与62项校验见package-verification；补丁SHA为71261d059d205b375334450aef281702a1c573562c6e905a08d411b907749a6c。verify_patch在工作区专用TMPDIR临时应用并核对6个旧文件、新测试路径缺省及7个最终文件，目标checkout不变。随后在精确674252feb836f631c162979f177d1fe91f22559f独立工作树真实应用并验证；所有最终SHA/Git blob来自原manifest。

主线初始674252f、工作树干净；live origin/main为ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4，仅五个证据文件前进。快进保留这些证据修订，主仓库接入七文件后与实测工作树逐字节一致。没有混入H2a或其他优化候选。完整来源见source-verification与integration-verification，原包云端948-blob核验与最终审阅哈希保持原边界，不冒充本轮全仓测试。

| 本地窗口 | 实际结果 |
| --- | --- |
| 默认原生focused | 36 PASS，{windows[0]['time_seconds']:.3f}秒 |
| 三项经典AgentTask中断/恢复 | 3 PASS，{windows[1]['time_seconds']:.3f}秒 |
| native pipeline CLI | exit0，2.000kWh / 1.70CNY，[0,0]模型计数 |
| 独立进程只读回读与审计 | exit0，全部11个稳定顶层字段一致；ordinal1001、dispatch10前后不变 |
| 真实provider/model、Docker、Actions、业务benchmark | NOT_RUN，任务不含这些调用 |

使用现有Python3.13.12/pytest8.4.2环境，依赖版本在pyproject范围内；core实际从.o26/s导入。真实AF_UNIX bind/listen/socketpair初检、OwnerEventLoop listener/wake路径通过。命令没有pipe选项，没有agent_task_pipe_checks或其他transport替代；PYTEST_ADDOPTS/PYTEST_PLUGINS未设置。每个实际命令、解释器、环境覆盖、stdout/stderr、UTC起止时间、退出码和JUnit原件在logs；全部生成物、临时文件及原始Registry留在工作区内、产品repo外。

生产变化只有三个Python文件：既有Orchestrator增加request_owner_stop转发；AgentTask的active/early stop走该公共表面；tool_pipeline正常run改走同一factory/Orchestrator.run。make_harness返回既有executor保持旧返回形状。Registry、PN、Harness、OwnerEventLoop、业务Module、工具/HOST ABI、dispatcher与adapter边界未改；没有新增scheduler、queue、owner、close方法或第二套authority。双语exampleREADME同步。

同一Module的direct Harness与Orchestrator在1/2 worker上比较完整业务值、资源因果角色、每firing事件类型序与终态；不要求并行分支的全局交错固定。受控并行、join/lineage、拒绝、未决不重放与终态回读均通过。stop测试确认两条已admitted read排空、无新后继、没有终态、资源仍可由host使用；launcher的真实loop与pool封装各关闭一次。提前stop回调测试确认零provider dispatch、port/loop各close一次、兼容factory形状不变。

三项经典测试通过实际os.kill发出OSSIGINT，确认当前firing工作区丢弃、此前settled.txt保留、discarded/partial文件不进入最终发布，以及交换graph后仍使用持久化planner/writer profile。各测试还显式resume，最终只读账本分别[2,0]、[3,0]、[3,0]（逻辑settled calls/超限）；共8次scripted逻辑调用，真实provider请求0。pipeline与early-stop为[0,0]。不把逻辑账本写成真实模型使用。

信号恢复的精确范围：AgentTask finally恢复原handler的控制流未改；focused提前回调测试在mock signal.signal下验证恢复调用和previous identity。经典三项原生用例不额外断言OS中最终handler identity，本轮未补做重复探针，不据此宣称通用信号/所有异常资源路径全面恢复。

真实CLI有一个complete terminal，指向精确final、publish_report firing与final checkpoint；10个published firing、12个业务产物、2个原始输入。原进程结束后另进程readback仅从持久Registry重建，没有复用Python缓存对象或新增dispatch。报告/全部source-candidate-validation refs、lineage、checkpoint、firings/events/projection深度一致；仅运行时stop_reason/transport字段缺省。只读前后ordinal/event count1001、dispatch/execution start10、模型[0,0]不变。独立审阅与审计结果位于review与native-cli-readback-audit。

云端PermissionError、初始路径/collection错误、unhashable fake-port fixture失败及后续修正通过全部按原件保留，cloud pipe语义39 unique/50 executions不重标为本次native结果。此前共享harness A的PARTIAL_ENV保持历史结论，本轮不重测或归因到H1。无新产品修复，无模型/API/业务评分/安全配置改动，无ZIP交付要求。代码和reviewed证据依用户长期授权一起提交、推送；最终GitHub SHA/远端核验收据另存publication/PUSH_RESULT.json。

公开材料只含源码/补丁、原始日志/JUnit及必要synthetic JSON导出；数据库/WAL/SHM、private profiles、完整provider请求/对话、缓存与环境不上传。本地公开副本仅必要工作区前缀替换，XML若涉及替换使用实体；原件/公开摘要及每项转换列在发布MANIFEST，原件保持冻结。
'''
    (TASK/'REPORT_ZH.md').write_text(report)
    print(json.dumps({'status':'PASS_NATIVE_FINITE','unique_tests':39,'test_executions':39,
                      'pipeline_model_counts':[0,0],'classic_scripted_calls':[2,3,3],
                      'real_provider_requests':0,'residual_sockets':0}))

if __name__ == '__main__':
    main()
