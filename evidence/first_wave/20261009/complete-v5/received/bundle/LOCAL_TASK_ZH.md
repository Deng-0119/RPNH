# 本地 agent 任务书：按阶段实施与验收

目标：在既有完整 RPNH checkout 上，复核 V5 冻结候选并形成可审查的本地结果；不要把新功能开发、未实现的 H7 native 链或最终组合默认为本次已完成。首次只读检查可直接开展；实际 repo 写入/测试仍按用户给本地 agent 的已批准范围执行。

## 0. 只读接收关

- 读本地 checkout 的 AGENTS.md，记录实际 HEAD/branch/本地 origin/main、全部 diff/untracked 及工具来源。不得把云端证据中的路径当成本机路径。
- 运行 `tools/verify_bundle.py`，返回码必须 0 且 status 为 `PASS_STATIC_BUNDLE_ONLY`。这不是产品测试通过。
- 运行 `tools/preflight_checkout.py --repo ...`。只接受其明确覆盖的基线就绪状态；它不是 native/依赖/后继阶段 gate。
- 精确基线为 main `1f191645c4d60c8b190d42e9fad99c85e8981c03`，产品 `d92ff3704b6002bf5ecbccb3e6a3d1489809a805`。若 HEAD 不同，不 reset：逐路径检查实际差异，另记适配任务；不能把修改后的目标称为原冻结阶段。
- 需要完整原 checkout 提供冻结 curated subset 未收录的文件，包括 `examples/net_operations/live_agent_replacement.py`。preflight 会确认该路径在精确基线 commit 中存在且本地字节匹配；若 commit 内也无该文件，报告精确缺口，不猜另一个来源，不从另一 lane 复制或网上抓文件。
- 只解压所需原 ZIP 的工作副本。不要递归寻找第一个同名 patch，只有 `CANDIDATE_INDEX.json.selected_final_patch_member` 是默认应用白名单。

## 1. 分 lane，分 stage

建立 `core`、`rsi`、`codex`、`dsh`、`opencode` 五条独立工作树；候选不就地叠加进用户正在工作的 checkout。每个后继使用前置精确字节与清单，先保存前置阶段命令/结果/身份。

原提交下可在已授权范围使用 `git worktree add --detach /新的实验路径 <精确commit>`；不要给用户分支 commit，不 reset/clean，不自动切换当前 checkout。工作树本身的创建是写操作，预检脚本不会代做。

每个 patch 前：
1. 用原 manifest 核 old blob、目标路径存在/不存在与冻结 patch SHA。
2. `git -C "$STAGE" apply --check --whitespace=error "$EXACT_PATCH"`。
3. 通过且获准后才 `git -C "$STAGE" apply --whitespace=error "$EXACT_PATCH"`。
4. 用该阶段原 verifier 和 source identity 核应用结果。任一 mismatch 停止，不 `--3way`、`--reject`、copy source、改旧 manifest 或关闭锁。

原 patch 树内工具可能是候选构造/硬化脚本，不能作为 setup 重跑。原 source 仅用于逐文件证据比对及已明确支持的离线复现，绝不覆盖完整 checkout。

执行顺序和精确入口看 `LANES_AND_PATCH_ORDER_ZH.md`、`ENVIRONMENT_AND_COMMANDS_ZH.md`；原详细约束看 `plan-v5/LOCAL_HANDOFF_ORDER_ZH.md`。

## 2. 结果分类

- `PASS`：精确源码、真实命令、完整日志、真实 process exit、测试 ID 与 gate 要求全部满足的明确范围
- `FAIL`：实际执行得到失败；保留原日志和触发条件
- `NOT_RUN`：未执行（工具、环境、授权、实现或 fixture 缺失），记录 reason
- `BLOCKED`：依赖身份或环境门槛不满足，未继续依赖动作
- `UNIMPLEMENTED`：需要工程实现，不能当成环境不足
- `INCONCLUSIVE`：中断/无 exit/证据缺失；不能从点号或一段输出推导成功

先做各 lane 已有离线验证，再做用户已批准且依赖具备的原生 gate；真实模型 API/费用/Actions始终不在此任务内。若原始离线测试包含非模型 Python 子进程（如 S1 冷读），按原门槛单列，不声称所有 D0 都零 subprocess。

Codex/DSH/OpenCode 的 schema/log/double、stock native、真实 transport/application/lifecycle 分别报告。stock版本只能使用已有可信 binary，记录绝对路径、exact version/hash/provenance，不下载或提升默认。

## 3. H7 明确停止点

core→history→lowering 可按原 D0 复现。最终 frozen 源仍不是完整 H7 runtime；`PreparedChildMaterials` 不得用声明对象、布尔位或空 profile 替代。完整 public inventory 设计待修订，本次不交本地按未审设计补实现。其余 peer/receipt/reservation/wrappers/parent completion 见独立工程清单。

H7 N01–N36 是未来真实验证合同，不是当前可跑实现；H7b、H8另阶段。未解决 claim/slot 不释放，不把 unknown 改成 stopped/interrupted/success。

## 4. 最终组合另设 gate

本次先交阶段结果；除非所选范围已获批准，不自动合并所有 lane。组合前必须定义精确选入候选、从干净完整基线建立新组合树，逐路径审查语义影响，形成新的完整 source inventory/identity 与适用的新 gate，并经独审。原 stage manifest、原0.161锁全部保留；组合不是改旧 expected hash 来通过原 gate。

组合至少按实际diff回归 H1/H2 owner/reader、所选 S1/H7/compiler/terminal/frontend 路径，列明确 node IDs。缺 native/fixture/测试记缺口。只有组合自身真实执行并满足门槛，才可写组合 PASS。

## 5. 回传

填写 `templates/RESULTS_ZH.md` 与 `templates/result-manifest.template.json`，每个stage建立独立证据目录、唯一UTC run ID；原stdout/stderr/JUnit、exit、before/after身份均保留。汇报有用结果优先，失败/NOT_RUN不得省略。

只回传必要的源码diff、摘要、测试/行为证据与合成fixture描述；不打包真实用户运行DB/rawdata、凭据、API key、私有配置、venv/node_modules/客户端binary。日志回传前检查秘密及用户私有内容，若有敏感材料先停并问，不能公开上传。

没有另获准发布则停在已验候选，不 commit/push/merge；若有授权，发布后仍需核确切remote commit与字节，不能只看命令exit。
