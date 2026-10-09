# R2 本地 gate（未授权推送）

先在 Deng-0119/RPNH 本地主线核 HEAD、工作区与相关源差异；读取 AGENTS.md。
本次基于已核 715468d 相关源，加 R1 后继 terminal_outcomes 修订，不能用旧冻结R1。
精确依赖见 r1-successor-overlay.json；本增量只有 examples/rsi_workflows 下五个新增文件。
旧R1与R1后继不是重复apply关系，由本地接收者按实际工作区选择修订补丁。

在已经准备好的 Python 测试环境、仓库根运行：

```bash
git status --short
git rev-parse HEAD
git apply --check "$R2_PATCH_FILE"
git apply "$R2_PATCH_FILE"
python -m pytest -q tests/test_iteration_profile.py tests/test_compiler_json_contract.py --junitxml=/tmp/rpnh-r2-compiler.xml
python -m pytest -q examples/rsi_workflows/tests/test_runtime.py -k 'not original_orchestrator' -o junit_family=xunit1 --junitxml=/tmp/rpnh-r2-step.xml
python -m pytest -q examples/rsi_workflows/tests/test_runtime.py -k original_orchestrator -o junit_family=xunit1 --junitxml=/tmp/rpnh-r2-native.xml
```

前两组逻辑测试为 D0；最后一组默认实际AF_UNIX是D1。云端native单项已被EPERM阻止，
不能改权限、ownership、安全守卫、socket实现或隐藏失败获取全绿。在支持AF_UNIX的正常
Linux/WSL2环境补验完整默认四项。零真实模型、API、登录、安装、Actions、push。

若明确要重现云端TEST ONLY替身结果，可单独运行：

```bash
python -m pytest -q examples/rsi_workflows/tests/test_runtime.py --rsi-transport=pipe -o junit_family=xunit1 --junitxml=/tmp/rpnh-r2-pipe.xml
```

必须把pipe另列D0，保留native失败，不得改成native PASS或自动fallback。
pipe fixture为main已存在的examples/tool_pipeline/tests/pipe_transport.py，原样Git blob
910687770c31a4e8ae11009c6352206dd67de94a；R2补丁没有修改/复制一份新transport。

## 本地应核

- final_select/final_retain/stop均来自显式profile mapping；failed原样保留。
- selector缺本轮settled evaluation不可准入；错candidate/父state/轮次不产生selection。
- candidate/evaluation/selection、跨轮state资源refs精确对应；report增删无效。
- domain UNKNOWN正常保留合法incumbent，与物理未知/未settled不同。
- 多轮由原PN/Harness推进，原返回模型调用计数为0，cap=1不解释为一次operation。
- terminal仅由原内核产生，以原read-only reader重建，读前后head/checkpoint/writer不变。

这些用例不覆盖registered-model/scripted port、物理outcome_unknown、owner interruption/
resume、跨进程竞态、author→run绑定/CAS、跨child预算或旧RRSI campaign迁移；相应
R3/R4门槛仍待独立补齐。不得据本包声称模型改进、完整R2或native R3通过。
