---
name: rpnh-environment-preparation
description: "通过既有 RPNH owner 检查、解析、准备并启动接收端选定环境。"
metadata:
  document-kind: reference-guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: environment-preparation.md
  revision: "2026-10-07.1"
  status: source-reviewed-pre-release
---

[English](environment-preparation.md) | [中文](environment-preparation_ZH.md)

# 接收端环境准备

此应用层区分分享物、本机准备与 Registry 业务执行。v2 作者声明及精确身份见
[可移植环境声明](guides/package-environments_ZH.md)。

## 支持的边界

- `check_environment` 读取所选解释器的 Python、发行包及实际生效的传递依赖元数据；不安装、不 lowering、不编译、不建立 Registry writer、不执行插件注册工厂、不请求 provider
- 用户选中的本机可信只读 adapter 可以检查系统能力、工具、插件元数据、配置及凭据引用。这是执行探针，与纯数据 package preview 不同
- `resolve_local_wheels` 只读取显式提供的本地 wheel 元数据及哈希，核对 wheel tags、`Requires-Python` 和有界具体依赖闭包；不下载、不运行构建后端、不安装。无法支持或有冲突时保持 unresolved，不另建隐藏环境
- `plan_environment` 仅生成数据；已有环境的替换必须显式选择，计划逐项列出精确制品和目标
- `prepare_environment` 要求可信进程内授权回调核对 plan digest、动作和目标。JSON 中的 approved 字段会被拒绝。新 venv 路径必须不存在；取消或失败保留已发生动作和部分目录，不删除用户原环境
- 准备在所选 Python 内启动实际安装的可信 HOST，使用同一检查器复核、装配真正 Registration，并编译分享包中已核验的 Module bytes；不执行业务 operation
- 另行授权的 `launch_package` 重验材料、本机绑定、安装身份、配置及 HOST 声明，再沿用既有 `start_run`、`OwnerEventLoop`、`ExecutionServices`、`Orchestrator`；实际 dispatch 前继续检查漂移，不暗改锁或重装

venv 创建成功不代表系统或工具要求满足。新环境创建前取消时，检查结果仍是目标缺失，
不会用 base Python 冒充已准备的目标环境。

## 安装后的命令流程

使用已安装的 `rpnh package`。涉及材料的命令都接受
`--archive ROOT.zip --lock PACKAGE_LOCK.json [--local-package DEP.zip] --entry main`。

1. `check-environment --selection SELECTION.json --output CHECK.json`
2. `resolve-environment --selection SELECTION.json --check CHECK.json --wheel EXACT.whl --output RESOLUTION.json`
3. `plan-environment --selection SELECTION.json --check CHECK.json --resolved-selections RESOLUTION.json --wheel EXACT.whl --output PLAN.json`
4. `prepare-environment --selection SELECTION.json --check CHECK.json --resolved-selections RESOLUTION.json --plan PLAN.json --state-dir PRIVATE_DIRECTORY --output RECEIPT.json`
5. `run --binding RESULT_BINDING.json --receipt RECEIPT.json --resolved-selections RESOLUTION.json --owner-request OWNER.json --run-dir ABSENT_RUN_DIRECTORY`

每个精确 wheel 分别传入一次 `--wheel`。没有 wheel 时，resolve 固定实际观测到的已安装选择。
`--allow-existing-changes` 显式允许选中替换 wheel，包括同版本不同代码；应用计划仍需授权。
不提供默认 `--yes` 绕过。交互式准备展示动作和路径，要求输入精确 plan digest；run 展示独立
owner request，要求输入它的身份。非交互调用者使用同一 API 和真正可信执行上下文，不能以
序列化授权文件代替。

只读命令默认不写文件；显式 `--output` 才保存，且拒绝覆盖。标准输出是公开状态投影，
完整私有报告、路径及本机引用只保存到显式选择的文件。准备在 `--state-dir` 内以私有权限
保存 digest 命名的不可变 plan、resolution、checks、binding、receipt；未覆盖路径时默认为
`config_path().parent/environment-preparation/<binding_id>`，该目录不是 Registry。

检查已准备环境时用 `--binding` 替换 `--selection`，并给出精确 `--resolved-selections`。
new_venv selection 明确绝对 base_executable 和尚不存在的 prefix；binding 记录实际解释器和
prefix。即使 Python 可执行文件是符号链接，启动器也保留所选 venv 的语义。

## 本地 agent／安装文档路径

`rpnh package setup-instructions --plan PLAN.json --format text|json --output SETUP`
生成私有接收端文档，包含相同 target、plan digest、动作、制品、失败保留规则和验收条件。
把文档交给另一个进程或 agent 不产生授权。完成已授权操作后仍必须调用同一检查器，并在
所选解释器内实际装配 HOST；文字声称成功或勾选 completed 不能代替观测，也不能授权运行。
计划变化必须重新取得精确审批。

## 可信 HOST 与只读探针扩展

本机显式选择已安装的 host_profile_id。`rpnh.environment_hosts` entry-point group 将该 ID
映射到返回 HostProfile 的工厂，只有明确准备／运行才加载它。Registration callable 仍是
可信 Python 对象，分享物 JSON 不能携带 import locator。默认驱动支持既有注册 executor
与 native plugin 路径；自定义 profile 可以提供已有执行服务／绑定工厂，但不新建 writer
或调度循环。

`rpnh.environment_probes` 可用相同 ID 注册独立只读
`factory(local_selection_or_binding) -> ProbePolicy`。普通检查在所选解释器内执行该本机
可信探针工厂及 adapter，不执行 HOST 或插件注册工厂。adapter 必须保持本机只读，不能
探测 provider 远程可用性。身份摘要覆盖 HOST、探针 entry-point 选择、包代码和 `__init__.py`；
来自不同发行包的显式探针也参加 profile 身份。内置 `rpnh-native/v1` 支持显式选中的已安装
native plugin 配置文件和元数据。

尚未安装的插件，其选定精确 wheel 可以包含有界纯数据
`.dist-info/rpnh_environment_plugins.json`：

```json
{"schema_version":"rpnh/installed_plugin_metadata/v1","plugins":[{"plugin_id":"example","version":"1.0","api_contract":"rpnh/plugin/v1","entry_point":"example"}]}
```

解析器核实该发行包内确有指定 `rpnh.plugins` entry point 及实现 bytes，检查 API、版本和
作者 scoped distribution 引用，并绑定本机配置文件摘要。安装后仍要真实装配和复核；这些
元数据不证明 operation 成功。缺少该文件或使用 opaque 配置引用时，需要显式解析的受支持
adapter，或人工准备后再检查；不能为发现声明就偷偷执行工厂。

## 证据、隐私与失败

报告始终为 execution_permitted=false。只能观测已安装元数据时，artifact_digest=null；
版本相同不证明所有安装文件相同。远程可用性、实时权限、容量和作者实现身份仍明确未检查。
准备 receipt 没有 business-success 字段。公开证据只投影允许的 target、状态、选择摘要及
声明摘要，不含本机路径、凭据引用、原始探针输出或 binding digest。由同一个运行 owner
在 admission 前发布应用证据。Registry exact ref 与材料摘要是不同类型，不能混用。

owner 控制传输不可用（包括 AF_UNIX 被拒绝）是真实运行边界，不能改用直接 worker、假 runner
或替代传输。创建 owner／Registry 或编译 Module 不等于业务 terminal。已有运行目录会被拒绝，
而不是重复建立 writer；需要恢复时沿用已有明确 owner／resume 协议。安装、装配、漂移及业务
运行失败保留各自阶段和真实的部分证据。

退出码：0 合同操作完成；2 参数／合同错误；3 缺项、未解析或不兼容；4 需要授权或准备不完整；
5 漂移／中断。Python launcher 仅在真实 owner／控制 socket 建立后返回 ExistingRunHandle；
handle.wait() 取得既有 run/task/net 及 terminal 事实，handle.request_stop() 向同一个 owner 发出
停止信号，CLI 等待该 handle。交给既有 owner 后沿用其真实停止／terminal 语义。自动测试离线执行，真实
provider 调用始终需要单独明确授权。

既有 execution_environment_identity/v1（research-exp）仍由相应的可选数值／工作区 HOST
绑定发布。纯 native profile 不制造第二份身份权威；其实际 HOST／worker 观测也不能代替
该独立可选路径的真实验收。
