# Static resource-lease read/reference 独立实现复审

日期：2026-10-08。最终裁定：**APPROVE，限本通用静态 resource-lease read/reference 修复的已验 D0 范围。**

已确认原失败修复，直接低层/transaction 闭环及合法 exact 选择问题均在最终字节复验通过。没有未解决的本范围阻塞；不是 H7 整体、native transport、默认 binding 搜索/活性或全仓测试通过证明。

## 审查范围

固定基线 Deng-0119/RPNH `1f191645c4d60c8b190d42e9fad99c85e8981c03`，产品基线 `d92ff3704b6002bf5ecbccb3e6a3d1489809a805`。独审对象为 `rpnh-static-lease-read-fix` 的通用静态 PN lease read 修复，非 H7 origin、bootstrap、native transport 或完整 child 生命周期。

- 在原 Registry、原 TeamNetMarking、原 reference_token_ids / claimed refs / consume delta / Success / rehydrate 链内审查。
- 不要求 Module 放弃现有 VariableResourceArc，不另加 marking、权限表、ledger 或 RW 锁。
- 普通 data/control read 保留旧 consume-return；静态 lease read 引用 exact token，不通过归还复制令牌维持并发。
- 收集原始失败、负向探针、产品修复迭代与最终冻结字节证据；不把编译成功当运行成功。

## 已复现并要求修正的问题

### R1 原 owner admission 不能执行静态 lease read

独立直接使用原 source 的真实 `start_run` → `owner.admit`：编译/M0 成功，但首次 admission 抛出 `CheckpointFiringStepError: resource lease has no exact variable consume-return inscription`。

证据：`evidence/baseline-independent.txt`。这不是 H7 未来 origin 行为，而是原通用运行层最小反例。

### R2 低层 admission 缺少声明/consume/index 的闭环

基线低层 `InvocationLifecycle.admit_firing` 接受缺失静态 lease read 和把该 read 标成 consumed；raw transaction 还可接受 claimed refs 与 version index 不一致及同 version 伪 logical ID。读侧部分验证会拒绝这些记录，但无法代替 commit 拒绝。

初稿补原 transaction guard 后，独审又证明清空普通 consume 索引可让两次 admission 和 Start 同时使用同一消费输入。修订后该路径按原 Module consume 索引规则拒绝，没有将普通 read 或 variable consume 的旧索引语义另行改写。

证据：`probe_lowlevel.py`、`probe_indices.py`、`probe_empty_consume.py` 与对应 `evidence/baseline-*`、`evidence/initial-candidate-empty-consume.txt`、最终探针记录。

### R3 合法非默认选择不得变成非法

独立正向验证：

1. pool 两枚 token / read weight 1，明确选择默认低 token_id 之外的另一枚，经真实 preflight → commit → Start → Success 成功。
2. variable read A 与 static read B，共用 pool，static weight 1，合法 union A+B 经真实全流程成功。静态弧不强制最大化与变量引用的重叠。
3. 进一步审出 exact helper 先运行无约束默认 selector 的问题：某个不可匹配默认 carrier 不能阻止另一组合法 exact refs。最终修复已经独立复跑通过。作者保存的旧候选失败来自真实两次前序 Success 生成的 carrier，并非手写 checkpoint；最终 exact admission、Start、Success 通过，错误 carrier 仍在 commit 拒绝且事件/对象数不变。默认 owner admission 仍返回 None，该默认调度行为未改。

## 重要语义边界

分类来自 adopted compiled PN 的 input/read 弧及 place.token_kind。调用方 Token.kind 不能把普通 data read 伪装成共享引用。exact claim 的 place、weight、freshness、consumer、net/ref/index 均须闭合；静态和 variable read 同 token 做集合 union。

引用存续沿原变量引用冲突规则：read/read 可并发，read 与同 occurrence 的 consume/edit 不可同时持有。同一 firing 的 static read 与 variable consume 使用不同 token。引用结束后，后续合法 variable consume-return 仍可推进；这不是永久禁止逻辑资源更新。

独立真实 Registry 对照：旧 variable read 与新 static read 分别读同一 logical-slot pool，active read 阻止 owner edit admission；read Success 保留原 token/ref/state；随后 edit 可正常 Start/Success，返回新 token 并保留本例相同 lease identity/immutable RVR。反向次序也纳入独立探针。证据：`probe_read_edit.py`、`evidence/read-edit-comparison.txt`。

静态引用提供既有 claimed-input snapshot/provenance 路径，不单独授予物理资源 borrow/edit 权限。没有 variable resource-access arc 时，`owner.access_resource(read/edit)` 仍拒绝。不能把非消费 PN 引用说成物理独占或永远阻止以后修改。

现有 reset/retirement 拒绝 reusable/resource_lease；owner adoption 要 drain 全部 PROVISIONAL；operation revision 要当前唯一 PROVISIONAL。它们复用 claimed refs 包括 read 的既有保护，不是新增 origin 不可删除保证。drain 后普通结构修订仍可合法删弧；H7 origin 不变量在此包外。

## 验证边界与失败记录

- 有效运行仅使用已提供 venv、临时真实 Registry 与纯 D0 输入；未执行 native client、模型/业务 API、安装、登录、Actions 或 push。
- 一次旧回归批次误包含 `test_structural_evidence.py` 的三个 OwnerEventLoop socket 测试；它们在 socket 构造处被环境 `EPERM` 拒绝，没有提权或连接成功。原记录保留，不计 PASS。后续明确 deselect 三例。
- `test_candidate_plan_resources.py` collection 依赖 curated snapshot 缺失的 `examples/net_operations/live_agent_replacement.py`，因此该组未运行；没有为凑数替换或伪造 fixture。
- 最初独立变量测试使用 M0 空 claim 或重复 resume 的错误预期；已改为真实前序 Success 产非空 lease claim，并按既有重复 resume 拒绝语义断言。它们不是产品回归，不用失败草稿推断产品缺陷。
- 不得凭本报告称 H7 整体或 native 完成。


## 最终字节身份

独立重算并核对：973 个固定原源码文件 SHA-256 和 Git blob SHA-1 全部匹配；979 个候选文件 SHA-256/size 全部匹配。8 个产品文件在最终复跑前后未变化。13 个变更文件为 8 产品、3 测试、2 中英文 guide。

- Patch SHA-256：`bd0e2a8d362179fd68bd5451a932449f059b7038db16ca627eb84843f809bd4d`
- `source-identity.json` 文件 SHA-256：`b65249d3771c306773c6930d39323d019450f7c668e0f7d778c798b70278f753`
- 候选 source manifest 聚合 SHA-256：`4a7841ac16432aeb173562ec797bc062839a006fdb684d5ea1e3b161c6c49fbc`
- 聚合算法：对 `source-identity.json` 的 `candidate_files` 数组作 UTF-8 JSON 编码，`ensure_ascii=False, sort_keys=True, separators=(',', ':')`，再算 SHA-256。
- 独立 GNU patch dry-run 对固定 baseline 的 13 文件全部可应用；没有修改基线。作者另有干净副本 apply/hash 复验，本报告不将作者的运行冒充独立执行。

完整身份与每文件 hash：`evidence/final-identity-verification.json`、`evidence/reviewed-source-identity.json`、`evidence/final-product-start-hashes.json`、`evidence/patch-apply-dry-run.txt`。

## 最终运行结果和去重

1. 作者统一回归：**71 PASS**，129.29 秒。以 `tools/offline_pytest.py` 的 autouse socket/URL 禁止 sentinel 执行。原记录镜像为 `evidence/author-final-focused.txt/.xml`。
2. 独审 pytest：**42 PASS**，由原创 9 个参数化 D0 case、真实 carrier 作者 case 独立复跑 1 个、现有纯回归 32 个组成。记录：`evidence/final-independent-tests.txt/.xml`、`evidence/final-pure-regressions.txt/.xml`。
3. 42 个独审 pytest 中，**13 个与作者 71 个重叠**：12 个 marking modularization + 1 个 exact carrier。两组按最终 module/testcase 去重为 **100 个独立 testcase**；不能直接把运行数相加当不同测试。计数依据在 `evidence/test-counts.json`。
4. 另有 **4 个独立真实 Registry read/edit 对照场景**：variable/static × read-first/edit-first，全部 PASS。它们是独立脚本探针，不加入 pytest 100 的总数。记录：`evidence/final-read-edit-comparison.txt`。
5. Direct 低层/API、raw index/identity 变异和清空 consume 索引的双 admission 防回归探针均按预期：正确 claim 接受，其余在 admission/commit 拒绝。记录：`evidence/final-lowlevel.txt`、`evidence/final-indices.txt`、`evidence/final-empty-consume.txt`。重复尝试不包装成新的独立 case 数。

独审原创 9 case 实际覆盖：同 transition 纯引用两个活跃 firing 与兄弟 Success；只读 Core 冷重建；真正非空 variable read 同池/分池；completion recovery 仅一次结算；snapshot 与物理访问权限区别；非默认静态 token；非默认 static/variable union；伪 Token.kind 不改 PN 分类；stale checkpoint 拒绝。

纯 typed checkpoint 探针仅用于发现默认 carrier 限制。后续真实 Registry 证据由两次普通前序 Success 产出两个 carrier，当前 bad 无法匹配但 good 可匹配。旧候选 exact admission 失败已保存，最终版本独立复跑 PASS。两类证据明确区分；没有把 typed projection 当作实际 durable 路径的替代证明。

## 可复验命令

使用提供的已有 Python：`/workspace/scratch/18c810e6dd59/rpnh-recovery-20261003/source/.venv/bin/python`。所有工作位于已有云执行环境，没有改用用户机器。

- 工作区根，`PYTHONPATH=rpnh-static-lease-read-fix/source`：`python -m pytest -q rpnh-static-lease-read-review/test_independent_static_lease.py rpnh-static-lease-read-fix/source/tests/test_static_lease_exact_selection.py --junitxml=rpnh-static-lease-read-review/evidence/final-independent.xml`
- 候选 source 根：`python ../tools/offline_pytest.py -q tests/test_marking_modularization.py tests/test_registered_operation_recovery.py tests/test_structural_evidence.py -k 'not test_inspector_routes_real_execution_and_preserves_exact_request and not test_scheduler_cannot_turn_disabled_operation_into_enabled_one'`
- 工作区根，相同 PYTHONPATH：分别运行 `probe_lowlevel.py`、`probe_indices.py`、`probe_empty_consume.py`、`probe_read_edit.py`，文件均在本报告目录。

最终结论：可交付该窄范围 patch 与 D0 证据，后续 H7 origin 不可删约束、跨进程 bootstrap/transport 及获准的 native 验收仍独立进行。本包不自动授予这些工作或远端发布权限。
