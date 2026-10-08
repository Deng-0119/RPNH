"""Render the final finite validation report from accepted command records."""
import json
from pathlib import Path

ROOT = Path('<WORKSPACE>')
TASK = ROOT / 'task-h2a-validation-20261008'


def main():
    result = json.loads((TASK / 'LAYERED_MANIFEST.json').read_text())
    integration = json.loads((TASK / 'integration-verification.json').read_text())
    assert result['status'] == 'PASS_NATIVE_FINITE' and integration['status'] == 'PASS'
    table = '\n'.join('| ' + w['id'] + ' | ' + str(w['tests']) + ' PASS | '
                      + f"{w['time_seconds']:.3f}" + ' |' for w in result['windows'])
    report = f'''# H2a TaskControl 读取收敛与 H1 组合补验

结论：**PASS_NATIVE_FINITE**。H2a 冻结五文件接入当前 H1 主线，无额外产品修复。组合源码上八个测试窗口全部通过：166 个不同用例、166 次执行，零失败、错误、跳过。其中仓库内 154 个、包附独立检查 12 个；H1 为 39 个、H2a 为 130 个，双方共享的三项经典 AgentTask 测试只执行一次。纯单元测试不计为 socket 集成。

| 组合源码测试窗口 | 实际结果 | JUnit 秒数 |
| --- | --- | --- |
{table}

原始 H2a 基点是 ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4；实际测试基点是已合入 H1 的 715468dab0b1bea07d7e94a7aa0606eaf194365c，HEAD tree 为 {result['head_git_tree']}。实测工作树清单 SHA256 为 {result['tested_worktree_manifest_sha256']}，覆盖 {result['tested_manifest_entries']} 个 tracked/候选文件条目，前后完整清单、dirty 状态及源码字节相同。这是实际工作树清单摘要，不是 Git tree，也不冒充包中原始 1,335 项历史源码身份。五个 H2a 文件和七个 H1 文件分别按各自冻结 manifest 核验。

来源 ZIP SHA256 为 0de920a4371ec83c6425607d90cfb2a035326d0e8da77f3e90bb18a133147fac；64 成员 CRC、63 项 SHA 校验、10 份原包 JUnit 解析及临时补丁应用通过。补丁 SHA256 为 62f914d41d953e9badd4eb06eec43273f9d52bd5bd9b948ad6e10137d3ed2b24。应用前四旧文件与新文件不存在状态通过验证；git apply --check、真实 apply、diff --check、应用后五文件 SHA/Git blob 通过。基点前进包含 H1 七文件及其证据，没有与 H2a 五路径重叠。主仓库接入后的 {integration['compared_entries']} 个实测文件逐字节/类型/执行位一致，之后新增的交付证据不改变运行源码。

生产变化限于既有 TaskControl.result()、_read_terminal_status() 使用 Registry 原生共享读取，以及默认 descriptor 物理读取采用注册大小。result 使用共享 terminal bytes，JSON decode 和累计计数完成后重查同一 cut；status 历史 canonical evidence/index 计数限定在该 cut。旧 keys、task-ID 字符串、原始 outcome、解码输出及累计计数语义保持，当前 running/stopped generation 不读取旧 terminal。没有新增 CLI、状态镜像、Registry、owner 或执行策略；status 的进程、Registry、socket 仍是独立观察，不承诺跨通道原子快照。

合法大于 4 MiB 的 body 与 native-run descriptor 可读；默认按注册大小约束 backing read，显式 descriptor budget 保持优先，膨胀或缩短文件错误退出，不截断伪成功。没有固定 4 MiB cap。此结论不延伸为通用对抗任意同长度篡改的安全承诺。合成 imported-accounting 非零历史计数与 (13,2) spy 分别检查累计语义和委派顺序，不代表真实模型消耗。

使用现有 Python 3.13.12 / pytest 8.4.2 环境，未安装依赖；实际 core 从 .t26/s 导入。正常 AF_UNIX bind/listen/socketpair 初检、指定 TaskControl/status 和两项真实 OwnerEventLoop callback 路径通过，没有 pipe 插件或 transport fallback。三个经典用例通过实际 OS SIGINT 后显式 resume，验证持久 profile、当前 firing 丢弃、先前完成工作保留；最终 scripted 逻辑账本分别 [2,0]、[3,0]、[3,0]，真实 provider 请求 0。H1 focused 的 stop 排空、无新后继、host loop/pool 生命周期和早停恢复调用保持通过；最终 OS handler identity 未额外探针，不扩大恢复覆盖声明。

组合源码 CLI 原生执行结果为 2.000 kWh / 1.70 CNY、模型计数 [0,0]，一个 complete terminal、10 firings、12 业务产物、2 source。原进程退出后独立进程 readback 的全部 11 个稳定顶层字段深度一致；仅运行期 stop_reason/transport 不在回读输出。只读前后 ordinal/event count 1001、dispatch/execution start 10、模型计数 [0,0] 一致。完整输出、事件因果/lineage 审计见 native-cli-readback-audit.json，原始 Registry 只留本地。

云端历史失败保持原分类：native-status 为 6 PASS / 1 AF_UNIX EPERM；native-resume 在 socket 构造阶段阻断，尚未 provider dispatch、未到恢复断言。新的本地通过不改写旧日志。old-red 的 20 个预期基线失败、初期 schema ID / fake handle / readiness fixture 错误及后续修正原件完整保留，不能仅凭分数推断原因。收到的 64 个包成员按字节保留；它们此前已经过云端路径/hostname 分发脱敏，原始与分发哈希和修改区间由 packaging/DISTRIBUTION_PROVENANCE.json 记录，不能声称与分发前云端原件完全相同。

本地原日志、命令、UTC 时间、退出码、JUnit、源码清单、独立审阅及精选合成 CLI 输出发布到 evidence/first_wave/20261009/taskcontrol-reader。仅替换私有工作区和输入目录前缀，公开 MANIFEST 对每处替换记录原始/包含 SHA、字节区间及行号；XML 占位符转义并重新解析。所有完整原件、数据库、private profiles 和缓存留在工作区。未调用真实模型/provider、Docker、Actions 或业务 benchmark；早先 A1 环境阻断保持历史且未重测。本轮不生成返回 ZIP。

按 owner 长期授权，验收后提交并推送 origin/main；实际提交和 live remote SHA 核验由包外 publication/PUSH_RESULT.json 记录。独立代码审阅无 blocker；最终证据与交付清单复核另附 reviews/。此有限通过不等于全仓或历史全部实验通过。
'''
    (TASK / 'REPORT_ZH.md').write_text(report)
    print(json.dumps({'status': result['status'], 'report': str(TASK / 'REPORT_ZH.md')}))


if __name__ == '__main__':
    main()
