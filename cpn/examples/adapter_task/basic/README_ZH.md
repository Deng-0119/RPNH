# Basic 宿主

[English](README.md) | 中文

前置条件：将 `EXECUTION_CONFIG` 设为一个已授权 RPNH execution selection 的绝对路径。
下列命令会启动一个真实主会话逻辑 turn：

下图是验收运行已结算后的实际 PetriNet，展示共用的 RPNH 执行层，而不是 Basic 终端截图。

![Basic 验收 turn 的 PetriNet](../assets/basic-petrinet.png)

```bash
: "${EXECUTION_CONFIG:?Set an authorized execution selection}"
RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-basic-example.XXXXXX")/session"
rpnh --frontend basic --execution "$EXECUTION_CONFIG" \
  --session-dir "$RUN_ROOT" --prompt "$(cat task.txt)"
```

可用 `rpnh net --run RUN_DIR` 查看主回合的运行网；`RUN_DIR` 必须取自会话登记的 attempt，
不能自行猜测。一个逻辑 turn 可能包含多次物理 generation submission；应读取
provider-private audit，不能根据 prompt 数量推断物理调用数。
