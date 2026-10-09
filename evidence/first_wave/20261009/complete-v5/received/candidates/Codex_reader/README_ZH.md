# MainThread 固定 cut：本地验证交付包

交付冻结的七文件 native refs-only patch，加便携本地验收说明。原 patch 和七份候选源码
一字节未改。此包不含完整 runtime、数据库、真实 Registry、凭据、venv 或安装包。

先读 `LOCAL_GATE_ZH.md`，再读 `COMBINATION_GATE_ZH.md`。后续公开 renderer 与 adapter
边界见 `API_HANDOFF_ZH.md`、`ADAPTER_NEXT_ZH.md`。

原基底：ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4。
已核后继：715468dab0b1bea07d7e94a7aa0606eaf194365c，三个既有目标 blob 未改；
后续 57 项和 portable 32 项只在两生产依赖 overlay 上执行，不是完整 HEAD/组合重跑。
原 H1 已入该后继，不要重套。H2a、codec 需各自 gate；旧 R1 暂缓，必须等替换包。

## 两种检查

1. 包装自检：已有 Python 3.11+ 和 git 可运行：

   `"$PYTHON" "$PACKAGE/verify_package.py"`

   它只核 SHA256SUMS、七文件/blob、在临时目录 apply patch、解析既有 XML 和对比固定
   输入路径；不加载项目代码，也不运行项目测试。需要允许的临时目录，不安装任何东西。
   输出的 JUnit 数字是核验旧记录，不能称为本机新测试结果。

2. 本地功能 gate：在已有完整、获准 worktree 上按 LOCAL_GATE 执行 `run_local_gate.py`。
   source/ 只有七个目标文件，baseline/ 只有三个原文件，不能直接把本包当项目运行。
   runner 用已有 pytest/jsonschema；禁止外部执行，不自动装依赖。

## 证据解释

- final-tests.xml：57 passed；另 9 deselected 见清单和日志。
- latest-main-tests.xml：同一 57 项，两依赖 overlay，不是另 57 个 unique tests。
- independent-review/results.xml：32 passed。
- portable-gate-validation.xml：便携 runner 首版 32 passed，只覆盖两依赖 overlay。
- 初始失败日志/XML是留存的修复过程，不计最终通过数。
- 上述数字不能直接相加。本包不认证公开 body、Codex RPC/客户端或 cold resume。

`COMMANDS.md`、独审报告、freeze.log 等是历史执行记录，云端命令不是本地步骤。
`independent-review/run_guarded.py` 是旧证据 runner；它曾依赖完整 source/，本地请用
新的 run_local_gate.py。旧 freeze.py 是重建工具且会改写冻结工件，未随本交付包提供。

包装时删除了重复的临时 apply 副本；证据中的云端根路径被替换，JUnit 的 hostname
被移除。case identity、结果计数、时间和失败状态保留。转换明细及原/现 SHA 见
`packaging-evidence-transformations.json`，核验范围见 `PACKAGING_QA.json`。
原始候选 source、baseline 和 patch 未经过脱敏或重写。
