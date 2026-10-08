# SlopCodeBench `code_search`：原生 Session 命令试验

[English](README.md) | 中文

这是可选的开发试验，把真实 RPNH owner／AgentLoop 接到原始 SlopCodeBench Docker
Session 内的正常编码、编辑和测试命令。它不是未经改动的官方 AgentRunner 运行。
本地已完成59项合成测试、安装态 owner／原生插件／AF_UNIX 命令路径，以及固定上游
Docker Session 和 Snapshot 的 complete／stop 夹具。每个夹具使用本地脚本响应，
分别3／2次提交，没有真实模型调用或基准解答；镜像是最小 Python 夹具，并非 SCB
通用基础镜像。**真实 code_search 模型试跑及原始判分器仍未运行**，不声称成绩、
提速或 harness 优势。原始失败及本地验收证据位于
`../../evidence/first_wave/20261008/`；旧记录保留原源码身份，不重标为集成提交上的实测。

默认范围是原始 **1 → 2 → 3 部分前缀**；固定任务有五个 checkpoint。这些公开题目
已用于开发检查，不是保留评测集。源码 revision 和许可见 `sources.json`、
`THIRD_PARTY_NOTICES.md`。

## 实际执行边界

`CheckpointPilot.run(当前 checkpoint, 已渲染 prompt)` 创建单节点 AgentTaskSpec，
通过公共 TaskControl 启动真正 owner。`session_command` 是声明 external_write 的
原生 managed plugin，每次已准入调用经本地 broker 执行一次原始
`StreamingRuntime.stream(command, env={}, timeout=60)`。模型可以在容器内正常
查看、编辑源码、运行自己的测试；大输出用 `read_managed_output` 读取，不重跑命令。
最终 write_file 产物是报告；解答源码仍由上游 Session 工作区持有。

模型不获得宿主 shell、任意宿主文件、delegation、原始判分器、后续题目、凭证或
broker socket。此任务拒绝额外 mount 和非空 static assets。solver 网络为 none，
避免从公网提前下载后续题目或参考答案；所需依赖必须预装在镜像中。CLI 解析并使用
准确的本地 Docker image ID，不会自动拉镜像、构建或安装依赖。
使用 disable_setup=True 前会拒绝任何已配置的 solver setup 命令；原始 evaluator
setup 保持独立且不改动。

Codex selection 还须传 `--codex-binary` 指向明确选定的官方可执行文件。owner 启动前，
私有 profile 快照将受支持的 RPNH bridge 绑定到私有端点，设置
`web_search="disabled"`、`project_doc_max_bytes=0`，并保留核心已有的原生工具禁用参数。
这会绕过环境中的全局包装器和继承的项目指令；模型、路由、请求预算与环境绑定不变，
准备步骤不读取或复制凭据文件。`endpoint-condition.json` 只记录控制条件，不公开
私有 profile 值。实现位于 SCB 包内，无需安装可选 ERP 包。

每个 checkpoint 边界都会停止整个 solver 容器，限时排空命令处理，再清理一次以
覆盖启动竞态。超时、响应丢失、输出超限、效果不明或清理失败，不会成为成功提交。
同一准确调用身份的重复传输只返回缓存；更换内容复用身份会拒绝。缓存只在当前
broker 生命周期内有效，不是崩溃恢复或远端 exactly-once 保证。
命令响应的 framed JSON 上限为 16 MiB，并保留 4 KiB 余量；超限会按效果不明
中止 checkpoint。超出部分不会保存，也不能恢复；read_managed_output 只能读取
已成功保留的结果，不能找回超限响应。

确认静止后使用原始 `Session.finish_checkpoint` 生成过滤后的快照。摘要针对原始
Snapshot 实际提取的文件，排除的 .venv、node_modules、缓存不会被误当成源码。
不可变记录绑定前后源码摘要、graph 身份、前序记录 hash 和真实 RPNH terminal
result。下一个 checkpoint 会核验输入摘要等于前一个 settled 输出。源码工作区继续
保留；每个 checkpoint 创建新的 RPNH Registry。

## 明确的开发条件

必须主动选择 `condition.example.json`，不能静默替换其他实验设置：

- 上游 cost／net-cost／step 上限明确选为 0，即关闭；正数上限会被拒绝，因为尚无
  忠实的用量／预算桥接。
- RPNH 仍有模型调用上限（示例 48）和 checkpoint 墙钟时限（示例 7,200 秒）。命令
  上限 60 秒，原生 IPC 操作时限 180 秒。这些是配置上限，不是测得的耗时。
- 每个 checkpoint 新建隔离网络的 solver 容器。源码保留，容器里临时安装的状态
  不保留；依赖应放进镜像。这是为获得静止快照而明确引入的改动条件。
- `run.py` 用原始 renderer 逐个披露当前题目，保留原顺序和测试，在独立 evaluation
  环境调用原始 checkpoint 判分器。结果不回灌下一轮模型。PassPolicy 显式选择；
  判分基础设施失败时停止。没有运行额外的代码质量模型评审。

官方 AgentRunner shim 仍被阻塞：其 UsageTracker 使用数值型默认值，而 RPNH
公共 task result 导出的是实际调用次数，没有规范化 token／费用摘要或逐调用 USD
准入 hook。试验把未知 token／费用写成 null／unavailable，绝不冒充实测 0。原始
runner 还在 finally 中快照，正式接入须防止未静止的快照成为有效判分证据。

本例实际使用的是 Registry 管理的 agent 执行与原生命令准入，以及上游拥有的
工作区连续性；不声称原生 workspace revision 复用、同 Registry 恢复、拓扑修改或
自动流程重构。将来的流程复用展示必须另立条件和结果。没有改动 core。

## 现在可运行的离线检查

在 RPNH checkout 和已有测试环境内：

```bash
python -m pytest examples/slopcodebench/tests -q
python -m examples.slopcodebench.preflight --prefix 3
```

测试在明确的注入边界使用合成 runtime、transport 和 owner 替身，检查真实 RPNH
图／plugin 声明，但不启动 owner、Docker、provider 或官方 grader。预检输出状态，
因完整验收尚未完成而返回 **2**；Docker 程序存在不代表 daemon 可用。不会探测被
拒绝的 socket 或重试访问限制。

可对固定 runner 源码只读检查 API，不导入或执行：

```bash
python -m examples.slopcodebench.preflight --runner-source /path/to/slop-code-bench
```

这只证明接口形状；实际 `run.py` 会先核对 Git revision 和已跟踪文件是否干净。

## 经授权的本地集成

下面是执行说明，**不是已执行证据**。先准备 Python 3.12+、固定上游文档要求的
依赖／Docker 基础镜像、已有的准确 RPNH 模型 profile，以及可用的 POSIX owner
socket。已获授权的常规环境准备和离线验收可以继续，无需逐步询问。真实 provider
调用、新增付费服务、权限扩张，以及适用本地策略限制的其他操作，须取得相应授权；
这不代表自动允许来源不明的软件或安全敏感配置变更。
必须使用 RPNH 源码基线 `ae09445fe1d9b973502bc5d2c961976c1d2c0163` 并应用本例。
较旧的已发布 wheel（包括 rc1）不是本例测试的 API 基线；包依赖本身不能核验 commit。

在运行 RPNH 的同一 Python 环境安装可信 checkout 内的可选 plugin：

```bash
python -m pip install -e . -e examples/slopcodebench
```

SCB 和 PROBLEMS 应为 `sources.json` 指定的干净 checkout。预先准备含所需依赖的
本地 runner 基础镜像，题库／grader 不得进入 solver mount。OUT 使用仓库外较短
路径，以免超过 UNIX socket 路径限制。

```bash
python -m examples.slopcodebench.run \
  --runner-source "$SCB" --problems-source "$PROBLEMS" \
  --environment "$SCB/configs/environments/docker-python3.12-uv.yaml" \
  --template "$SCB/configs/prompts/just-solve.jinja" \
  --execution "$EXECUTION" \
  --codex-binary "$OFFICIAL_CODEX" \
  --condition examples/slopcodebench/condition.example.json \
  --output "$OUT" --prefix 3 --pass-policy any-case \
  --acknowledge-development-model-run
```

安装后也可用参数相同的 `rpnh-scb-pilot`。Python API 是 `rpnh_scb.pilot` 中的
CheckpointPilot，需要原始 inference Session、已解析的准确镜像 ID 和明确选定的
DevelopmentCondition。Codex selection 另传 `codex_binary`；非 Codex profile 省略该
CLI/Python 参数。run 只接受下一个 checkpoint 的当前 prompt；真实实验不要
使用以下划线命名的测试注入入口。

真实成功执行后的预期文件：

- condition.json、environment.json、definition.json、input-identities.json
- 每个 checkpoint 的 request／before／after、snapshot/、本地 Registry/control、
  commands.jsonl、rpnh-result.json、result.json
- 原始 grader 的 grade.save() 写出的原始评价文件
- lineage/ 下不可变逐轮记录和 development-summary.json

进入 coordinator 后失败的尝试保留 failure.json 和失败记录，不继续后续 checkpoint
或判分。Session spawn 和 broker 构造失败也进入该边界；仅在已有 runtime handle 时
尝试清理，并保留原始异常。
不支持自动恢复／重试；效果不明时先停止诊断，不能为重新读取输出而重放
命令。源码变化后的旧分数不得直接沿用。
failed 指 checkpoint 尝试失败，不代表没有副作用；可能仍有部分源码改动，不能
把它当作从未执行而重放。

## 发布与设计反馈

原始评分、额外检查、mock 和未运行阶段必须分开。contracts.py 的 raw-workspace
函数只是有界观测辅助；pilot 使用原始 Snapshot 选择。ledger 不提供 sandbox，
也不授权 benchmark 继续。

发布遵循 `../example_validation/` 的 rpnh/example-evidence/v1；lineage 作为带
hash 的 artifact。真实 Registry 引用原样复制，不能由 hash 构造。profile、凭证、
数据库和未审查输出留在本地。用户要求原始失败内容供网页端分析：将审查后不含秘密的
日志、保留的请求／响应材料和判分明细发布到 `evidence/first_wave/`，安全时保留原字节。
如确有凭据，只替换必要字段、逐项注明变更，完整原件继续留在本地。请求 recipe、
adapter 返回与未保留的 vendor wire 应明确区分，不能仅凭分数归结为单一原因。

当前设计反馈：现有 managed-plugin API 可以完成正常命令接入，本路线不需要新增
workspace seed/export core 功能。可移植的 settled-workspace 导出和公共用量／预算
观测是另外的潜在易用性缺口，不是本例已证明的核心不变量错误。
