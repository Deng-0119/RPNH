---
name: rpnh-install
description: "Install unified RPNH and distinguish its optional presentation and host integration paths."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: installation.md
  revision: "2026-09-29.3"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "unified main; host differences explicitly labelled"
---

[English](installation.md) | [中文](installation_ZH.md)

# 安装与分发范围

## 目标与前提
从明确指定的 RPNH 源码或 wheel 安装，不调用模型。当前运行支持范围仍是 **Linux（包括 WSL2）**，不支持原生 Windows 和 macOS。`pyproject.toml` 声明 Python `>=3.11`，不等于每个 Python/平台组合均已测试；运行时涉及 Unix socket、POSIX 进程控制和 Linux 进程检查。basic 文本前端不需要 Codex 或 Node。

发行包名称是 `rpnh-harness`，版本 `0.1.0`，Python 导入包为 `cpn`，用户命令为 `rpnh`。依赖是 `jsonschema>=4.20,<5`、`websockets>=12,<16`。当前**不存在 `codex`、`dsh` pip extra**。不要在同一环境安装来自不同维护线的两套 `cpn`。

Canonical 源码仓库是 GitHub 上的 `Deng-0119/RPNH`，目前受访问控制。本文不声称已经发布
包索引版本或可下载 release artifact。请取得获准的 checkout／archive，或从已审查提交构建
wheel；以下命令不假定包索引已发布。

## 从批准的源码安装
在包含 `pyproject.toml` 的源码根目录运行 Bash：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
rpnh --help
rpnh config --help
```

开发时才使用 `python -m pip install -e '.[test]'`。依赖安装可能访问包索引，但不是供应商/模型调用。记录源码提交与依赖版本。basic 使用同一内核，不是另一份执行实现。

## 构建 wheel，并在源码目录外安装
构建需要单独的 `build` 工具及已声明的 setuptools 后端：

```bash
python -m pip install build
python -m build --wheel --outdir dist
```

把 `WHEEL` 设置为刚构建产物的绝对路径；这是操作者提供的路径，不是公开下载地址：

```bash
: "${WHEEL:?Set the absolute path of the approved wheel}"
TEST_ROOT=$(mktemp -d)
python3 -m venv "$TEST_ROOT/venv"
"$TEST_ROOT/venv/bin/python" -m pip install "$WHEEL"
cd "$TEST_ROOT"
"$TEST_ROOT/venv/bin/rpnh" --help
"$TEST_ROOT/venv/bin/python" -c 'import cpn; print(cpn.__file__)'
```

输出应来自新环境的 `site-packages`，不能来自源码目录。[开发维护](development_ZH.md)介绍源码包内 `scripts/check_installed_docs.py` 的受限零模型配置检查；它不验证模型和交互式终端。

wheel 还包含 provider-neutral 的跨宿主任务包。导出只是零模型文件操作，不需要源码 checkout：

```bash
"$TEST_ROOT/venv/bin/rpnh" examples list
"$TEST_ROOT/venv/bin/rpnh" examples export --output "$TEST_ROOT/adapter-task"
```

传入 execution profile 并提交导出的任务是另一项可能产生费用的真实操作；须按任务包内对应
宿主的说明执行。每个宿主目录包含一份既有验收的脱敏摘要，但不包含凭据，也不能替代对用户
自有路线的验证。

## 选择安装范围

| 范围 | 必需内容 | 入口和当前边界 |
|---|---|---|
| 内置终端 | 批准的统一 Python 包 | `rpnh --frontend basic`；对话仍需选择模型 |
| Codex 展示 | 统一包和精确 `codex-cli 0.155.0` | `rpnh --frontend codex`；兼容性绑定版本 |
| OpenCode 展示 | 统一包和精确 OpenCode `1.18.32` | `rpnh --frontend opencode`；仅显式选择、仅 Linux/WSL2，且无 UI 指标 |
| 插件、引导与案例 | 批准的统一 Python 包 | `rpnh plugins`、`rpnh init`、`rpnh doctor`、`rpnh examples`；本地检查和导出不调用模型 |
| 托管 DSH | 统一包、内含 `integrations/dsh`、固定上游及工具链 | `rpnh-dsh`；显式离线数值或共享配置文本模式 |
| 共存 | 统一源码/wheel，加所需可选宿主 | 不要在同一环境覆盖安装多份同名 wheel |

统一 main 默认使用 `auto`：在交互式终端中，找到兼容的 Codex `0.155.0` 时使用它，否则使用内置终端；非交互式使用内置终端。需要特定界面时显式选择 `--frontend basic`、`codex` 或 `opencode`。使用可选宿主前阅读[适配指南](adapters_ZH.md)、[OpenCode](opencode_ZH.md)或 DSH 指南。只读 Viewer 及其打包资源属于同一统一发行包，但它不是另一套执行后端。

## 验证、升级与卸载
只有已安装命令、包内 schema/config/static/example 资源，以及源码目录外的零模型配置流程均通过，才可称安装验证通过。editable 安装成功或链接检查通过都不足以替代。Viewer wheel 校验要求完整的固定 vendor 文件集合及许可文件；空清单或自行缩减的 asset manifest 不能免除这些文件。本文档批次单独记录 wheel 验证和站点检查。

升级前按检查点停止受影响任务，保留会话/run 目录与唯一 catalog 的一致性私有备份，再在新环境安装批准的 wheel。由 catalog 重建 profile，复核精确模型身份后恢复。必须核对 schema 兼容；不要靠改版本字符串或清 writer 锁修复历史 Registry。

`python -m pip uninstall rpnh-harness` 不删除用户 catalog、shell 凭据或会话/run 数据。请明确决定保留策略，不删除活跃运行目录。回退环境使用原批准产物，但不会撤销外部效果，也不代表可打开不兼容 Registry。

## 代码对应
`pyproject.toml`、`cpn/rpnh_cli.py:_parser`、`cpn/rpnh/user_config.py`、适配中的 `integrations/dsh/run.sh`。教程命令不是执行证据，实际完成情况见进展记录。
