# 工具 Pipeline：本地原生验收与原始证据

**PASS_NATIVE_FINITE**：默认原生 22 项及补充 10 个不同测试通过，共 32 个不同 IDs。镜像 join 重复窗口单独保留，合计 33 次执行；重复不增加覆盖。标准终态为 **1.70 CNY**，逐行 HALF_UP fixture 为 **0.02 CNY**。独立进程仅从 Registry 回读，ordinal 保持 1001，dispatch 保持 10。

执行身份为 `00f2d29c7deffed44e2ec635a24f390c6e0d9ace` 加新增示例。交付的 16 个示例文件保持原字节，集合 SHA-256 `a547de500345f51c102fb197dfabedafc2aa48c251dc19d9e10733cbe2553618`，core 无改动。所有本地运行均使用真实 AF_UNIX OwnerEventLoop，没有 pipe fallback。产品入口见 [中文说明](../../../../examples/tool_pipeline/README_ZH.md) / [English](../../../../examples/tool_pipeline/README.md)。

- [完整本地报告](local/REPORT_ZH.md)、[分层结论](local/LAYERED_MANIFEST.json)、[精确测试清单](local/native-test-inventory.json)。
- [事件、lineage 与回读审计](local/native-evidence-audit.json)、[补充反例审计](local/supplement-evidence-audit.json)、[独立原生复核](local/review/native-audit-review.md)、[独立反例复核](local/review/supplement-review.md)。
- [完整原始命令与日志](local/logs/)、[补充探针与版本快照](local/probes/)、[本地原生 Registry JSON 导出](native/)。
- [原云端 AF_UNIX 权限失败](cloud-handoff/evidence/native-attempt.log)、[云端分层身份](cloud-handoff/EVIDENCE_MANIFEST.json)、[旧探索性部分完成记录](cloud-handoff/review/independent-results.json)。它们保持原始历史边界；云端 pipe PASS 不冒充原生通过，取消或空日志不补造结果。
- [逐文件原件/导出身份及转换记录](MANIFEST.json)。完整云端交付包文本逐字节保留；本地公开副本仅替换私有工作区前缀为 `<WORKSPACE>`，原件与 Registry stores 仍在本地。

两个 read 体在不同 worker 同时进入，并与 Registry 精确 active firing 对应；两侧 join 输入缺失均阻断准入。错误数据、错源版本和伪造 parents 不产生 validated/final/publish；未决 firing 保留，重建 Harness 不重放。并行容量由既有 Harness 限制，独立整数 validator 从原始 Wh/fen 重算。

这是离线合成、受信 HOST 示例的有限验收。RPNH 模型计数全部 `[0,0]`，未运行真实 provider、Docker、Actions 或外部业务 API；没有外部 I/O 加速、通用 async API、managed-plugin/action receipts、自动 Python lowering、通用中断恢复或同长度篡改检测的结论。数据库、配置凭据、环境缓存和 ZIP 未上传。
