---
name: rpnh-develop-and-validate
description: "Maintain documentation and verify actual artifacts without implicit CI or model calls."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: development.md
  revision: "2026-09-29.2"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](development.md) | [中文](development_ZH.md)

# 开发、验证与发布维护

## 绑定精确版本
记录源码、相关适配版本与实际执行命令。功能分支包含 main，不证明两个适配可共存。保留并行贡献者改动，快进提交前重读目标 HEAD。文档修改不授权运行设计变更、真实供应商调用、Actions、公开发布或站点部署。

## 确定性测试与已安装产物检查
按所选源码准备独立 Linux 环境并安装声明的测试依赖：

```bash
python -m pip install -e '.[test]'
python -m pytest -q
```

这是仓库通用测试入口，不是每次改动都要执行的命令。文档或局部修复应运行直接受影响的
聚焦测试；只有跨运行时／schema 重构可能影响无关组件，或明确准备完整候选时，才运行全量
套件。真实 provider 检查必须另行授权，且只用于离线测试无法证明的传输或端到端行为。测试
范围是针对改动的证据，不是进度计数器。唯一的 `.[test]` extra 声明 pytest、numpy、scipy。
依赖缺失应如实记录，不能归因到运行语义；不得为迁就预装库而放松版本边界。

仓库提交 lockfile 固定的 viewer bundle 及许可文本，使普通源码／sdist wheel 构建不依赖
Node 或网络。只有明确更新这些依赖时，才重新生成并测试已提交资源：

```bash
npm ci --ignore-scripts --prefix frontend/net-viewer
npm test --prefix frontend/net-viewer
npm run build --prefix frontend/net-viewer
```

真实 wheel smoke 先按[安装](installation_ZH.md)构建并在源码外安装，再把 RPNH_SOURCE 指向源码，INSTALLED_PYTHON 指向独立环境 Python：

```bash
: "${RPNH_SOURCE:?Set the approved source root}"
: "${INSTALLED_PYTHON:?Set the wheel environment Python}"
python "$RPNH_SOURCE/scripts/check_installed_docs.py" \
  --python "$INSTALLED_PYTHON" --source-root "$RPNH_SOURCE"
```

checker 检查安装来源及包内资源，使用临时 HOME/config，只执行 help 和空 catalog init/build/check/list。子进程审计 guard 拒绝 socket connect、再启动子进程和 SQLite connect，不打开 Registry writer、不提供凭据。通过不代表 basic/Codex 交互、插件执行、DSH 固定宿主、resume 或真实模型通过；脚本不代装依赖。

## 文档检查与本地站点
源文件为 docs/guides、docs/architecture、docs/reference、两个 docs index 和 README 语言对。`_ZH.md` 是同 topic name/revision 的中文页；协议字段和命令不翻译。它们是普通文档，不是可执行插件 skill。

```bash
python -m pip install -r docs/requirements.txt
python scripts/docs.py check
python scripts/check_doc_examples.py
python -m unittest discover -s tests -p 'test_docs_site.py' -v
python scripts/docs.py build --output /tmp/rpnh-docs-site
```

输出目录须尚不存在。站点使用 markdown-it-py，直接渲染同一组源文件、转换内部链接，提供主题导航、标题锚点和逐页语言切换；无 JavaScript、远程资产、runtime import、模型调用或托管服务，直接打开 index.html。检查覆盖 metadata、对应语言、标题、链接、代码块语法。JSON/Python/Bash 语法检查**不会执行示例**。构建和链接检查分别报告，离线 checker 不探测外链。

缺少可选 Markdown/YAML 库时，文档测试明确标记 skip，不让仅安装核心测试依赖的环境因文档测试收集失败。安装 `docs/requirements.txt` 后才实际运行；schema 示例 checker 使用 runtime 已声明的 `jsonschema`。

已按官方配置资料评估 Sphinx/MyST/PyData，但文档环境未能安装依赖，因此未采用、也不声称 Sphinx 构建通过。轻量本地 renderer 仅服务文档，以后替换无需改变 Markdown 正文或 runtime。

## 贡献、兼容与问题报告
先读仓库约定，保持改动聚焦，为行为变化加确定性回归并同步指南/参考/示例。结构移动与语义变化分开。区分声明契约、高级可信宿主接口、私有实现，不把全部符号宣传为稳定 SDK；明确版本化 schema 兼容和旧证据来源。

报告包含 revision、平台、最小脱敏复现、预期/实际和边界，分享日志前看[排障](troubleshooting_ZH.md)。不提交原始 Registry、workspace、模型 transcript、密钥或私有 endpoint。workflow/PR 操作前单独核查触发条件；commit skip 字符串不足以证明安全。

## 许可、归属与发布

RPNH 使用根目录的 MIT License。第三方组件保留各自许可；源码和构建产物必须保留
`THIRD_PARTY_NOTICES.md`、固定 DSH 上游许可和 viewer 库许可。

当前代码树是发布候选，不代表每个可选宿主或用户自有 provider route 已经可用。公开前应验证
真实 wheel 与安装后的命令入口。私有 Registry 数据和真实 provider 证据不得进入公开仓库。

官方工具资料：[Markdown parser](https://markdown-it-py.readthedocs.io/en/latest/using.html)、[Sphinx Markdown](https://www.sphinx-doc.org/en/master/usage/markdown.html)、[PyData 安装](https://pydata-sphinx-theme.readthedocs.io/en/stable/user_guide/install.html)、[Python 打包](https://packaging.python.org/en/latest/tutorials/packaging-projects/)。运行支持事实来自实际仓库，不由这些外部教程推导。
