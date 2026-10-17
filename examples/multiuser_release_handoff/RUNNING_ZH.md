# 运行与查看原始任务

[English](RUNNING.md) · [示例](README_ZH.md) · [模型配置](../../docs/guides/models_ZH.md)

## 源码与环境

使用 Linux 或 WSL2、Python 3.11+、Git，以及同时含有本示例和匹配运行时依赖的源码 checkout。本地桥接使用 AF_UNIX 和 multiprocessing。按[安装指南](../../docs/guides/installation_ZH.md)安装，在 checkout 根目录运行命令，输出和配置放在仓库外。

历史 run02 实测运行时为 `7e27f27c3474ceb5a1ce4b82e4df624c28529f30`。公开基线 `798b4260e33cfb7f6476eee17e9f48de9ba998f2` 单独不含全部保留依赖；除公开恢复/HOST JSON 修复外，还需累计调用上限、显式上下文选择、原生 head 查询支持及保留的 Petri 输入修正。不要将示例与旧 wheel 混用并假定兼容。本次源码整理没有进行新的付费运行。

实际示例直接构造 `AgentTaskSpec`，使用 legacy `local_process` 适配器路径；累计调用上限传输限定于带 cap 的 legacy v14/v15 worker 格式，不包含 registered-host v13，也不推导任意前端/提供方兼容。保留下面的模块调用方式，确保插件函数身份能正确解析。

## 获取原始上游任务

上游为 [Kirito-Lab/EntCollabBench](https://huggingface.co/datasets/Kirito-Lab/EntCollabBench/tree/main)，数据修订 `df95c87317863d507f229df190b2c0a29c4e4afc`，根文件 `mcp_multi_tasks_40.json`，任务 `mcp_multi_26`。对应[代码快照](https://github.com/yutao1024/EntCollabBench/tree/9d085fcb86adaf20254c09e2ca35123e535a9643)说明下载后路径为 `scripts/dataset/mcp_multi_tasks_40.json`。

数据集许可证元数据未指定，检查的根目录无 LICENSE 文件。本包不授予数据使用权，也不再分发原始数据。请仅在有权使用时自行获取；安装官方 Hugging Face CLI 后：

```bash
WORKSPACE="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-mup.XXXXXX")"
export WORKSPACE
hf download Kirito-Lab/EntCollabBench mcp_multi_tasks_40.json \
  --repo-type dataset --revision df95c87317863d507f229df190b2c0a29c4e4afc \
  --local-dir "$WORKSPACE/upstream"
```

从 JSON 中精确选出唯一 `task_id=mcp_multi_26` 的完整原对象，保存为 `$WORKSPACE/original-task.json`。`design.actor_contracts` 从三个 `sub_task_list` 条目提取 `user_prompt` 与 `ground_truth` 参数：运维文件字段、开发分支/SQL、QA 检查表、支持事件字段。这些原始任务要求对角色可见，不是盲测；没有执行上游评测器。不得用新造合成任务代替，也不能将本适配环境称为官方基准环境。

生成原任务选择和仅用于脚本预检的公开配置：

```bash
python - <<'PY'
import json, os, sys
from pathlib import Path
w = Path(os.environ['WORKSPACE']).resolve()
source = json.loads((w / 'upstream/mcp_multi_tasks_40.json').read_text())
if not isinstance(source, list):
    raise SystemExit('Expected the pinned upstream JSON array; inspect its actual format before proceeding.')
rows = [row for row in source if row.get('task_id') == 'mcp_multi_26']
if len(rows) != 1:
    raise SystemExit('Expected exactly one original mcp_multi_26 task.')
(w / 'original-task.json').write_text(json.dumps(rows[0], ensure_ascii=False, indent=2) + '\n')
fixture = (Path.cwd() / 'examples/multiuser_release_handoff/tests/reader_fixture.py').resolve()
if not fixture.is_file():
    raise SystemExit('Run this from the matching source checkout root.')
condition = 'mup-native-reader-fixture-v1'
adapter = {
    'schema_version': 'local_process_adapter_config/v1',
    'adapter_kind': 'local_process', 'model_condition': condition,
    'argv': [sys.executable, str(fixture)],
    'probe_argv': [sys.executable, '--version'],
    'env': {}, 'inherit_env': ['PATH', 'HOME', 'TMPDIR', 'PYTHONDONTWRITEBYTECODE'],
}
(w / 'fixture-adapter.json').write_text(json.dumps(adapter, indent=2) + '\n')
selection = {
    'schema_version': 'llm_execution_selection/v1',
    'adapter_kind': 'local_process', 'model_condition': condition,
    'adapter_config_path': str(w / 'fixture-adapter.json'),
    'timeout_seconds': 120, 'max_output_tokens': 1024, 'max_response_bytes': 65536,
}
(w / 'execution.json').write_text(json.dumps(selection, indent=2) + '\n')
PY
```

## 无提供方预检

准备 `schema_version=llm_execution_selection/v1`、`adapter_kind=local_process` 的执行选择及匹配的 local-process 配置。仅对此预检路径，适配器可指向随包 `tests/reader_fixture.py`，model condition 为 `mup-native-reader-fixture-v1`。使用本机解析的绝对路径，输出预算 1024 tokens、响应 65536 bytes、超时 120 秒。reader 预检在新输出目录生成自己的配置，测试原生输入允许/拒绝及完成边界，不调用提供方。

```bash
python -m examples.multiuser_release_handoff.episode \
  --output "$WORKSPACE/preflight" --workspace-root "$WORKSPACE" \
  --execution "$WORKSPACE/execution.json" \
  --task "$WORKSPACE/original-task.json" --preflight
```

完整预检执行真实原生工具调用、重叠读取/写排斥、旧版本拒绝、精确消息交付与脚本 reader。预期 `PREFLIGHT.json` 为 PASS、模型/提供方调用零。fixture owner 如实停止，不伪装四个模型智能体完成，也不替代历史业务 episode。`--reader-preflight` 只选择 reader 检查。

## 另行授权的新模型运行

按[适配器配置参考](../../docs/guides/configuration_ZH.md)和[模型指南](../../docs/guides/models_ZH.md)准备自己可用的 **local_process** 适配器及匹配选择。适配器必须实现 RPNH 请求/响应协议，任意模型 CLI 不会自动兼容。不分发历史精确配置，不复制账号/凭证。选择并授权实际所需模型、推理/上下文限制及费用，不使用脚本 reader 配置跑真实业务。

授权模型和工具执行后，使用新的不存在的输出目录，去掉预检开关：

```bash
python -m examples.multiuser_release_handoff.episode \
  --output "$WORKSPACE/fresh" --workspace-root "$WORKSPACE" \
  --execution "$WORKSPACE/live-execution.json" \
  --task "$WORKSPACE/original-task.json"
```

此命令调用设计智能体，创建四个独立参与者和新本地 Git/Drive/事件世界，不连接真实 Drive/Gitea/ITSM。验收需检查 `RESULT.json`、各用户结果、真实工具回执及世界，不能只看退出码。可用 `rpnh net --run "$WORKSPACE/fresh/users/operations" --view --no-open` 查看真实 Registry。

新运行不会恢复历史世界。`--continue-business` 只在业务用户/世界未创建时采用已有终态设计；`--resume-design` 用于另行授权修复后的设计 Task 恢复。二者都不是恢复已初始化业务用户的捷径。原世界 continuation 脚本、私有检查点和配置不作为便携命令分发。

## 本地代码检查

测试位于 `examples/multiuser_release_handoff/tests`，同时使用 checkout 的 `tests/test_static_lease_reads.py` 等原生辅助代码，不是独立 wheel 测试包。从根目录运行 `PYTHONPATH=.:tests python -m pytest examples/multiuser_release_handoff/tests`。测试与模型/业务执行分开，保留失败，区分语法/import、原生离线通过和新的实跑。

## 已验证的发布入口

上述固定上游下载、原任务提取与配置命令已实际执行成功。完整原生预检通过13次注册工具调用，以及脚本reader的允许输入／拒绝路径和workspace完成检查，真实模型／provider调用为0；原生脚本请求记录与真实provider用量分列。八处保留依赖的80项定向检查在提供原任务后通过，新运行的三个控制流测试也通过。已用现有依赖构建wheel并隔离安装，核对schema catalog、v14/v15 cap往返与CLI入口。确切范围见[发布验证](release-validation.json)。

新的正式运行中，任何非终态角色返回会停止等待者。`RESULT.json`记录原生Source的firing／请求／lease claim计数及HOST reads／pending edit；只有全部为空才报告完成，并在最终Viewer capture前记录Source owner-stop。未决状态保持partial，供受支持的恢复与效果对账处理。
