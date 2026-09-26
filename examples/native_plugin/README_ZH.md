# 原生工具与登记资源示例

[English](README.md) | 中文

在仓库根目录把此包安装到 RPNH 所在环境。以下每个 `RUN_DIR` 在命令启动前都必须不存在。

```bash
python -m pip install ./examples/native_plugin
rpnh plugins --config examples/native_plugin/plugins.json list
rpnh plugins --config examples/native_plugin/plugins.json check
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-native.XXXXXX")"
rpnh plugins --config examples/native_plugin/plugins.json run demo/add \
  --input examples/native_plugin/input.json --run-dir "$DEMO_ROOT/add-run"
rpnh plugins --config examples/native_plugin/plugins.json run demo/instruction \
  --input examples/native_plugin/instruction-input.json \
  --run-dir "$DEMO_ROOT/instruction-run"
rpnh net --run "$DEMO_ROOT/add-run" --show-resources
```

加法结果的 `output.value` 为 `5`，并带有终端证据且模型调用为零。指令操作返回登记指令
资源的真实身份与版本。`demo/summarize` 由混合案例复用；其直接输入位于
`summary-input.json`。

完整步骤和清理说明见[新用户案例指南](../../docs/guides/examples_ZH.md)；通用插件契约和
信任边界见[自定义指南](../../docs/guides/customization_ZH.md)。
