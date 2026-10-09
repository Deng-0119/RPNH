# 本地命令与来源

以下是在授权独立 checkout 中的命令模板。先将 PYTHON 设置为已有环境解释器的绝对路径，将 BUNDLE、REPO、RESULTS 设置为已核实目录；RESULTS 在源码树外。不得把方括号占位符直接执行。原作者用的云端解释器路径不适用于本地。

## 包与应用前检查（不运行产品）

```sh
"$PYTHON" "$BUNDLE/verify_package.py"
"$PYTHON" "$BUNDLE/verify_patch.py" "$REPO"
```

上述通过且该隔离 checkout 已获授权后，才可：

```sh
cd "$REPO"
git apply --check "$BUNDLE/codex-effort-wire-codec.patch"
git apply "$BUNDLE/codex-effort-wire-codec.patch"
"$PYTHON" "$BUNDLE/verify_package.py" --candidate "$REPO"
```

若已应用 patch，使用 --candidate 检查最终 bytes，不重复应用。不要用 --3way/--reject 或自动解决不同基线；保持固定 patch。

## 既有离线回归

源码：作者 COMMANDS.md、review/REVIEW_ZH.md、tests/test_codex_compat.py、tests/test_frontend_boundary.py。先确认已有 pytest/jsonschema/websockets，缺失就 NOTRUN，不为本任务安装。

```sh
cd "$REPO"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "$PYTHON" -m pytest -q tests/test_codex_compat.py tests/test_frontend_boundary.py --junitxml="$RESULTS/affected-all.xml"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "$PYTHON" -m pytest -q "$BUNDLE/review/test_review_codec_boundaries.py" --junitxml="$RESULTS/supplemental.xml"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "$PYTHON" -m pytest -q tests/test_frontend_boundary.py::test_codex_frontend_uses_external_socket_for_absent_long_root --junitxml="$RESULTS/af-unix.xml"
```

逐条保存完整 argv、cwd、开始/结束时间、exit/stdout/stderr，不用管道隐藏 pytest 退出码。第一条预计 44 个 collected cases，但以实际 collection/JUnit 为准。单列 socket 项与完整集中的该项属于重复覆盖，不可相加为不同测试。

云端历史最终命令明确排除了 socket 项，得到 43 passed/1 deselected。若本地仍不能执行 socket，保留原失败/阻塞，必要时再运行历史 deselect 命令并与完整运行分开报告：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "$PYTHON" -m pytest -q tests/test_codex_compat.py tests/test_frontend_boundary.py --deselect=tests/test_frontend_boundary.py::test_codex_frontend_uses_external_socket_for_absent_long_root --junitxml="$RESULTS/affected-with-explicit-deselect.xml"
```

## 原生客户端命令从受支持入口取得

已授权预置 binary 的版本探针源自 resolve_codex_binary：

```sh
"$CODEX_BIN" --version
```

本包不提供未经核验的 TUI 私有参数或“安全 smoke”快捷命令。真实 launcher 是 cpn/frontend/codex_app_server.py 的 run_codex_frontend / codex_frontend_argv；产品文档中的 `rpnh --frontend codex` 会进入会话并可能执行模型，不能直接当零调用测试。只有任务书的配置隔离与 fail-closed gate 成立，才可用已支持的验证入口让该 launcher 启动 stock TUI。返回实际展开 argv 和入口来源；无安全入口则 NOTRUN。不得通过改常量/initialize/version gate 运行 0.161。
