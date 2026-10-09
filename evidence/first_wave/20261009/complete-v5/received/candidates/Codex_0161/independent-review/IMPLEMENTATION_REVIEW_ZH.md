# Codex 0.161 candidate 增量：独立代码审查

日期：2026-10-08。最终结论：**显式 candidate 最小增量可交接；独立审查发现的基线和 gate 问题均已闭环，无未关闭阻塞项。生产 adapter 通过静态审查与 34 项离线探针；不能据此宣称 stock/Rust 原生通过或升级默认 pin。**

## 审查范围和真实基线

实际审查的三个生产变更文件：`cpn/frontend/codex_app_server.py`、`codex_history.py`、`codex_compatibility.v1.json`。

最终测试组合为原 `715468d` source subset + native reader `f4726d7…` + effort codec `3be0de65…` + 最终 owner-history `df0c3090…` + d92 两文件产品 overlay。不是完整 `8dd360e…` checkout 的全产品认证。

独审发现并要求修正：初版新 source 直接复制旧 history/source，尚未包含 d92 两文件；其首轮测试不能算作 d92 验证。作者随后补入已交付 `latest-main-overlay/source` 的确切字节，并分别保存前后组合记录。最终独审已核：

- task_control.py: `b8658b6c19d64eb4bc34a9c320c84a9140cab624e236ffe8c8edfcc7bd026729`
- registry/run_authority.py: `7475c2d5403dea49dc0a45796a3f84beab6c7516f18466bc8e1338bf0be684e3`

三个候选生产文件和这两份 overlay 在最终测试前后 SHA256 不变，见 `final-composite-stability.json`。d92 是前置依赖，不是本次三文件增量的新实现。

## 已确认的实现边界

- manifest 内 compatibility_profiles 是唯一版本能力表；Python 从它加载 frozen dataclass 与不可改映射。默认常量仍为 0.155.0；未知 selector 拒绝，环境变量或客户端 initialize 不能选 candidate。
- launcher、binary --version 核验与 server 共享精确 profile；initialize 的 name/version 与 thread.cliVersion 按该 profile 输出。不会自动寻找另一版、下载、安装或提升支持状态。
- object parser 只准 type/itemId 两字段、非空有界 turnId/itemId；仅 0.161 items query 开启。新查询捕 cut 一次，经既有 refs-only projection 解析 exact committed turn，再匹配其原 user/agent slot，生成 native exclusive anchor。
- 固定两 safe slots 的真实边界保留：合法 object 请求最多返回一个 item，nextCursor 为空；非空反向 cursor 为现 v1 string、inclusive、同 cut/turn filter。没有为了多页覆盖新增第三项。
- old opaque strings 继续绑定原 cut/source/query/order/view/filter，跨同 root profile 重开可重验；UUID identity 与 failed ordinal 间隙保持一致。
- 新版历史时间输出 null，resume collaborationMode=null、disabledPluginIds=[]；0.155 wire 仍原样。未扩大 collaboration、plugins、raw item/body、Registry、cursor 存储或授权面。
- 请求仍经过原 owner/source 检查、RO reader、safe renderer 与 send-lock 内重验；未绕过既有权限边界。

## 独立执行结果

解释器：既有 `rpnh-recovery-20261003/source/.venv/bin/python`，pytest 8.4.2。默认 Python 缺 pytest 的初始尝试未运行测试，改用现有 venv，没有安装任何软件。

最终确切组合：`test_delta_independent.py` **34 passed / 0 failed，35.56 秒**。日志 `final-composite-tests.log`，JUnit `final-composite-tests.xml`。

覆盖包括：四个 exclusive 方向 × limit 边界、两个 slot 的空页/反向含锚、新旧 cut 差异且 string 不 recapture、send-lock 后 source/lease/close 失效拒绝、RO query_only 和 authority 字节/epoch 不变、私有字段不泄露、UUID/native ordinal、跨 profile 重开、握手失配无正文、畸形对象与 failed/future/cross-turn/non-slot 拒绝。

首轮同 34 项先出现 33 过/1 探针失败：独立探针在建立第一个 RO SQLite 连接前取文件 fingerprint，合法的空 WAL 创建被误报。修正为先建立 RO/epoch 观察边界后全过，产品无需修改。所有旧组合过程证据存于 `pre-d92/`；最终仍只算 34 个独立测试，重复运行不相加，且不与作者重叠测试数拼成所谓总覆盖。

补全设计的 56/56 上游文件也完成独立本地 SHA256、Git blob 和字节数核对，见 `completed-design-integrity.json`。这属于源码证据一致性，不是 Rust 反序列化执行。

## Native handoff 静态发现与离线闭环

独审要求修复两处新 gate 脚本问题：

1. 初版 logger 将合法 object cursor 送进 opaque HistoryCursor.decode，导致额外 object JSON-RPC probe 在产品前被 logger 拒绝；应安全区分 string/object/null，不输出任意 cursor 字段。
2. 未知或执行 RPC 被拒绝后，TUI 正常退出仍可产生 exit 0，令 pair 继续后续 run；应记录 violation 并使当前 lane 停止，不能把应用退出当 gate 通过。

两处均已修复：object 只记录形状布尔，不尝试 opaque 解码、不输出任意 cursor 内容；拒绝/错误/child violation 即使 client exit 0 也令 gate 返回 2，pair 停止对应 lane，并恢复测试包装。

此外发现初版日志分析器只检查响应 cut 和全集覆盖，未检查请求 cut/continuation 链，可能把错误请求 cursor 的日志标为 coverage complete。作者补入按时间顺序的独立 turns/items token 链验证、request cut 与 resume 绑定、推进和最后 continuation 消耗、跨 items 页去重。

独立新增 5 份纯合成日志探针已验证：合法动态分页通过；错误 request cut/token、非推进 next、跨页重复 items、未消费的最终 continuation 均 INCOMPLETE。结果见 `analyzer-final-probes.json`，保留初版重现 `analyzer-initial-gap.json`。这些是日志工具检查，不能作为生产产品/stock 客户端通过计数。

另独立复跑作者 16 项 fake runner/log 测试：16 passed / 0 failed，0.50 秒，见 `handoff-offline-review.log/.xml`；这是同一组测试的复核，不另加到作者计数。

原生 object probe 被单独命名为 WebSocket RPC 测试，明确非 stock TUI trace；实际 native 运行仍待环境和二进制授权。

目前没有执行任何真实 native runner、Codex binary、Unix socket、登录、模型调用、Actions 或 push。仅静态审查与 fake JSON transport / synthetic Registry / fake runner-log 检查获准并已执行。

## 最终补丁及冻结输入核验

独立重新从旧 history/source 构造 1,041 文件基线，并仅用已冻结 latest-main-overlay/source 的两份 d92 字节覆盖对应路径；每份基线均与最终 BASE_SOURCE_LOCK 匹配。未调用作者 freeze 脚本代替独立检查。

`git apply --check` 与实际 apply 均通过。补丁净 6 文件：3 份生产 adapter/manifest、1 份候选测试、2 份新中英文文档。未把 Reader、effort、owner-history 或 d92 overlay 重新计作本次改动。应用后 1,044 文件的完整路径集合、每份内容与字节数全部匹配最终 manifest/source；原 31 项冻结输入仍未改变。

- patch SHA256: `720bcf2bd95886a26425e00caec80eab0fbcf0eec0481202ca54ce88a5f8a3c8`
- source fingerprint: `316a06aad41653e890d16a19519d07b09042c54c4961586b54cf2842b10d8658`
- 独立证据：`final-patch-verification.json`

已核作者最终 JUnit 分组为 69 candidate + 53 history = 122，以及另 8 项默认定向、16 项 fake/log；独立 34 项与作者可能重叠，不能相加成互斥总覆盖。原生、Rust 构建/反序列化、真实 provider/worker、整个主线产品认证仍明确不在本交接结论内。
