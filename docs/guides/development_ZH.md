---
name: rpnh-develop-and-validate
description: "开发与打包"
metadata:
  document-kind: guide
  audience: user-and-developer
  language: zh-CN
  counterpart: development.md
  revision: "2026-11-02.1"
  status: maintained
---

[English](development.md) | [中文](development_ZH.md)

# 开发与打包

## 准备开发环境

使用 Linux 或 WSL2、Python 3.11 或更新版本，在仓库根目录运行：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m pip install -r docs/requirements.txt
python -m pip install build
```

`test` extra 包含 pytest、NumPy 与 SciPy。凭据和生成的 profile 应放在仓库外。
测试使用确定性输入，真实 provider 检查需要单独配置并显式选择的环境。

## 执行检查

运行覆盖修改组件的测试；涉及广泛修改时运行完整套件：

```bash
python -m pytest -q
```

文档检查覆盖语言配对、本地链接及代码块语法：

```bash
python scripts/docs.py check
python scripts/check_doc_examples.py
python -m unittest discover -s tests -p 'test_docs_site.py' -v
python scripts/docs.py build --output /tmp/rpnh-docs-site
```

站点输出目录必须尚不存在。渲染器生成本地静态站点，不导入运行时、不调用模型、
不使用远程资源。语法检查不执行文档中的示例，也不访问外部链接。

## 构建与检查发行物

```bash
python -m build
python -m pytest -q tests/test_dsh_distribution.py::test_distribution_contains_runtime_and_public_source_assets
```

构建生成 wheel 和源码发行包。包装检查验证运行资源、公开文档、安装脚本，以及
开发指令的排除。源码包包含开发所需的测试和可复用示例。生成包放在 Git 忽略的
`dist/` 目录中。

将 wheel 安装到独立环境，再执行[安装指南](installation_ZH.md)中的安装后检查。
使用随库检查器时，设置源码根目录与独立安装环境的 Python：

```bash
: "${RPNH_SOURCE:?Set the source root}"
: "${INSTALLED_PYTHON:?Set the wheel environment Python}"
python "$RPNH_SOURCE/scripts/check_installed_docs.py"   --python "$INSTALLED_PYTHON" --source-root "$RPNH_SOURCE"
```

它检查包资源和无需模型的帮助／配置／示例命令，不执行业务工作流，也不认证可选
原生前端。

## Viewer 资源

仓库包含已构建的 Viewer 依赖及许可证，正常 wheel 构建不需要 Node。更新依赖时：

```bash
npm ci --ignore-scripts --prefix frontend/net-viewer
npm test --prefix frontend/net-viewer
npm run build --prefix frontend/net-viewer
```

同时提交对应 lockfile、静态资源和第三方许可证。

## 文档与贡献

指南说明用户任务，参考文档说明接口，架构文档解释设计。中英文应同步维护主题
元数据与链接；这些 Markdown 文件是普通文档。

修改范围与缺陷报告见[贡献指南](../../CONTRIBUTING_ZH.md)，发行选择见
[版本说明](releases_ZH.md)，客户端版本见[兼容性](compatibility_ZH.md)。保留根目录
MIT 许可证、第三方声明、DSH 上游许可证与 Viewer 依赖许可证。
