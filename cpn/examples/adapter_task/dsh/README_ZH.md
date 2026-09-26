# 固定版本 DSH 宿主

准备安装版 RPNH integration 声明的精确上游 checkout。设置 `DSH_SOURCE` 和一个已授权、
与 DSH 兼容的 execution selection；该 profile 保持同一路线和模型，同时须在 DSH 的
2 MiB 完整 frame 中留出空间。

```bash
: "${DSH_SOURCE:?Set the pinned DSH checkout}"
: "${EXECUTION_CONFIG:?Set an authorized DSH-compatible selection}"
RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-dsh-example.XXXXXX")"
rpnh-dsh "$DSH_SOURCE" --execution "$EXECUTION_CONFIG" \
  --root "$RUN_ROOT" --task "$(cat task.txt)"
```

该任务复用 RPNH provider adapter。DSH 不选择其它 provider 或 exact model，也不把该请求
改造成 workflow。它是一个已登记 DSH 宿主 turn；物理调用数须以其 audit 为准。
