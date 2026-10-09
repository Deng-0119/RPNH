# 最终组合验收：H1 + H2a + codec + R1 + native history

## 身份与结论边界

本包是 native history 固定 cut、可信 host、refs-only 前置包。H1 已进入
`715468dab0b1bea07d7e94a7aa0606eaf194365c`，以该完整 commit 为组合起点时不要重套 H1。
其余输入包、patch 校验和及全部目标文件身份见 `combination-inputs.json`。

重要：原 R1 六文件包暂缓合入。已发现 compiler 的 terminal.config={} 缺 native
run_outcome，作者修订及独审未完成。旧 R1 的 hash 和六路径仅保留历史记录，不是
本轮可应用的验收目标。H1/H2a/codec/history 可继续；完整含 R1 的组合必须等待正式
替换包，再核新 patch/source 身份、目标碰撞和适用 gate。不得套用旧 R1 来补齐组合。
该 JSON 是输入身份表，不是组合已经 apply 或通过测试的证明。

包内既有结果分清如下：

- 基底 `ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4` 上的 focused matrix 为
  57 passed、9 deselected；9 项不是 skip，也不是 pass。
- 原独审是 32 passed，其中 14 项新 history、12 项原 Registry、6 项独立 probes。
- 后续只把 715468 的 `runner.py` 和 `agent_tasks.py` 两个生产依赖覆入隔离树，
  同一 57 项再通过。它不是完整 715468 HEAD 重跑，也不是新增 57 个 unique cases。
- portable runner 的首次 32 项复核也只在上述两依赖 overlay 上完成。
  本包保留该已实跑 runner 原字节；本地功能结果仍需另行取得。
- 上述结果均不证明 H2a、codec、R1 与 history 的最终组合已通过，不证明
  stock Codex 0.155/0.161、公开 body、分页 RPC、adapter 或 cold-resume 已认证。

## 先查碰撞，再组合

1. 保存实际仓库、HEAD、工作区差异、已合包和 Python 版本。读根 AGENTS.md 与相关
   本地测试说明。完整 HEAD 与 715468 不同时，核清所有相关生产依赖差异；不能仅因
   本包三个旧目标 blob 未变，就称完整 HEAD 已验证。
2. 先核本轮有效输入的 ZIP/patch/source 身份。包含已暂缓旧 R1 的五份历史固定版本
   目标集合经实际比较：
   H1 7、H2a 5、codec 12、R1 6、history 7，共 37 个唯一路径、零路径交集。
   这是这五份历史固定输入的集合结果，不代表 R1 替换包；本地已有改动、更新版本、
   其他待合包仍可能撞同路径。
   对实际全部输入重做 path 并集/交集检查，并检查同文件同段语义修改。
3. 本包原七文件与 patch 必须先独立按 `file-manifest.json` 核验。每个 patch 都先
   `git apply --check`；出现冲突要检查，不可加覆盖参数、全文件拷贝覆盖或删除已有改动。
   已合 H1 不再套用。工作树应在已获准范围内隔离，不能擅自丢弃用户改动。
4. 文档若同路径/同段冲突，在本地合理合并两边有效内容，核双语 name/revision、链接和
   主张，不以任选一版整文件覆盖解决。记录输入 SHA、合并理由、完整 diff、合并后 SHA，
   重新检查文档。生产源、schema、fixtures 和测试不得作为“文档合并”放宽 hash。
5. 对最终组合生成新的 source identity manifest，包含实际 HEAD、输入包/patch SHA、
   每条最终路径 SHA、与原各包 manifest 的差异、解决方式和审阅结论。
   原 frozen manifest 不得被覆盖或改写。任何生产源身份变化都需要独立补丁/审阅和
   对应重跑，不能沿用原通过数。

## history 本地复跑与最终门槛

先完成 `LOCAL_GATE_ZH.md`。`run_local_gate.py` 严格核本包原七文件，并保留已实跑版本。
当前固定输入没有目标路径碰撞，因此不提供 manifest override。若实际 HEAD 或本地
改动需要文档合并，先审阅并记录新合并差异、生成单独 resolved identity manifest。
原 runner 将拒绝不同文档 hash；不能宣称它直接适用于该合并树，也不能跳过身份检查。
应另行审阅适用于新组合的验证方式，保留两生产 Python 与测试 Python 的冻结身份，
再在真实最终树复跑。该情况下须明确本 gate 尚待完成。

在最终完整组合工作树上再次运行 history 的 guarded 32 项，并依各包本地任务书复跑
H2a、codec 的适用离线 gate；R1 等正式替换包后再纳入。记录实际命令、退出码、
JUnit XML、case identities、
源码身份和首错。各包历史通过数不能直接相加充当组合结果。H1 真实 native execution
需按其原授权及环境门槛另验；本包不授权启动 owner/child、模型、Actions、安装、登录、
push 或扩展权限。

最终报告至少分列：输入身份通过/失败、路径碰撞及解决、最终生产源码一致性、文档
差异、每个实际重跑 gate 的 passed/failed/deselected/not-run 和未关闭门槛。

## 下一包

`API_HANDOFF_ZH.md` 给出准确 native API。cut/anchor 仅是需重校的定位，不是 bearer
grant；本包不创建 grant，不返回公开 body。公开 renderer 必须沿既有安全过滤及授权
边界另做独立小包。不得直接序列化内部 user_input/answer 字典，不把实时 TaskControl
状态写进旧快照，也不因 refs-only 测试就声称 Codex 历史分页已经可用。
