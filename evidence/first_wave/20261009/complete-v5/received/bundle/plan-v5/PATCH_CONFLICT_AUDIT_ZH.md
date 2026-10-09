# 补丁交集与目标 hash 锁核查

日期：2026-10-09 UTC。方式：只读解析实际 patch headers、读取候选/manifest 字节并计算 SHA-256、读取现有 ZIP 的对应 patch。未应用补丁、未导入产品、未跑测试或 native。完整逐文件 before/candidate 摘要见 [evidence JSON](evidence/IDENTITY_AND_INTERSECTIONS.json)。

## 1. 三种不同问题

- **同路径修改：** 后继依赖前置的特定 preimage，须按顺序整合；不等于已经验证会发生 Git hunk 冲突。
- **目标身份不匹配：** 两个 patch 完全不重叠，仍可修改对方 gate 锁定的未触及依赖文件。
- **运行组合未认证：** 即使既无文本重叠、也不命中锁，compiler/Registry/测试发现等语义交互仍需要新组合的证据。

## 2. 实际 touched-path 交集

共比较 66 对候选；其中 53 对跨 lane 的 touched-path 交集全为空。所有非空交集如下：

| 前置 | 后继 | 实际交集 |
|---|---|---|
| H7_core | H7_history | `cpn/rpnh/registry/event_store.py`；`cpn/rpnh/registry/parent_child.py` |
| Codex_reader | Codex_history | `cpn/rpnh/registry/main_thread.py` |
| Codex_effort | Codex_history | `cpn/frontend/codex_app_server.py`；`tests/test_codex_compat.py` |
| Codex_effort | Codex_0161 | `cpn/frontend/codex_app_server.py` |
| Codex_history | Codex_0161 | `cpn/frontend/codex_app_server.py`；`cpn/frontend/codex_compatibility.v1.json`；`cpn/frontend/codex_history.py` |

S1 与 H7 core、history 与 lowering 虽没有 patch 路径交集，后者仍有语义/精确输入依赖，不能由空交集推导可倒序。H7 core→history 与 Codex 链内的交集是明确后继修改，保存旧阶段身份后进入新阶段。

## 3. Codex 0.161 的 1,044-file 目标锁

`native-gate/prepare_fixture.py`、`run_profile_gate.py`、`run_object_rpc_probe.py` 都遍历 `file-manifest.json["source_files"]`，对目标 repo 的各记录路径比较 SHA-256；pair入口委托对应gate。manifest包含1,044路径，source fingerprint为 `316a06aad41653e890d16a19519d07b09042c54c4961586b54cf2842b10d8658`。`verify_package.py` 的包内完整性/净patch检查不能替代这个目标树检查。

下列逐路径候选字节已与锁逐一对比，均确实不同，既不是仅凭文件名猜测，也不是测试执行结果：

| 增量 | 命中锁路径数 | 解释 |
|---|---:|---|
| S1 | 7 | 全部候选摘要与旧Codex锁不同 |
| H7_core | 19 | 全部候选摘要与旧Codex锁不同 |
| H7_history | 1 | 全部候选摘要与旧Codex锁不同 |
| H7_lowering | 3 | 全部候选摘要与旧Codex锁不同 |
| R1_v2_full | 0 | 新增路径不在旧锁中；未证明运行组合 |
| R2 | 0 | 新增路径不在旧锁中；未证明运行组合 |
| DSH | 6 | 全部候选摘要与旧Codex锁不同 |
| OpenCode | 5 | 全部候选摘要与旧Codex锁不同 |

核心链合计是29个不同锁路径：7(S1)+19(core)+1(history)+3(lowering)中，history的event_store.py已经算在core内。DSH另6、OpenCode另5；合计40个不同锁路径，仍不是一个已验证组合。lowering行按本次已核源码计算，最终资格与版本以 [状态页](LOWERING_STATUS.md) 为准。

### S1：7个

- `cpn/rpnh/_marking/claims.py`
- `cpn/rpnh/_marking/selection.py`
- `cpn/rpnh/marking.py`
- `cpn/rpnh/registry/_event_store/validation/firing.py`
- `cpn/rpnh/registry/declared_effect_validation.py`
- `cpn/rpnh/registry/module_effects.py`
- `cpn/rpnh/runtime_net.py`

### H7 core：19个

- `cpn/rpnh/registry/_event_store/backend.py`
- `cpn/rpnh/registry/_event_store/commit.py`
- `cpn/rpnh/registry/_event_store/validation/operation.py`
- `cpn/rpnh/registry/_invocation/admission.py`
- `cpn/rpnh/registry/_invocation/execution.py`
- `cpn/rpnh/registry/_module_resource_projection.py`
- `cpn/rpnh/registry/_registry.py`
- `cpn/rpnh/registry/bootstrap.py`
- `cpn/rpnh/registry/checkpoint_reentry.py`
- `cpn/rpnh/registry/event_store.py`
- `cpn/rpnh/registry/module_binding_authority.py`
- `cpn/rpnh/registry/module_execution.py`
- `cpn/rpnh/registry/module_nets.py`
- `cpn/rpnh/registry/operation_execution.py`
- `cpn/rpnh/registry/run_authority.py`
- `cpn/rpnh/registry/schema_catalog.py`
- `cpn/rpnh/registry/task_ledger.py`
- `cpn/schemas/INDEX.json`
- `cpn/schemas/registry_v1/fact_event_envelope.v1.schema.json`

### H7 history：1个，与core重复

- `cpn/rpnh/registry/event_store.py`

### lowering快照：3个

- `cpn/rpnh/compiler.py`
- `cpn/rpnh/composition.py`
- `cpn/rpnh/executable_net.py`

### DSH：6个

- `cpn/dsh/backend.py`
- `integrations/dsh/src/agent.ts`
- `integrations/dsh/src/capabilities.ts`
- `integrations/dsh/src/projection.ts`
- `pyproject.toml`
- `tests/test_dsh_distribution.py`

### OpenCode：5个

- `cpn/frontend/opencode_compatibility.v1.json`
- `cpn/frontend/opencode_launcher.py`
- `cpn/frontend/opencode_protocol.py`
- `docs/guides/opencode.md`
- `docs/guides/opencode_ZH.md`

R1/R2都是新增路径，不在该1,044-file清单。该gate是逐条已知路径校验，不是“禁止任何额外文件”的完整tree等值证明。例如OpenCode新增的tests/conftest.py不命中锁，但可能影响pytest发现和插件行为，组合仍须审查。

两份technical-report不在该subset或BASE_SOURCE_LOCK；1f仅报告更新本身不造成此锁失配。不因此回退报告。

## 4. 其他gate的阶段边界

| 入口 | 核什么 | 后继注意 |
|---|---|---|
| S1 verify_changes；core/history verify_identity | 各包候选/输入/patch身份；core/history源码subset | 原输入快照已含前置，不重复套；后继合法改变祖先文件后不能要求祖先final manifest仍等值 |
| reader run_local_gate | 七个目标final hashes + 选择测试 | history改main_thread，保存reader stage |
| effort verify_patch / verify_package --candidate | 十二路径preimage/apply-check与目标final hashes | history改其中两路径，保存effort stage |
| Codex history prepare_fixture / run_native_gate | 二十一目标final hashes与fixture identity | 0.161改三个产品路径后使用新gate |
| DSH verify_bundle --checkout | 十三changed paths的preimage/新增检查 + 十二protected | 无旧报告hash；protected原pin/bridge等边界保持 |
| OpenCode verify_package / tests/opencode_candidate_support.py | 包内完整性、972 baseline blobs、十路径变化；真实Git checkout的HEAD/dirty和cpn/tests py/json记录 | 记录实际身份不等于组合运行通过；不强制回到8dd |

## 5. 精确patch身份

以下为patch SHA-256，不能与source aggregate或ZIP摘要互换。根级patch按现有源码、独审与交付包逐一核对；具体ZIP字节身份见evidence JSON与lowering状态页。

| 增量 | touched paths | patch SHA-256 |
|---|---:|---|
| S1 | 13 | `bd0e2a8d362179fd68bd5451a932449f059b7038db16ca627eb84843f809bd4d` |
| H7_core | 32 | `7746881935e0d35e85cb71b2e08bab82ab3f844d52771212b39b3a7728448ed3` |
| H7_history | 4 | `b8ad5b5c18346cf9fa71cd17c96d17deb67ecbb336329d83c78c271f521bedfa` |
| H7_lowering | 8 | `2bc8dd99115f2d139f6b17dd8cd1342087a02110233b8d43c5c37c5199a9cc98` |
| R1_v2_full | 6 | `67466bfc13a685dd79dc26d0c0c2f0a33b7016553ccf53728b57906cc2f10ac3` |
| R2 | 5 | `5054c53cc61bb03fdc58a1da6f7e56b41decc0ccb78124478effc38b2bf93e52` |
| Codex_reader | 7 | `f4726d7c1a230c8935b22310bb1d29f7c8e68342ef9b660cabb6430b8a3d4654` |
| Codex_effort | 12 | `3be0de65edb5a4f68579f115f5072bacc3e4fe4ffefeba96b226e45cbcab8a8d` |
| Codex_history | 21 | `df0c3090e84f532d158dd02d7b65489323563a470843e4bd4e2688ffcc25900b` |
| Codex_0161 | 6 | `720bcf2bd95886a26425e00caec80eab0fbcf0eec0481202ca54ce88a5f8a3c8` |
| DSH | 13 | `0bddfeb49e1b829911d2f594670476bef39a6e6aefff28c5f61f545506a26b06` |
| OpenCode | 10 | `ee33ed95126989319e981683a308e3182d9b5b4e4c0f0a900e5bb8f111716bba` |

R1的v1→v2 delta另为 `40da4de0316e5fcb07276fcc159c7277403eef942df4769ad361e5f40804e72d`；仅适用于精确旧六文件，不能和v2 full一起使用。

H7 core的ZIP同时保留旧 `evidence/source-v1/H7-core.patch`（`ca908dd1894ab5268379008efc9cd59f87215bac20544b7cb6afc139b3de7d24`），这是追溯材料，绝不能代替根级 `77468819…`。所有ZIP名、roots、patch member路径、字节摘要见evidence JSON。

## 6. 组合处理结论

1. 分lane隔离，先各stage验收并保留manifest、结果和失败。
2. 不跳过锁、不改旧manifest、不用source覆盖、不回退现有报告；实际hunk问题在明确新组合分支审查解决。
3. 最终选入的组合另建source identity/full inventory与适用gate，解释新增/改变的每条依赖，经独审后做影响回归。
4. 新gate通过只证明其实际窗口；不把旧stage PASS、多个版本数字或未跑native相加包装为组合PASS。
