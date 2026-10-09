# H7：实现待办与未来 gate（非一键实验清单）

## 当前能复验的冻结切片

- S1：原 static lease read 与 exact selection；不是 child origin/物理许可
- core：992-file 原 Registry 因果链及守卫；D0 test-only `_NativeBoundaryEvidence` 不是生产 issuer
- acceptance history：994-file 只读历史分类；VALID 不授予新动作，不成为 receipt
- origin lowering：999-file 机械原 compiler/lowering 与声明冻结；`FrozenBoundChildDeclarations` 不等于 `PreparedChildMaterials`，执行材料请求继续 fail closed

冻结输入/输出及旧失败均在12包中。旧core文档列出的history/lowering待办需结合后继判断，不能重做或把剩余native项顺便划完成。

## 仍需要设计或实现

1. 完整 public inventory：原 selected-installed HostProfile/Registration 的有限公开材料契约、actual exact refs、公有依赖闭包、opaque secret binding及两类完整执行材料准备。最新设计仍待修订，不作为本包实施依据。V5内历史proposal只说明方向，不能当作已批准完备接口。
2. 生产 native issuer：原task-lock、sealed bundle、一次性ticket、原Popen来源观察与不可伪造的进程内证据。
3. 原OwnerEventLoop双向peer认证、严格有界receipt、sticky stop与实际收发窗口。
4. Core/EventStore/SQLite之前的typed exclusive target reservation、identity重检与失败方zero-touch。
5. 实际AgentTask与Module原运行wrapper消费同一typed reservation与protected bootstrap/origin。
6. exact child binding、same-cut terminal observation、parent registered completion与ordinary Success闭环。

这些都不是“本地多跑一次”能关闭的缺口。当前任务先报告 `UNIMPLEMENTED`，没有代码时不能编造测试通过，也不得为了测通开放generic Success/recovery、清除claim/slot。

## 工程完成后才进入的验证

逐项建立O01–O66 evidence mapping并独审；之后只在获准且条件具备的Linux原生环境验N01–N36。真实kernel peer、receipt交付、physical reservation、worker/Popen/竞态必须是实际接口，不能由fixture/shim/cold-read替代。H7b observation-only recovery R01–R10与H8消费者迁移独立验收。

本包不包含新的public-material候选，不推进新功能；缺失设计/实现交回云端工程后再发新冻结增量。
