---
name: rpnh-install
description: "Install unified RPNH and distinguish its optional presentation and host integration paths."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: installation.md
  revision: "2026-11-02.1"
  status: source-installation
  basis: "unified main; host differences explicitly labelled"
---

[English](installation.md) | [中文](installation_ZH.md)

# 安装与分发范围

## 目标与前提
从明确指定的 RPNH 源码或 wheel 安装，不调用模型。当前运行支持范围仍是 **Linux（包括 WSL2）**，不支持原生 Windows 和 macOS。`pyproject.toml` 声明 Python `>=3.11`，不等于每个 Python/平台组合均已测试；运行时涉及 Unix socket、POSIX 进程控制和 Linux 进程检查。basic 文本前端不需要 Codex 或 Node。

发行包为 `rpnh-harness`，导入包为 `cpn`，命令为 `rpnh`。
当前公开源码版本为 `0.1.0rc3`。10 月 30 日的
[v0.1.0rc3 release](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3)
包含 Python wheel 与 stock DSH operator tarball，**不含** 11 月 1 日的源码修复；
详见[源码与产物边界](compatibility_ZH.md)。
版本字符串不能单独区分这些源码；应记录提交或产物摘要，并使用全新环境。
不表示包索引发布或 runtime 全面验收。

依赖为 `jsonschema>=4.20,<5`、`packaging>=24,<27`、`websockets>=12,<16`。
没有 `codex` 或 `dsh` pip extra；不要在同一环境覆盖安装不同来源的 `cpn`。

## 一键源码安装

需预备 Python 3.11+（含 `venv` 与 pip 引导能力）、Git、curl 和 Bash。
以下下载并执行[公开安装脚本](../../scripts/install.sh)，建议先查看脚本。
需要访问 GitHub 与依赖包索引，不需要模型凭据，也不调用模型。

```bash
bash -c 'set -e; installer=$(mktemp); curl -fsSL https://raw.githubusercontent.com/Deng-0119/RPNH/main/scripts/install.sh -o "$installer"; bash "$installer"; rm -- "$installer"'
```

脚本创建 `$HOME/.local/share/rpnh/source` 和隔离的
`$HOME/.local/share/rpnh/venv`，安装 RPNH 与原生演示插件，检查已安装 CLI，
并打印精确源码提交。目标目录已存在（含符号链接）时拒绝安装；不用 sudo，
不修改系统 Python、PATH 或 shell 启动文件。失败时保留目录用于诊断，
请选择新目录，不覆盖重装。

如需先检查脚本、自选目录或固定已审阅提交，分开下载与执行：

```bash
INSTALLER="$(mktemp)"
curl -fsSL https://raw.githubusercontent.com/Deng-0119/RPNH/main/scripts/install.sh -o "$INSTALLER" &&
less "$INSTALLER" &&
bash "$INSTALLER" --prefix "$HOME/rpnh-source-install" --ref ab5e4bfc5582870b56e23f9539ccad6a8c4be649
```

固定提交示例安装的是该现有源码，不是新 release。脚本本身从 `main` 下载，
应检查所下载的内容。一键命令默认安装执行时的公开 `main`，不包含未公开开发内容。

默认安装后：

```bash
. "$HOME/.local/share/rpnh/venv/bin/activate"
cd "$HOME/.local/share/rpnh/source"
```

自定义 prefix 时替换上述两个路径。接着可运行
[零模型加法案例](../../README_ZH.md#使用案例两数相加并查看运行)。
安装器不配置 provider、可选前端或真实任务。缺少前提组件时，应按通常的系统安装流程准备；
脚本不安装系统包或修改安全设置。

## 从批准的源码安装
在包含 `pyproject.toml` 的源码根目录运行 Bash：

```bash
SOURCE_ROOT="$PWD"
git rev-parse HEAD
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
"$TEST_ROOT/venv/bin/python" -c 'import cpn; from importlib.metadata import version; print(version("rpnh-harness")); print(cpn.__file__)'
```

当前源码输出版本应为 `0.1.0rc3`；10 月 30 日 wheel 也使用同一版本，因此还须核对选定的源码提交。包路径应来自新环境的 `site-packages`，不能来自源码目录。[开发维护](development_ZH.md)介绍源码包内 `scripts/check_installed_docs.py` 的受限零模型配置检查；它不验证模型和交互式终端。

从本源码候选版构建的 wheel 包含命名可复用案例以及默认的 provider-neutral 跨宿主任务。
导出只是零模型文件操作，不需要源码 checkout。旧 rc1 wheel 只包含 adapter 任务。当前命名
导出可使用 `--example native_plugin`、`hybrid_summary`、`compose_serial` 或 `package_reuse`；
详见[导出与修改](examples_ZH.md#导出案例并改成自己的应用)与
[可运行 v2 教程](package-reuse-example_ZH.md)。

```bash
"$TEST_ROOT/venv/bin/rpnh" examples list
"$TEST_ROOT/venv/bin/rpnh" examples export --output "$TEST_ROOT/adapter-task"
```

传入 execution profile 并提交导出的任务是另一项可能产生费用的真实操作；须按任务包内对应
宿主的说明执行。每个宿主目录包含一份既有验收的脱敏摘要，但不包含凭据，也不能替代对用户
自有路线的验证。

## 可选前端与宿主

内置 basic 终端和只读 Viewer 已包含；basic 对话仍需获准 provider 与精确模型，
按[模型指南](models_ZH.md)配置。引导脚本不安装可选客户端。

| 入口 | 当前边界 |
|---|---|
| Codex | 默认 `0.163.0`；`0.166.0` 是[显式候选](codex-candidate-0166_ZH.md)。 |
| OpenCode | 默认 `1.19.4`；`2.2.5` 仅为 [REST／认证／非持久化事件子集](../reference/opencode-v2-candidate_ZH.md)，不是完整原生前端。 |
| Stock DSH operator | RPNH Python companion 与 operator 手动配对 DSH `0.2.2-alpha.2`，按[operator 安装指南](../../integrations/dsh/operator/README_ZH.md)操作；不代表托管升级验收。 |
| 早期 DSH Web／headless 路径 | 是不同集成入口，按 [DSH 指南](dsh_ZH.md)使用，不适用 stock operator 安装步骤。 |

其他展示客户端见[前端配置与切换](frontends_ZH.md)。
不要把旧 DSH Web 固定版本配置混入 stock alpha.2 operator 配对。
这些是独立 RPNH 适配，不表示上游背书或任意版本兼容。
选择安装方式前请查看[兼容性与已知限制](compatibility_ZH.md)。

## 验证、升级与卸载
只有已安装命令、包内 schema/config/static/example 资源，以及源码目录外的零模型配置流程均通过，才可称安装验证通过。editable 安装成功或链接检查通过都不足以替代。Viewer wheel 校验要求完整的固定 vendor 文件集合及许可文件；空清单或自行缩减的 asset manifest 不能免除这些文件。本文档批次单独记录 wheel 验证和站点检查。

升级前按检查点停止受影响任务，保留会话/run 目录与唯一 catalog 的一致性私有备份，再在新环境安装批准的 wheel。由 catalog 重建 profile，复核精确模型身份后恢复。必须核对 schema 兼容；不要靠改版本字符串或清 writer 锁修复历史 Registry。

`python -m pip uninstall rpnh-harness` 不删除用户 catalog、shell 凭据或会话/run 数据。请明确决定保留策略，不删除活跃运行目录。回退环境使用原批准产物，但不会撤销外部效果，也不代表可打开不兼容 Registry。

## 代码对应
`pyproject.toml`、`cpn/rpnh_cli.py:_parser`、`cpn/rpnh/user_config.py`、`cpn/rpnh/frontend_config.py`、`cpn/frontend/dsh_launcher.py`、`cpn/frontend/dsh_assets.py`、`integrations/dsh/frontend/official_host.ts`、`official_bff.ts`、`setup.ts`、旧版适配的 `integrations/dsh/run.sh`。教程命令不是执行证据，实际完成情况见进展记录。
