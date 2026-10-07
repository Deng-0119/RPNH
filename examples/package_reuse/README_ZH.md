# 用同一个真实 v2 包完成三条环境准备路径

[English](README.md) | 中文

本例让**同一个 `native-add-v2.zip`、包锁和 `main` 入口**分别经过已有环境、新 venv、
操作说明三条路径。真实 Module 来自公开 `build_plugin_module` API，调用现有
`demo/add` 插件，没有手写一个等价工作流代替原包。

预期业务结果为 `{"value":5}`，有已注册的终态/结果引用，且
`actual_model_call_counts: [0,0]`。不需要 provider、API key、账号、数值计算扩展或
外部服务。环境准备成功本身不是这个业务结果。

## 1. 在源码目录之外开始

使用 Linux/WSL2、Bash 和 CPython 3.11+。需要已经安装 **0.1.0rc2 候选版或更新版本**
的 RPNH，以及当时安装的精确本地 wheel。旧公开 rc1 二进制不含这条路径。
从所选 release/candidate 获取 wheel；如果使用源码，先在源码目录运行
`python -m pip wheel --no-deps --wheel-dir dist .` 并保留生成的 wheel。
这里不表示 rc2 已经公开发布。

激活已安装 RPNH 的 Python，在新的工作目录运行以下命令。出现提示时，输入
你实际安装的 wheel 的绝对路径：

```bash
set -e
read -r -p 'Absolute path to your installed RPNH wheel: ' RPNH_WHEEL
test -f "$RPNH_WHEEL" || exit 1
WORK="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-package.XXXXXX")"
rpnh examples export --example package_reuse --output "$WORK/tutorial"
cd "$WORK/tutorial"
SAMPLE="$PWD/examples/package_reuse"
CONTROL_PYTHON="$(python -c 'import sys; print(sys.executable)')"
mkdir "$WORK/wheelhouse"
python -m pip download --only-binary=:all: --dest "$WORK/wheelhouse" "$RPNH_WHEEL"
python -m pip wheel --no-deps --wheel-dir "$WORK/wheelhouse" ./examples/native_plugin
WHEELS=()
for wheel in "$WORK"/wheelhouse/*.whl; do WHEELS+=(--wheel "$wheel"); done
MATERIAL=(--archive "$SAMPLE/native-add-v2.zip" --lock "$SAMPLE/native-add-v2.lock.json" --entry main)
rpnh package preview "$SAMPLE/native-add-v2.zip"
rpnh package resolve "$SAMPLE/native-add-v2.zip" > "$WORK/recomputed-package-lock.json"
cmp "$SAMPLE/native-add-v2.lock.json" "$WORK/recomputed-package-lock.json"
```

只有上面的 pip 获取/构建步骤会使用包索引。包解析器和准备安装器只使用明确传入的
本地 wheel 字节，不会下载或偷偷补充缺失依赖。离线时，从经过批准的 wheel 缓存
提供同样的完整闭包即可。

导出的 `native_plugin` 是本例依赖的现有可信 demo。其标准 wheel 构建会加入惰性
`.dist-info/rpnh_environment_plugins.json` 声明，使未安装插件也可解析。
这份声明不会执行插件工厂。

### 文件和依赖闭包

- `native-add-v2.zip`：惰性 v2 Module、精确 schema、环境需求和 MIT 许可
- `native-add-v2.lock.json`：精确根包锁，不依赖其他 sharepackage
- `plugins.json`：接收方明确选择的可信 `demo` 插件，版本 `0.3.0`，配置为空
- `owner-request.json`：完整 owner 请求，包含两个输入角色、预算和无模型条件
- `selection-existing.template.json` / `selection-new-venv.template.json`：完整可审阅模板；`select_environment.py` 填入真实路径和精确包目标，无需手抄摘要
- `expected-output.json`：原始输入的预期结果，只是断言材料，不是执行证据
- `prepared_binding.py`、`verify_result.py`：读取公开数据的工具，不运行工作流
- `build_package.py`：可选作者工具，使用已安装公开原生插件/包 API；接收方不必运行它

声明根依赖是 `rpnh-harness>=0.1.0rc2,<1` 和 `rpnh-native-demo==0.3.0`。
HOST 为内置 `rpnh-native/v1`，插件 API 为 `rpnh/plugin/v1`。
Harness 直接依赖 `jsonschema>=4.20,<5`、`packaging>=24,<27`、
`websockets>=12,<16`；具体解析还包含实际生效的传递依赖，如 `attrs`、
`jsonschema-specifications`、`referencing`、`rpds-py`，以及所选 metadata 需要时的
`typing-extensions`。精确版本、wheel 哈希和真正生效的闭包记录在各路径的
`resolution.json`，不是从此列表猜测。`setuptools>=77` 仅用于 pip 隔离构建后端，
不是业务 Module 的依赖。

## 2A. 使用已有环境

第一次体验可先建立用户自己的已有环境，只安装同一个 harness 及其声明依赖。
如果要使用现有环境，把 `EXISTING_PYTHON` 改为其 Python 的绝对路径，并跳过
创建和安装命令。批准前审阅计划中的所有变更。

```bash
python3 -m venv "$WORK/existing-python"
EXISTING_PYTHON="$WORK/existing-python/bin/python"
"$EXISTING_PYTHON" -m pip install --no-index --find-links "$WORK/wheelhouse" "$RPNH_WHEEL"
ROUTE="$WORK/existing"
mkdir "$ROUTE"
python "$SAMPLE/select_environment.py" --python "$EXISTING_PYTHON" --output "$ROUTE/selection.json"
```

然后执行第 3 节。首次检查通常返回 `3`，因为 demo 插件还没安装。
精确计划应添加 `rpnh-native-demo`，再装配真实 HOST，不应替换已有 harness。
如果你选的环境需要替换已有包，先检查冲突；这些变更需要显式使用
`--allow-existing-changes` 重新解析，并重新批准计划。

## 2B. 从精确 wheel 创建新 venv

保持控制端环境已激活。不要预先创建 `new-python`；这由经过明确批准的准备动作
负责。先设置以下变量，再执行第 3 节：

```bash
ROUTE="$WORK/new-venv"
mkdir "$ROUTE"
python "$SAMPLE/select_environment.py" --python "$CONTROL_PYTHON" \
  --prefix "$WORK/new-python" --output "$ROUTE/selection.json"
```

初次检查会因目标不存在返回 `3`。提供完整本地 wheel 闭包后，解析和计划应解除
阻塞。批准的计划创建指定 venv、安装声明闭包，并在其中执行真正已安装 HOST 的
装配；不会把基础 Python 当作已经准备好的目标。

## 2C. 把同一计划交给人员或本地 agent

选另一个尚不存在的目标，形成独立接收方路径，而不是复用前一条路径的 receipt：

```bash
ROUTE="$WORK/setup-document"
mkdir "$ROUTE"
python "$SAMPLE/select_environment.py" --python "$CONTROL_PYTHON" \
  --prefix "$WORK/setup-python" --output "$ROUTE/selection.json"
```

执行第 3 节至 `setup-instructions`，再把 `setup.txt`、精确 plan、selection、
resolution、包材料和 wheel 路径交给已获授权的本地操作者。对方必须审阅材料，
然后执行下方同一个交互式 `prepare-environment` 命令。
文档本身不授予权限；打勾或文字声称完成不能代替真实检查器和 HOST 装配。
如果机器或路径变化，在目标机重新生成 selection/check/resolution/plan，批准新计划。
ZIP 和包锁仍保持不变。

## 3. 检查、解析、审阅并准备所选路径

每选择一条路径，执行一次本节。初始检查受阻是预期行为；状态码不是 `0` 或 `3`
时必须停止。

```bash
if rpnh package check-environment "${MATERIAL[@]}" \
  --selection "$ROUTE/selection.json" --output "$ROUTE/check.json"; then
  CHECK_STATUS=0
else
  CHECK_STATUS=$?
fi
test "$CHECK_STATUS" -eq 0 -o "$CHECK_STATUS" -eq 3 || exit "$CHECK_STATUS"
rpnh package resolve-environment "${MATERIAL[@]}" \
  --selection "$ROUTE/selection.json" --check "$ROUTE/check.json" \
  "${WHEELS[@]}" --output "$ROUTE/resolution.json"
rpnh package plan-environment "${MATERIAL[@]}" \
  --selection "$ROUTE/selection.json" --check "$ROUTE/check.json" \
  --resolved-selections "$ROUTE/resolution.json" "${WHEELS[@]}" --output "$ROUTE/plan.json"
rpnh package setup-instructions --plan "$ROUTE/plan.json" --format text --output "$ROUTE/setup.txt"
cat "$ROUTE/setup.txt"
rpnh package prepare-environment "${MATERIAL[@]}" \
  --selection "$ROUTE/selection.json" --check "$ROUTE/check.json" \
  --resolved-selections "$ROUTE/resolution.json" --plan "$ROUTE/plan.json" \
  --state-dir "$ROUTE/state" --output "$ROUTE/receipt.json"
BINDING="$(python "$SAMPLE/prepared_binding.py" --receipt "$ROUTE/receipt.json" --state-dir "$ROUTE/state")"
rpnh package check-environment "${MATERIAL[@]}" --binding "$BINDING" \
  --resolved-selections "$ROUTE/resolution.json" --output "$ROUTE/after-check.json"
```

审阅屏幕展示的精确动作和路径，仅在同意时输入计划摘要。必须在交互式终端运行；
管道传入 `yes`、JSON 中添加 `approved` 或保存摘要文件都不是授权。

验收条件：resolve/plan 的 `unresolved: []`；receipt 的 `failure: null`，包含
`host_declarations_digest`、绑定的解释器和已完成的 HOST 装配；after-check 为
`passed_for_checked_scope`，但 `execution_permitted: false`。
state 目录存储以摘要命名的不可变证据。`prepared_binding.py` 只选成功 receipt
真正指向的 binding。

只读命令仅写入明确指定的输出，拒绝覆盖已有文件。重新尝试时使用新的 `WORK`
目录；失败后保留局部环境和证据供诊断，不删除或暗中复用。

## 4. 授权业务运行，并验证真实结果

仅在第 3 节通过后继续。`run` 需要单独交互授权：审阅完整 owner 请求和尚不存在
的运行目录，再输入显示的身份摘要。批准准备计划不等于批准业务运行。

```bash
RUN_DIR="$ROUTE/run"
rpnh package run "${MATERIAL[@]}" --binding "$BINDING" \
  --receipt "$ROUTE/receipt.json" --resolved-selections "$ROUTE/resolution.json" \
  --owner-request "$SAMPLE/owner-request.json" --run-dir "$RUN_DIR" \
  --include-terminal-result --output "$ROUTE/run-result.json"
python "$SAMPLE/verify_result.py" --result "$ROUTE/run-result.json"
rpnh net --run "$RUN_DIR"
rpnh net --run "$RUN_DIR" --show-resources
rpnh net --run "$RUN_DIR" --view --no-open
```

校验器要求精确包目标、run/task/net/terminal 引用、`stop_reason: terminal`、
可用的已注册 JSON 输出 `{"value":5}` 及其内容身份，以及零模型调用。
它读取真正 owner 命令明确保存的私有结果文件，不能为手工编造的 JSON 认证。
普通 stdout 仍只展示引用和计数；结果字节只写入明确选择的私有 `--output` 文件。

查看器会打印本地 loopback URL，请在自己的浏览器打开。查看 request → operation →
result、已完成操作及其 checkpoint/token 引用；**Show resources** 会显示注册的
插件能力。查看器只读，不授予执行权限，图像也不能单独证明业务结果。按 Ctrl-C
停止查看器服务。

如果主机拒绝 AF_UNIX owner socket，真实 owner 就无法运行。保留
`ENVIRONMENT_OWNER_CONTROL_UNAVAILABLE`，报告 **BUSINESS_TERMINAL_NOT_VERIFIED**。
编译 Module、创建 venv 或 Registry 都不是业务完成。没有假 worker 或替代传输回退。
在支持普通 owner socket 的本地 Linux/WSL2 上运行同样命令；保持包字节不变，
在那里重新生成接收路径和计划，再给予授权。

## 5. 复用同一个包处理自己的输入

只修改 owner 输入，所以三条路径的精确包目标保持不变。创建独立请求、预期结果
文件，并使用新的运行目录：

```bash
python - "$SAMPLE/owner-request.json" "$ROUTE" <<'PY'
import json, pathlib, sys
request = json.loads(pathlib.Path(sys.argv[1]).read_text())
for item in (request['task_input'], request['entry_inputs']['request']):
    item['payload'] = {'left': 12, 'right': 8}
    item['summary'] = 'Add the supplied integers 12 and 8'
request['command_id'] = 'package-reuse-native-add-custom'
root = pathlib.Path(sys.argv[2])
with (root / 'custom-owner.json').open('x') as f: json.dump(request, f)
with (root / 'custom-expected.json').open('x') as f: json.dump({'value': 20}, f)
PY
rpnh package run "${MATERIAL[@]}" --binding "$BINDING" \
  --receipt "$ROUTE/receipt.json" --resolved-selections "$ROUTE/resolution.json" \
  --owner-request "$ROUTE/custom-owner.json" --run-dir "$ROUTE/custom-run" \
  --include-terminal-result --output "$ROUTE/custom-result.json"
python "$SAMPLE/verify_result.py" --result "$ROUTE/custom-result.json" --expected "$ROUTE/custom-expected.json"
```

两个整数都必须在 -1,000,000,000 到 1,000,000,000 之间。自定义预期结果为 `20`，
不能继续用原始 `5` 的断言文件验证。

图结构、schema、插件配置、资源字节或插件实现变化都属于材料变更。
不能直接改 ZIP 却继续使用旧 manifest/lock/binding。在自己导出的作者副本修改，
为变更后的插件代码/资源更新版本，重新构建并安装 wheel，更新显式配置和需求，
再生成新的包与包锁。针对新目标重新执行 selection/check/resolve/plan/prepare。
`build_package.py` 展示了这个固定 `demo/add` 示例的真实公开 SDK 作者流程：

```bash
python -m pip install --no-index --find-links "$WORK/wheelhouse" rpnh-native-demo==0.3.0
python "$SAMPLE/build_package.py" --output "$WORK/rebuilt-stock-package"
cmp "$SAMPLE/native-add-v2.zip" "$WORK/rebuilt-stock-package/native-add-v2.zip"
```

可选的原始包重建必须逐字节相同。此脚本不是通用图编辑器；作者改动应落实到其
真实 Module 和依赖声明。包身份、本地环境锁和私有运行证据彼此独立。
不要把本地 selection、凭证、receipt 或 Registry 路径放进公开 sharepackage。
