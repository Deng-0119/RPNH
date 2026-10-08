# 本地执行任务书：Registry 原生 reader + 共享提示 A/B 补验

## 目标与权限边界

在本地受支持环境验证本包两项 harness 修复，产出可复核的独立 A/B 结果和剩余阻塞；达到验收条件后停在待人工合并状态。不要 push、发起 Actions、运行真实模型/provider、启动业务 benchmark、修改业务 grader/历史分数、运行后续设计包或擅自扩展新 A 白名单之外的 runtime/parser/enum。

可以执行本任务列出的离线/零真实模型 scripted native fixtures，使用临时隔离 run 目录。这里的“零模型”指没有任何真实模型/provider 网络请求；fixture 的 Registry 逻辑 call ledger 可能仍计数，必须分开报告，不能把逻辑计数写成真实模型使用。

若需要新增 local-only fixture/测试脚本，用独立文件和独立 diff 记录；不要悄悄改 A/B 冻结版本或额外生产接口来让补验变绿。未找到受支持入口或权限不足时停止依赖该入口的项并报告 BLOCKED/NOT_RUN，继续独立项；不得编造 CLI、绕过权限或将 transport 替换当 native。

## 1. 获取文件、核源、保护本地修改

1. 下载本 ZIP 到本地，验证外层 SHA256、ZIP CRC，再检查 CHECKSUMS.sha256 与 file_manifest.json。不能假定云端路径在本地存在。
2. 找到 Deng-0119/RPNH 的正确 checkout；读取该 checkout 的 AGENTS.md、CONTRIBUTING.md、docs/guides/development.md 及必要本地 skills。记录实际 OS、Python、pytest、工作目录和现有测试环境；不要上传凭据、环境变量值或私有 profile。
3. 保存 git rev-parse HEAD、git status --short 与受影响文件的本地 hash。确认 checkout 含同一基线的 examples/package_reuse/native-add-v2.lock.json 与 native-add-v2.zip；它们是已有测试输入，不是本补丁新增文件。保留全部 dirty/untracked 文件；禁止 reset --hard、clean、覆盖现有修改或暗中 stash。优先从已存在的精确基线对象创建独立临时 worktree/副本。
4. 实测基线必须明确为 c545621c30452650202ad0aca2bc023b6956929b。逐一核验 file_manifest.json 列出的所有旧文件 bytes/Git blob，并确认其中列为 added 的路径原本不存在。主工作树已前进时可在精确基线隔离验证，但必须单列与当前本地 HEAD 的兼容性；不能把旧基线测试标成新 HEAD 实测。若基线对象缺失或冲突，先报告，不擅自改写 patch。
5. 将 BUNDLE 设为解压后的本包绝对目录。以下命令在隔离的精确基线仓库根目录执行，使用仓库已支持的 Python 环境：

```sh
git apply --check "$BUNDLE/patches/A-registry-execution-reader.patch" "$BUNDLE/patches/B-shared-output-guidance.patch"
git apply "$BUNDLE/patches/A-registry-execution-reader.patch" "$BUNDLE/patches/B-shared-output-guidance.patch"
```

之后逐一比对最终白名单中的全部文件 SHA256。不得再用 source/ 覆盖应用结果。如果已有同样改动，应证明 exact equality 并报告 already applied，不能重复应用。

## 2. 精确 targeted 离线回归

先检查 manifest 中旧/新 hash；当前 A 包含共享核心变更，不能沿用旧“全部 core 未改”的说明。只读契约约束读取行为，不要求把既有运行中的 owner 再创建为另一个实例。

在核源后的候选 checkout 根目录执行，分别留原始 stdout/stderr、退出码、JUnit 和环境信息，不做无关全仓库跑测：

A：以下是新共享 reader 最终实现候选已执行的相关集合。在当前冻结字节上本地重跑，单独记录本次 source hashes：
```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m pytest -q examples/harnessaudit_office/tests tests/test_package_result_projection.py tests/test_object_store_streaming.py tests/test_package_result_delivery.py tests/test_run_descriptor_reads.py tests/test_registry_read_session.py tests/test_module_graph_projection.py --disable-warnings --junitxml="$RESULTS/A-offline.xml"
```
其中 test_no_terminal_does_not_read_owner_resources 验证 environment_host 无 delivery 候选时保留既有短路，不把它解释成通用 current-terminal 查询。旧 HA-only 108 pass 不属于新 A 验收结果。

B：
```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m pytest -q tests/test_optional_output_guidance.py tests/test_workspace_write_atomicity.py tests/test_agent_request_envelope_materialization_stage3.py tests/test_agent_loop_tool_components.py --junitxml="$RESULTS/B-offline.xml"
```

RESULTS 必须是新建的验证输出目录。完整记录使用的解释器，不靠改变 Python import 路径偷偷导入旧版本。新 A 相关集合在说明性 delta 前实测为 242；B focused 集合为 36。数量不同要解释，不直接套用云端数字。作者/独立复测/本地复测各列，不相加。

A 必须以现有 Registry 的共享 typed reader 为统一匹配入口，HA exporter 与 environment_host 不再各自复制终态链校验。逐项报告：

- frozen cut 属于正确 Registry core；跨 core/cut 的错误组合拒绝，不能仅凭相同 ordinal/epoch 接受。
- current authority、run/task、generation、status 和可空 terminal/result typed 返回值来自同一 cut；historical terminal 不可关闭 reopened active generation。
- 两个 consumer 在各自一次读取内保持同一冻结 cut，并在同一未变化 Registry 事实集上读到相同 exact terminal/result 身份；不要跨 core handle 复用 cut 对象；各自保留既有显示/投影格式，不创设新的授权或回填 authority。
- 正常 exact ref、None + terminal、错 type/id/version/额外字段、foreign-run、多个历史 generation、running/active/stopped。
- canonical/provisional 隔离、exact closure/checkpoint/index/result/task、full-ref cache 和 payload bytes/metadata 一致性；strict descriptor JSON 拒绝重复 key/non-finite，descriptor 与产品文件实物大小边界均生效。
- frozen-cut head ordinal/writer epoch 漂移 fail closed；HA collector 不留下部分 observations，environment_host 不返回拼接结果。
- 真实临时 Registry 前后只读 fingerprint；没有 writer、resume/reconcile、launch、自动修复、模型调用或权限扩张。

B 离线至少逐项报告：plain Unicode/multiline text、旧 quoted text、JSON-looking text、structured 有效/无效 JSON、schema preflight 先于 file child、content/source_ref 互斥、exact source delivery、workspace 无隐式 fallback、outcome/port 和 completion 原有拒绝约束。不要自动修复坏 JSON 或选择 outcome。

## 3. 受支持 native main-session 起点

以下入口已在基线源码中核实存在，使用 scripted ports，不调用真实 provider：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m pytest -q tests/test_main_session_registry.py::test_main_turn_accepts_direct_text_write_without_double_json_quoting tests/test_main_session_registry.py::test_real_terminal_child_registry_is_receipt_authority --junitxml="$RESULTS/B-native-main.xml"
```

必须是真实 OwnerEventLoop / AF_UNIX 运行；不得用 monkeypatch socket/pipe 来绕过环境能力。第二项证明 parent receipt 指向真实 child Registry；它仍是 text output 中的 JSON 内容，不是独立 structured-schema write 验收。

单独跑这两项不足以关闭下面的本地新增观测要求。先检查实际 tests 和源码，再选择最小的受支持 fixture 接线。缺口如需新增 fixture，请作为单独 local_validation_additions diff 返回，并记录命令；这里不虚构尚不存在的测试名。

## 4. B：实际 Registry → canonical recipe → provider-visible → write/result 闭环

以现有 _DirectTextTerminalPort / run_agent_task 的 scripted 路径为起点，在新临时 run 上完成一条 text、一条真正非 text declared schema 的 structured JSON；source_ref 如需附加覆盖，应使用当前 firing 实际可见的 exact resource。不要以 text 端口里恰好写了 JSON 文本冒充 structured schema。

需要留存最小可分享证据：

- 本次 fresh 注册的 static prompt 与 optional_agent_tool_catalog exact refs、payload SHA256；未改历史 prompt 资源。
- 检查是否真实发布 logical_provider_request_recipe/v1（源码可从 cpn/components/agent_loop/turn_execution.py 进入），记录 exact ref、canonical payload SHA256 和 source refs。不存在时如实报告，不拿 synthetic before/after-prompt.json 代替。
- scripted port 收到的 attempt.canonical_request_bytes 或当前入口实际等价物的 SHA256；按本地源码中的真实 materializer 还原 recipe，核对 provider-visible messages 与 tool parameters。区分 logical recipe 与 provider envelope，不强行要求两种不同文档字节相同。
- 比较新 prompt 的 text / JSON / exact source_ref 说明与可见 write_file descriptor 和输入 schema；继续保留 outcome/port 约束。确认 B 的 schema/enum/parser/writer runtime 未改；A 允许的共享 Registry reader 与两个消费者变化必须与新 A 白名单完全一致。
- 对每条 write 留 output schema、exact product/resource refs、Registry payload SHA256/大小、workspace bytes SHA256/大小、result/terminal refs 与实际输出核对。不要只看 stdout 或 stop_reason。
- 针对现有 resumed run，只确认旧 immutable prompt 保留；不能因新 factory 文本发生变化就重写历史资源。若选做 resumed fixture，单列具体新/旧 generation 证据与覆盖范围。
- 记录真实模型/provider 网络请求数 0，scripted logical call 次数另列。

## 5. A：两个消费者原生只读验证、真实受管动作导出和历史 reproject

现有 native managed fixture 起点（源码已核实存在）：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m pytest -q tests/test_optional_managed_plugin_actions.py::test_node_scoped_managed_calls_persist_v3_alongside_builtin_v2 --junitxml="$RESULTS/AB-native-managed-start.xml"
```

该现有测试会运行 scripted native AgentTask 以及本地纯函数插件，验证 Registry v3/v2 记录；它本身没有调用 HA exporter，也没有证明 provider delivery。用真实产生的 managed-action Registry 记录接入适当 node/role 映射后调用 export_registry，补充实际 managed-action 与终态同次导出的回归。不得以注入一个 action row 的云端用例替代这项。若缺受支持映射/fixture 前置条件，报告具体缺口和 NOT_RUN/BLOCKED，不冒充已通过。若追加本地脚本，独立列 diff/hash/命令。

HA exporter 新开 Registry 时只允许 create=False/read_only=True。公共 reader 和 environment_host 可借用其既有 core/kernel，但读流程不得开启 writer、创建事件或触发执行；不能为了验证而创建另一 execution owner。核实两消费者各自复用 reader 的同 cut typed 结果，不能退回各自扫描，也不得把一个 core handle 的 cut 转给另一个 handle。比较导出前后事件 head、writer epoch 与必要对象 hashes；不得 resume/start/修复现有运行，不能为了拿终态调用 TaskControl writer 路径。复验 current generation、active、坏 caller ref 与 head 漂移的拒绝边界；真实并发 writer 的压力测试若无现成安全入口，单列 NOT_RUN，不能从注入式 D0 推定已覆盖 OS race。

若本地已有受授权且材料齐全的 completed native-live HA run，可以使用 README 现有命令：

```sh
python examples/harnessaudit_office/example.py reproject --run-root "$EXISTING_RUN" --output "$NEW_DIRECTORY"
```

前置检查必须包含已有 native Registry、run_status/driver_result、公有输入、protocol、要求的结束快照及 quiescence/condition 信息。NEW_DIRECTORY 必须不存在。这个命令会在新目录复制该 run 的最终 Bank snapshot；它仅留本地，不得打进返还 ZIP。缺任一前置材料即 NOT_RUN，不许为了补材料悄悄执行 run/resume/model/backend。stale saved driver ref 被拒绝是 expected rejection，保留原数据，不能静默修正 ref 后标成同一用例 PASS。禁止 score/grader 与重算或覆盖旧分数。导出修订、native outcome、业务成功和历史分数分列。

## 6. 停止条件与返还

达到各自 targeted tests、native text/structured 写入闭环、recipe/request 一致性、A 实际 managed-action 导出且无未解 blocking finding 时，可报告对应“本地补验通过”；未运行项仍明确 NOT_RUN。历史 reproject 取决于现存资料，缺资料时报告不适用或 NOT_RUN，不扩大范围造数据。任何源不匹配、原生入口不可用或需要新权限时，停止依赖项并交回准确 blocker。

按 return_schema.json 填写 return_template.json，附：环境/源码 manifest；A/B 独立原始 logs/JUnit；per-case 结果；最小 refs/hashes 证据；新增 local-only 脚本和 diff；最终 dirty 状态与未改历史数据声明。返回新的精简 ZIP。不要返还 Registry/Bank DB、token、API key、私有 profile、raw provider transcript 或全目录副本。必要脱敏必须保留原始 artifact SHA256、included SHA256、位置与规则。没有 permission/通路完成就保留 BLOCKED，不无限循环。完成后不要 push/合并，交用户一起复核。
