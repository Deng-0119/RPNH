# 3-DOF 动力下降

[English](README.md) | [中文](README_ZH.md)

这个真实 provider 案例把一个数值最优控制问题交给 RPNH 主 agent，并要求它自行设计
多 agent workflow。案例提供验收方程和独立 verifier，不提供固定 workflow 或 solver
实现。

![实际验收通过的 3-DOF workflow Overview](assets/three-dof-petrinet.png)

## 公开来源

问题类型来源于 Acikmese 与 Ploen 的 “Convex Programming Approach to Powered
Descent Guidance for Mars Landing”，DOI
[10.2514/1.27553](https://doi.org/10.2514/1.27553)。`sources.json` 还记录 SCvx
论文 [arXiv:1804.06539](https://arxiv.org/abs/1804.06539)，以及仅用于参数来源说明的
公开 G-FOLD 数值案例。本仓库不复制上游代码；`problem.json` 独立、精炼地定义动力学、
限制和验收容差。

## 复现

使用 Linux／WSL2、已安装 RPNH 的源码 checkout，以及一份明确授权的 execution
selection。所选 profile 的 workspace 必须能够使用 agent 选择的数值包；本案例通常使用
NumPy/SciPy。下面的命令可能产生多次付费／外部调用；案例不会自行选择或切换
provider/model。

```bash
: "${EXECUTION_CONFIG:?Set an authorized exact execution selection}"
python -c 'import numpy, scipy; print(numpy.__version__, scipy.__version__)'
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-3dof.XXXXXX")"
python examples/three_dof_powered_descent/run.py \
  --execution "$EXECUTION_CONFIG" \
  --session-dir "$DEMO_ROOT/session"
```

runner 把 `task.md`、`problem.json` 和独立 verifier 注册为精确用户任务。主 agent 选择
图，启动一个 child Registry；runner 等待 terminal authority，并打印 task ID、child
`run_dir`、注册结果与 PetriNet 摘要。

无需再次执行模型即可查看真实 run：

```bash
: "${RUN_DIR:?Use the child run_dir printed by run.py}"
rpnh net --run "$RUN_DIR"
rpnh net --run "$RUN_DIR" --show-resources
rpnh net --run "$RUN_DIR" --resources-only
rpnh net --run "$RUN_DIR" --view --no-open
```

本任务没有声明 resource place，因此 `--resources-only` 为空。workflow 必须用随任务提供
的 verifier 检查其 `descent_solution.json`。把已结算 workspace 中的精确文件复制到
Registry 外后，还可以再次检查：

```bash
python examples/three_dof_powered_descent/verify_solution.py \
  /path/to/copied/descent_solution.json
```

## 验收

结果必须满足 verifier 的 `accepted: true` 且无 failure：终端位置误差不超过 0.5 m，
终端速度误差不超过 0.05 m/s，Euler 子步不超过 0.01 s，并满足推力、高度、glide-slope、
速度和干质量限制。`reference_result.json` 记录一条实际可行轨迹（41.67 s，冲量约
395.28 kN·s），只作为对照，不是强制最优值，也不保证任意 solver iterate 都可行。

图拓扑、数值方法和目标值可以不同。Registry terminal evidence 是必要条件，但不能替代
独立数值验收。`validation.json` 只记录脱敏的最终成功边界；原始 Registry、生成轨迹、
transcript、路线和模型身份保留在私有环境。

如果 run 被用户主动停止，请使用单独的
[checkpoint 恢复指南](../../docs/guides/checkpoint-recovery_ZH.md)，继续同一个 child
Registry。
