# H7 后续原生接线任务书（尚未执行）

本文件是后续工程与验收说明，不授予本机、原生进程或对外访问权限。本候选只完成窄 Registry 核心离线切片。H7 全量 D0 未完成；D1、H7b 与 consumer migration 均未执行。

## 中间候选与后续分工

本包是可继续构建的中间核心候选，不是交给本地环境即可跑完 H7 的完整实现。后续每个增量都必须基于 `SOURCE_IDENTITY.json` 钉住的 source 与 S1 输入，单独冻结变更、验证并独审；本包已有 PASS 不能自动覆盖新增实现。

- **云端可继续纯离线推进：** selected-installed material 的数据结构与验证、两类 payload normalizer、原 compiler 的通用机械 lowering、source-qualified 历史 acceptance validator，以及 parent observation/completion 的 Registry 核心校验与纯离线用例。原 wrapper/transport 接线的代码也可先在云端静态实现和受 sentinel 约束的离线测试中推进；测试注入仍不能替代真实原生证据。
- **本地真实原生接线与执行验证：** 在后续明确授权的 Linux 原生环境中完成实际 issuer/Popen、peer/PID/lock、OwnerEventLoop/stop、receipt 交付、物理 target reservation、两类实际 wrapper 运行及真实 child terminal 观察。只有所需实现与离线切片已逐项验收，才能执行对应获准的 D1 窗口；不得凭本核心包直接宣称可跑完整 H7。
- 下面的顺序按依赖关系组织，不表示所有未实现项都必须移到本地完成。任何新增进程、socket、安装、模型/业务访问或其他权限，仍须遵守该阶段明确授权；云端离线工作不因此停下，也不因此获得原生执行权限。

## 起点与不可变约束

- 使用本包 `source/` 的 992 个冻结文件；完整 aggregate 见 `SOURCE_IDENTITY.json`。先运行 `tools/verify_identity.py`，确认 S1 979 文件、S1 patch 与 H7 patch 的身份。不要根据当前 main 假定这些候选已经合并。
- 冻结合同见 `inputs/frozen-contract-v3/`，设计独审见 `inputs/frozen-contract-review/REVIEW.md`。保留原 Registry、PN、Start、claim、事务、writer fencing 为唯一业务 authority；不得新增 scheduler、ledger、ready/allow 表、假 token 池或另一套 Module runner。
- 本包离线 fixture 用 `object.__new__` 注入封闭 evidence，且在注入前创建临时 child Core。它只能证明 Registry 验证行为，不能作为生产 issuer、peer 认证、receipt 交付、Core-before-reservation 或物理 target zero-touch 证据。
- 原生工作只能在用户另行指定并授权的 Linux 环境中执行。未取得授权前继续纯离线实现/审查，不开 socket、PTY、Popen/native client，不调用模型或业务 API，不安装、不运行 Actions、不 push。不要把本说明当作授权，也不要擅自切换到用户电脑。

## 最小实现顺序与逐项交付

1. **安装材料与请求准备。** 两类完整 typed payload normalizer、selected-installed HOST inventory 与公开材料 collector 回接原入口；固定 K → T/task-ID/document-root → normalized D → I → E → sealed bundle，无循环摘要，无 intent 后路径改写。提交 exact material/inventory 身份及 O49/O56 定向证据。
2. **原 transport issuer。** 在原 task lock 内安装 sealed bundle；Registry 新鲜 dispatch commit 只产生一次不可序列化 ticket，replay 不再 mint；原 Popen 返回 observation 由实际边界封闭构造。当前 `_NativeBoundaryEvidence` 不可公开化为 caller receipt、dict、Boolean 或通用构造器。
3. **原 OwnerEventLoop gate。** 固定 command、严格有界 JSON/duplicate-key parser、双向 peer 与 birth tuple、NOT_READY 短返回、owner-entry sticky stop veto、immutable receipt。按合同区分 request 到达、owner 处理、commit、编码/入队、完整接收与 durable stop。signal/异线程只置 pending 并唤醒，不执行事务或等锁/Future。
4. **Core 前物理 reservation。** 完整验证并消费 receipt/permit 后，原子 exclusive mkdir 最终 leaf；持有受信 root/leaf identity 的 typed process-local reservation，在任何 Core/EventStore/SQLite/writer 访问前复核。空、partial、standalone、同 intent 的 EEXIST、symlink、identity 漂移均 fail closed；失败方上述构造/写入计数为 0，既有文件 hash/epoch 不变。崩溃留下 leaf，不补初始化、不接管。
5. **两类真实 wrapper。** AgentTask 和 Module 原 fresh composition 都消费本次 typed reservation，再接 protected bootstrap → origin → 原 runner；legacy absent-only 路径不放宽。实现原 compiler 下的通用机械 lowering，不能用本包单一 test component 证明任意 Module/AgentTask 已接通。
6. **历史接受验证。** 基于 exact source/run/transaction/root/member/producer/Start/epoch-at-cut 构造历史 classifier；canonical copies 是证据材料，不是历史授权 API。不得由“没有 durable stop”推断未发生 SIGINT/Event，也不得把 receipt 用作可重放启动许可。
7. **父端闭环。** exact native child binding、same-cut terminal observation、parent registered completion、ordinary Success 接回原链。目前这些为 UNSUPPORTED；完成之前必须保留 claim/slot，unknown/stopped 不可伪造 successful terminal。
8. **完整 D0 后再 D1。** 根据冻结 `ACCEPTANCE.md` 的 O01–O66 逐项补齐真实输入、负向原子性和 ordinary 对照，独审确认后，再在明确获准的环境执行 N01–N36 的真实双进程/锁/PID/receipt/reservation/stop 窗口。N29–N36 不得用 fixture、shim 或 cold read 替代。H7b 的 R01–R10 另立验收，不与当前计数合并。

## 每轮证据与停止条件

每轮冻结 source/patch/interpreter/Registration/catalog/inventory，保存命令、选择与排除 node IDs、退出码、前后 manifest、JUnit/结果 JSON。分开统计原创用例、去重 testcase、重复执行与历史失败；不得累加成完整 D0 覆盖率。

原生轮须额外记录 Popen attempts、lock wins、acceptance commits、完整 receipt、child bootstrap、origin、business admissions、真实模型/业务请求（应为 0）；记录 K/T/D/I/E、目录身份及失败方 zero-touch 计数；分别记录 stop 的各观察点和 same-cut terminal 证据。不保存秘密。

任何源变化使旧 PASS 不再自动适用；需要独审确证的 blocker 先报告，再修最小边界并重跑相关切片。出现不匹配、不支持、unknown 或缺 evidence 时停止依赖步骤，保留容量与原始日志，不改口称 stopped/interrupted 成功。严禁扩大为多 slot、递归 worker、跨 host、alternate root、自动重启、强杀、清理或 consumer migration。
