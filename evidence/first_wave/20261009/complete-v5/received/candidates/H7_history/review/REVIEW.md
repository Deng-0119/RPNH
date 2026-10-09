# Acceptance history 独立审阅

## 裁定

**APPROVE_NARROW_D0_UNMERGED_CANDIDATE**。只批准下述冻结字节的 source-qualified、只读 historical mechanical acceptance 切片；本轮独立 51 个最终 pytest node IDs 全部通过。没有批准 fresh permission、receipt 交付、native issuer、子进程创建或完整 H7。作者的最终 139/68/40 测试由其单独报告，不借用其通过数作为本轮独审实绩。

## 固定输入及实际复核

- 输入 H7 core：992 files，manifest SHA-256 `ac68327e442b7bda8a6aa2ba93c0cd20ad72181a661b0497627b08713334907e`。
- 最终 source：994 files，manifest SHA-256 `a3af1d85ffbc8acac72e719fed316ed3c848229fce92026c62e571cb77a913a2`。
- overlay patch SHA-256：`b8ad5b5c18346cf9fa71cd17c96d17deb67ecbb336329d83c78c271f521bedfa`。
- 独审自建 round3 源副本；前后全文件 manifest 相同，且与作者最终 source 全文件相同。未修改作者 source。
- 另从全文件核实的 992-file 原输入自建干净副本，独立 `git apply --check`、应用上述 patch，所得 994-file manifest 与最终 source 精确相同。该步骤只做本地文件变换，无 fetch、push 或远程 HEAD 声称。
- 相对输入仅两改两增：修改 `cpn/rpnh/registry/event_store.py`、`cpn/rpnh/registry/parent_child.py`；新增 `cpn/rpnh/registry/acceptance_history.py`、`tests/test_acceptance_history.py`。原 schema/catalog、S1 和其他输入字节未改变。

身份证据：`evidence/ROUND3_SOURCE_BEFORE.json`、`ROUND3_SOURCE_AFTER.json`、`AUTHOR_SOURCE_FINAL_CROSSCHECK.json`、`PATCH_INPUT_MANIFEST.json`、`PATCH_APPLIED_MANIFEST.json`、`patch-apply.exit`。

## 已验边界

1. 原 source/binding/run/task、完整 acceptance transaction 与 commit cut、四 phase 顺序和各自 cut、原 physical stream heads/outbox、历史 writer epoch 与 lease/native epoch相符。重建 cut 不掩盖已有原行损坏。
2. 原 root/admission/invocation、临时成员缺失/错属/多余孤立项、Start transaction、schema bytes 与因果边界在本次测试范围内关闭；不会只看提交者复制的 descriptor 或 bool。
3. shared mechanical matcher 继续使用原 `_execution_at` 与 `revalidate_started_operation_at`；live commit wrapper 仍独立要求原 native boundary evidence。没有新的 bool bypass、持久 authority ledger、额外表或 receipt issuer。
4. schema-directed dependency walk 追踪原声明的 refs、数组和 map，加原 resource origin/provenance authority 字段。合法 request.definition/public_configuration 中恰似 ref 的业务 JSON 不自动成为 Registry authority；实际依赖仍需原 bytes/schema。
5. 原 API 可达的 durable owner stop、writer fence 前进、provisional products 之后，原历史机械 proof 仍 VALID；canonical view 仍不公开 provisional acceptance。正向均经真实临时 Registry/PN API 建立，无 SQL 伪造生命周期。
6. 读取前后 canonical DB 表内容不变。VALID 输出仅有限 identities/digests；不输出 acceptance/intent 正文、worker pid/uid、request/definition/configuration、allowed_action 或临时产品正文。结果不是 native evidence，不能重新进入 live 接口成为许可。
7. foreign original Registry 与错误 assertion fail closed。损坏 SQLite 文件由原 uncaught DatabaseError 改为无 proof 的分型；这是读取稳健性修复，不是授权成功的漏洞。未要求吞掉任意编程异常。

## 独立证据及修复轨迹

保留各轮原始日志、JUnit、测试快照和源身份，不覆盖最初失败。

- round1 原 27 例：4 PASS、23 FAIL。其中 18 例确实在 SQL 损坏后错误 VALID：前三 phase physical head 3 例、tx/events/outbox 一致错误 epoch 3 例、acceptance 非 record 的 object/relation/commit event command/idempotency/causation/parent 12 例。另 3 个 PUBLISHED 用例在注入阶段被 SQLite CHECK 拒绝，根本未执行 classifier；foreign Registry 的 UNAVAILABLE 已 fail closed，是测试误要求 INVALID；terminal-ready 正向被现有 API 拒绝，是不可达路径假设。
- round1-extra 原 15 例：13 PASS、2 FAIL。missing Start member 是真实错误 VALID；foreign root member 在注入阶段被 FOREIGN KEY 拒绝，不是 VALID。
- round2 冻结源 `d009afc86439d9dc17a7b4b448f918ca97375d2eba013d6ebe6063c85839e58f`：原修正范围 38 例首次 34 PASS、4 个上述注入 fixture 失败。只对临时损坏负测显式关闭相应 CHECK/FK 后，4 例全 PASS。
- 同一份修正注入 fixture另对 round1 原源复跑：3 个伪 PUBLISHED 确实返回 UNAVAILABLE、foreign root member 返回 INVALID。这是后来取得的独立补证，不能倒写成旧日志已证实；伪 PUBLISHED 也不是合法生命周期或新的许可。
- round2 新 10 例：4 个 orphan temporary member、5 个原 Start command/idempotency/correlation/causation/parent 损坏仍错误 VALID；1 个损坏 SQLite source 抛 DatabaseError。三类均已修复。
- round2 新正常 API 正向：两种 ref 形状的合法 request public configuration 原 API 已成功 accepted，history 却 INVALID。已改为沿原 schema 声明走依赖。operation config 对照第一次 fixture不符合原 executor schema，明确属 fixture 错误；纠正为原 schema允许值后，旧版已 PASS，不计产品缺口。
- 最终 round3：**51 PASS，0 FAIL，0 ERROR，0 SKIP**；38 个原范围 + 10 个新增边界 + 3 个业务数据对照。`evidence/round3.xml`、`round3.log`、`round3.exit` 是最终实绩。

另已只读核实作者最终 XML：139 PASS（108 history + 31 core）、68 adjacent PASS、40 既有 core-review PASS；三组 source-before/after 都与 a3af 最终身份相同。连同本轮独立 51 个，共 298 个不同 classname/test node IDs，0 FAIL/ERROR/SKIP；247 个由作者执行，51 个由本轮独审执行，不能混称全部为本轮独立执行。

阶段运行不累加通过数。最终 51 是不同 pytest node IDs，不声称 51 种彼此没有语义重叠的独立能力；与作者同类回归也不重复宣传为新增功能覆盖。详见 `TEST_COUNTS.json`。

## 严格保留的限制

- 所有 accepted fixture 都显式注入 D0 native observation test facts；没有 OS peer authentication、receipt完整交付、实际 Popen、target exclusive reservation、SIGINT 时序或 native Success 实证。
- 现有 H7 firing 未结算；后继 execution_generation、合法 H7 publish/abandon、H7b recovery、完整 child native binding 均不可达/未验，不用 SQL 修改伪称正向。PUBLISHED 不代表本切片能证明未来 promotion 合同。
- proof 是原 mechanical acceptance 的历史分类，不是当前 writer fence、fresh bootstrap、resume/reissue 或已发生 child creation 的许可。
- 当前信任边界仍是原 Registry 和可信安装代码；这些定向损坏负测不是任意恶意重写 SQLite、所有 immutable bytes 与全部元数据后的密码学真实性保证。
- read-only/query_only 保证不改 canonical database/event state；SQLite 仍可能维护 SHM/WAL 协调文件，不声称目录零写入。
- 离线 sentinel 阻 socket、URL、subprocess/exec/spawn、fork/PTY；最终 runner也直接封锁 `os.openpty`/`os.forkpty`。没有 model/provider/API/native执行、安装、Actions、push。未跑全仓、未称已合并或当前 remote main。

## 复跑

该目录可放在交付包的 `review/`，相邻 `source/` 为最终冻结源。使用已有且获准的 Python 及已安装依赖：

```sh
RPNH_PYTHON=/path/to/existing/python bash review/tools/run-review.sh
```

如位置不同，另设 `RPNH_SOURCE=/path/to/frozen/source`。结果默认写到 `review/reproduced-results/`；可用 `REVIEW_RESULTS_DIR` 指定新目录。runner不安装任何依赖、不读取生产 Registry，只由 pytest在临时目录创建本地 Registry。复跑前后必须比较 manifest，且其值须为上列最终身份；测试无法替代身份核对。此包不含临时 Registry DB、immutable业务对象目录、native evidence实例或receipt正文。
