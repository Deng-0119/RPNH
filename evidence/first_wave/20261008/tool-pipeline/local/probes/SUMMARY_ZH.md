# 工具 Pipeline 补充原生验收

**PASS：10 个唯一 test ID；共 11 次测试实例，镜像两次窗口只计一项。**

原 9 项：9 passed in 358.30s；命令进程 exit=0。镜像首次 x/m：1 passed in 77.37s；指定全新 y：1 passed in 75.58s；均 exit=0。三个窗口完整命令、UTC 起止时间、elapsed、环境、stdout/stderr、JUnit 见同目录各自 receipt 和原始日志。

固定产品 HEAD：`00f2d29c7deffed44e2ec635a24f390c6e0d9ace`；commit tree：`c951267ed6ce5f563f69aafd5f695e30625cff40`。本地 origin/main 引用相同，未联网刷新。前后状态均为 `?? examples/tool_pipeline/`；16 文件逐字节与输入 manifest 一致，没有产品修改。

产品 source_set_sha256：`a547de500345f51c102fb197dfabedafc2aa48c251dc19d9e10733cbe2553618`。逐文件 SHA-256、大小和全部路径见 SUPPLEMENT_RESULT.json / source_identity.json。

原 9 项实际加载字节保存为 `test_native_boundaries.original9.py`，SHA-256：`88916bf8784b1dd13b88175376fc1a8c71d56312ae1579fb7991720e08e290d2`。该版本在启动前记录，追加镜像后从未变动的原始前缀精确保全，hash 匹配。

镜像加载的 10 项版本保存为 `test_native_boundaries.mirror10.py`，与当前 `test_native_boundaries.py` 字节一致，SHA-256：`27ba623bd1d8226a0e34ff6928f1507bb2b0ac0d505f6356bcea6e865f22edef`。镜像只定向执行自身 ID；y 的源码 hash 在 subprocess 启动前捕获。

| 精确 test ID（文件均为 test_native_boundaries.py） | 状态 | 验收结果 |
|---|---|---|
| `test_usage_mutations_native[negative]` | PASS | 真实 PN 的 check_usage_input 拒绝；无 validated/final/terminal 或 publish firing |
| `test_usage_mutations_native[float]` | PASS | 真实 PN 的 check_usage_input 拒绝；无 validated/final/terminal 或 publish firing |
| `test_usage_mutations_native[nan]` | PASS | 真实 PN 的 check_usage_input 拒绝；无 validated/final/terminal 或 publish firing |
| `test_usage_mutations_native[duplicate]` | PASS | 真实 PN 的 check_usage_input 拒绝；无 validated/final/terminal 或 publish firing |
| `test_usage_mutations_native[overlap]` | PASS | 真实 PN 的 check_usage_input 拒绝；无 validated/final/terminal 或 publish firing |
| `test_usage_mutations_native[too_large]` | PASS | 真实 PN 的 check_usage_input 拒绝；无 validated/final/terminal 或 publish firing |
| `test_source_version_native` | PASS | schema-valid 错源版本候选已注册，由 validate_report 拒绝；无发布 |
| `test_forged_parents_native` | PASS | HOST 发布前 ValueError；完整预期 traceback；read_usage=1；重建 reconciliation_required |
| `test_single_worker_native` | PASS | 终态 complete / 1.70；10 firings、12 resources；active 最大 1、最终 0 |
| `test_missing_usage_join_native` | PASS | tariff 已归一化、精确 usage firing active；join disabled / admit=None；ordinal 542→542；释放后终态 1.70 |

每个反例均保留原始 Registry 和 inspect/readback 导出，所有结果模型计数 `[0,0]`。路径见 JSON 的 cases 字段。预期 HOST 错误完整保存于 `.p26/x/parents/expected.traceback.txt`，未裁剪或当成非预期失败；没有失败重试或 AF_UNIX 权限阻断。

伪造 parents 的准确恢复顺序：原 Harness 先排空已在途 tariff read，再抛出 ValueError；重建的真实 Harness 执行 tariff check/normalize，最终 reconciliation_required。它没有重新调用 read_usage，也不表示成功终态或 OS 崩溃恢复。前后 Registry 导出和 rebuild.json 均保留。

镜像证据：指定窗口 `.p26/y/mirror/{held-e,e}/evidence.json`、`observations.json` 和 `r/`；先前 `.p26/x/m/mirror/` 保留为独立执行窗口。原 9 项目录 `.p26/x/{negative,float,nan,duplicate,overlap,too_large,source,parents,single}/`。自有 socket 已关闭，无残留。

只新增 probes 下的探针、执行记录、原始源码快照和摘要；生成运行证据仅在获准 x/y 目录。无产品修复、commit/push、外部调用、模型/provider、Docker/Actions、pipe import、socket patch 或进一步测试。父任务默认 22 项、标准/舍入 CLI、跨进程回读及逐 firing 审计不计入本结果。
