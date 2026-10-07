# 两版本声明与显式重新绑定

[English](AUTHOR_VERSIONS.md)

导出的 `selfcheck_declaration.py` 是声明入口：明确加载所选**可信已安装 factory**，
用公共 PluginDefinition/PluginResource 检查 schema、依赖，输出完整 descriptor、
资源 hash 与真实导入路径；不绑定 HOST。请安装 wheel 并使用 `python -I`，避免
导出的源码遮蔽已安装模块。独立的 `selfcheck_terminal.py` 是执行入口：沿
`rpnh plugins run` 使用的现有 runtime 运行一个已授权 pure operation，保存真实
JSON result，再核对输出、terminal evidence 和零模型调用。IPC 不可用是 blocked，
不能记成 pass。两者均不依赖核心私有测试助手，也不要求全仓 suite。

| 事实 | A 版本 | B 版本对照 |
|---|---|---|
| 身份 | distribution 名称/版本、插件名称/版本、entry point | 同步 pyproject、惰性 metadata、factory、selection |
| 完整 descriptor | `declaration-a.json` 的 `descriptors` | `--previous declaration-a.json` 输出精确前后差异 |
| Schema | config、operation input/output | 检查必填字段、类型与边界 |
| Effect | 各 operation 的 `effect` | 重审所需权限；此运行检查只接受 pure |
| 依赖 | `requires` 的精确插件版本 | 明确选择并安装精确依赖 |
| 资源 | media type、字节数、SHA-256 | 同名资源字节变化也需重新绑定 |
| Handler | 模块/函数身份、模块 SHA-256 | 改名或代码改动会改变身份；传递依赖仍属于环境 |

先为 A 生成报告，再从导出目录复制 B。例如 distribution 从 `rpnh-native-demo`
改成 `my-native-tools`，Python 模块从 `rpnh_demo` 改成 `my_native_tools`，插件与
entry point 从 `demo` 改成 `my_tools`，版本从 `0.3.0` 改成 `0.4.0`。同步
`pyproject.toml` 的 py-modules/entry-point 值、`rpnh_environment_plugins.json`、
`PluginDefinition` 和 `plugins.json`。本例按自身发布政策要求发行版与插件版本一致；
RPNH 通用 API 不要求这两个版本相等。

从导出根目录使用已批准的本地构建依赖与 wheelhouse 显式构建安装。自检不会安装：

```bash
python -m build --wheel --no-isolation --outdir ./wheels ./examples/native_plugin
python -m pip install --no-index --no-deps ./wheels/my_native_tools-0.4.0-py3-none-any.whl
python -I examples/native_plugin/selfcheck_declaration.py --output declaration-b.json --previous declaration-a.json
python -I examples/native_plugin/selfcheck_terminal.py --operation my_tools/add --run-dir /absolute/absent/b-run --result terminal-b.json
```

改名的发行包可以共存，但 entry point 必须唯一。声明检查分别定位 pyproject、惰性
metadata、selection 和实际 PluginDefinition 冲突。已安装发行包、wheel 惰性声明、
selected、factory-loaded verified、HOST bound 是不同证据阶段。惰性声明缺失保持
unknown；`cpn.plugins.catalog` 的 `inspect_plugins`、`inspect_plugin_wheel` 不加载
factory。安装文件 implementation fingerprint 不是原始 wheel SHA。

人工接受差异后运行 `rpnh plugins ... check`，重建派生 package 声明/archive，解析
**新的精确 lock**，重新选择环境、prepare 并检查新 binding，最后开始全新 run。
包工作流见 [包复用指南](https://github.com/Deng-0119/RPNH/blob/main/examples/package_reuse/README_ZH.md)；其原 lock 对应原插件，不可给改名 B
复用。保留 A 的报告、wheel、lock 和 run；版本差异是事实，不能自动推断向后兼容。
Registry metadata 与插件 catalog 仍是不同权威。
