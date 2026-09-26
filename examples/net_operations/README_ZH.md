# 原生网操作示例

[English](README.md) | [中文](README_ZH.md)

## 仅定义示例

在仓库根目录运行：

```bash
python -m examples.net_operations.compose_serial
```

命令会输出一个完整的两阶段 `ModuleDeclaration`，不会写 Registry，也不会调用模型。
组件符号分别带有 `prepare_` 和 `finish_` 前缀，连接边是显式的，两个 operation 共享同一个
预先声明的预算桶。编译或运行前，调用方仍须提供匹配的可信 component、executor、terminal
和 schema 注册。

## 真实 Agent 替换示例

`live_agent_replacement.py` 是用于在同一 Registry 中验证全部当前可执行原生网操作的可复现任务：

1. Extract 取得第一个 Agent 定义。
2. Branch 取得后继定义，不启动第二个 owner。
3. Instantiate 为后继定义建立符号身份独立的实例。
4. Compose 生成初始串行图，并实际执行第一个 Agent。
5. Replace 在全网静止边界采用后继网；第二个 Agent 读取第一个 Agent 的已登记 workspace 文件，
   再发布终态结果。

下图来自 `live_result.json` 对应的已授权真实运行，是采用后继网之后的实际 PetriNet。
时间轴会明确停在 net-version 边界；图片不会把先后两个定义伪装成一张静态网。

![已采用的替换 PetriNet](assets/live-replacement-petrinet.png)

以下命令会产生真实 provider 费用。先检查 `rpnh config show`，取得其中显示的
`execution_config_path`，并使用仓库外一个尚不存在的目录：

```bash
python -m examples.net_operations.live_agent_replacement \
  --run-dir /tmp/rpnh-net-operations-live/run \
  --execution /absolute/path/from/rpnh-config-show.json
```

命令不会更改当前 profile，并拒绝复用已有 run 目录。PASS 必须同时具备 Registry 终态证据、
一次替换采用、连续的 workspace 血缘、预期的两个文件，以及成功结算的 `workspace`、
`read_file`、`write_file` 和完成动作。命令输出的 JSON 只包含已配置路线摘要和 Registry
证据；完整 Registry 与 provider-private audit 留在指定 run 目录中。

可用 `rpnh net --run RUN_DIR` 直接查看已经采用的后继网；加上 `--show-resources` 或
`--resources-only` 可查看两种显式资源投影。本示例没有声明 Petri resource 节点，因此两种
资源计数正确地均为零。

仓库中的 `examples/net_operations/live_result.json` 是一次经授权真实 provider 运行的脱敏结果，
不含 endpoint、凭据、本地路径、run 身份或原始 transcript。历史 Reentry 与 workspace
fork/import 计划尚不可执行，因此不会被写成真实 PASS。
