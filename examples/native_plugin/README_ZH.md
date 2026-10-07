# 原生工具与登记资源示例

[English](README.md) | 中文

可从源码根目录运行，或先获取安装版的可修改副本：

```bash
rpnh examples export --example native_plugin --output /absolute/absent/my-native
cd /absolute/absent/my-native
```

下方 editable 安装会使用导出的 `rpnh_demo.py`，之后可修改 handler。导出不会安装或执行插件；实际运行即使零模型调用，也需要支持的本地进程与 Unix-domain socket 权限。

在源码或导出根目录把此包安装到 RPNH 所在环境。以下每个 `RUN_DIR` 在命令启动前都必须不存在。

下图是 `demo/add` 完成后的实际 PetriNet。隐藏资源计数表明可通过 **Show resources**
显示一个已声明的插件 capability；当前可见图是普通的请求—operation—结果路径。

![已完成原生插件 PetriNet](assets/native-plugin-petrinet.png)

```bash
python -m pip install -e ./examples/native_plugin
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

完整步骤和清理说明见[新用户案例指南](https://github.com/Deng-0119/RPNH/blob/main/docs/guides/examples_ZH.md)；通用插件契约和
信任边界见[自定义指南](https://github.com/Deng-0119/RPNH/blob/main/docs/guides/customization_ZH.md)。
