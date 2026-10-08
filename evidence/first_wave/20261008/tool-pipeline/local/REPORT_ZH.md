# 工具 Pipeline 本地原生补验

结论：**PASS_NATIVE_FINITE**。默认原生测试 22/22 通过，补充 10 个不同原生测试通过，标准、逐行舍入 CLI 和独立进程 Registry 只读回读均通过。没有发现需要修改产品实现的问题；新增示例的 16 个文件保持交付包字节，core 无改动。

本地开始时产品 HEAD 为 `e92b05c9afe324ebb675f2d67b73c02efe7b9536`，工作树干净。核对 live origin/main 后，仅快进两份技术报告到包内核验基线 `00f2d29c7deffed44e2ec635a24f390c6e0d9ace`。所有本轮运行均在该 HEAD 加新增示例的状态执行。基线 commit tree 为 `c951267ed6ce5f563f69aafd5f695e30625cff40`，cpn tree 为 `27aef45c5ff39e543ce3a683a5a5654921cda3d4`；16 文件集合 SHA-256 为 `a547de500345f51c102fb197dfabedafc2aa48c251dc19d9e10733cbe2553618`。逐文件身份见 `tested-source.json`，原包 107 项校验均通过。

环境为 Linux/WSL2 6.6.87.2、Python 3.13.12、pytest 8.4.2，core 从权威 checkout 导入。复用已有系统依赖，并在工作区专用环境安装符合 pyproject 范围的 pytest；未修改系统安全配置。所有运行、临时文件和缓存均在工作区内、产品仓库外。原生 bind/listen/socketpair 初检通过，CLI 和测试使用真实 OwnerEventLoop，没有 pipe fallback 或 socket monkeypatch。环境与完整命令、stdout/stderr、起止时间、退出码在 `logs/` 和 `probes/`。

| 验收层 | 结果 | 证据 |
| --- | --- | --- |
| 云端静态与显式 pipe 语义 | 历史 PASS，22 个不同用例，cleanup 复测 9 项重叠 | 原包 EVIDENCE_MANIFEST 与原日志，未重标为原生 |
| 云端原生 AF_UNIX | 历史 BLOCKED，EPERM，退出 2 | 原包 native-attempt.log，原始阻断保持 |
| 本地默认原生 suite | PASS，22 项，351.320 秒 | logs/native-tests.junit.xml、完整 collect 列表 |
| 本地补充反例与单 worker | PASS，9 项，358.300 秒 | probes/extra-native.junit.xml 与各 Registry 导出 |
| 镜像缺 usage 的 AND-join | PASS，1 个不同用例，两次窗口 77.376 / 75.583 秒 | probes/mirror-native*.xml、held-e 与 observations |
| 标准与逐行舍入 CLI | PASS，1.70 / 0.02 CNY | logs/native-standard、native-rounding，原生导出 |
| 独立进程只读回读 | PASS，稳定字段完全一致，无新增事件或 dispatch | readback-protocol、before/after、native-evidence-audit |
| 真实 provider/model、Docker、Actions、外部业务 API | NOT_RUN | 全部本地运行 Registry 模型计数 [0,0]，任务不含这些调用 |

本地共 **32 个不同 pytest IDs、33 次执行**。镜像重复不增加覆盖；两次 CLI、回读与 JSON 审计也不计作额外 pytest 用例。原 9 项补充测试运行时已载入的版本单独冻结为 `test_native_boundaries.original9.py`，后追加镜像的 10 项版本另存，运行身份不以较晚磁盘内容替代。完整清单见 `native-test-inventory.json`。

标准运行的真实 terminal `run_outcome=complete`，10 个 firing、12 个业务产物及 2 个原始输入资源；最终报告为 2.000 kWh、1.20 + 0.50 = 1.70 CNY。舍入运行两行各 0.01 CNY，总额 0.02。成功依据精确终态资源，而非 report 文件存在或 validated 布尔字段。

受控运行在 ordinal 389 时，两个 read 函数体已在不同 worker 进入、均未返回，对应两个精确 active firing。usage read/check/normalize 在 403/468/530 settled；542 时 tariff read 仍阻塞，join 未 enabled。tariff read 在 556、normalize 在 683 settled，join 直到 698 才 admitted。标准、舍入、受控三次运行合计 30 个 firing / 36 个输出，逐项核对 admission、transition Start、operation Start、所有输出注册、completion、completed settlement 的顺序；claims、业务 parents、Registry derived_from、producer、binding 和 schema authority 均一致。独立复核见 `review/native-audit-review.md`。

标准 Python 进程已结束，旧 export 移到保留目录后，另起 CLI 进程仅以 persisted Registry 为输入重建导出。报告、source/candidate/validation 精确 refs、全部 lineage、checkpoint、firings/events/projection 完全一致；只缺运行时附加的 stop_reason/transport。前后 max ordinal / event count 均为 1001，dispatch / execution Start 均为 10，模型计数 [0,0]，没有再次执行工具。旧导出未删除、未传给回读进程。

错误单位、时间、两路同时错误及 1.71 候选由默认原生 suite 验证；补充负数、float、NaN、重复、重叠、越界、schema-valid 错源版本均经真实 PN 拒绝，没有 validated/final/terminal 或 publish admission。两路 rejection 保留。伪造 parents 在 publication 前触发实际 HOST ValueError，完整预期 traceback 保留；未决 read_usage 只调用一次，重建 Harness 返回 reconciliation_required，无自动重放。单 worker 成功且 Registry 最大 active=1，双 worker 最大 active=2。两侧 join 输入缺失、缺 validated、错误工具 identity/allowed-tool、未 claimed sibling 读取均有原生检查。

这是离线合成示例的有限验收。HOST coroutine 为 example-local ABI；read 消费已交付 Registry artifact，没有外部 I/O 或加速结论。没有新增通用 async API、managed plugin/action receipt、任意 Python 自动转 PN、run_tool_program 自动 lowering、通用中断恢复或同长度磁盘篡改检测。本次未改变既有 ERP/SCB 实验分数，也未重新运行真实模型实验。

原云端失败、旧版本探索性部分完成/取消记录与空原日志均完整保留，不将其写成当前 PASS。GitHub 交付依用户既有“完成后推送”授权；包内默认不 push 的文字保留为历史任务说明。公开材料仅包含示例源码、任务相关文本日志及 Registry JSON 导出；数据库、WAL/SHM、配置凭据、venv/cache、ZIP 留在本地。公开本地副本仅替换私有工作区前缀，逐文件原件/导出 SHA 与替换次数在发布 MANIFEST；原始字节仍可本地复核。
