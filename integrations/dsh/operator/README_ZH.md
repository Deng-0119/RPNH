---
metadata:
  document-kind: how-to
  language: zh
  counterpart: README.md
  status: prerelease
---

[English](README.md) | [中文](README_ZH.md)

# RPNH for DSH：既有任务操作插件

**RPNH operator 0.1.0-rc.3 + RPNH Python 0.1.0rc3** 独立适配
**DeepSeek Harness 0.2.2-alpha.2**，是第三方 RPNH 适配，不是 DeepSeek 官方产品
或背书。保留上游品牌与许可；界面明确显示 **RPNH 适配版 / for DeepSeek Harness**，
分别展示 operator 与 DSH 版本。两者均为预发布。

这是手动安装的既有任务插件，不替代 DSH runtime、不提供任务创建 UI，也不是
已具备 managed 自动升级资格的发行。

## 精确依赖与下载

- Linux 或 WSL2，Python >=3.11，Node >=24.9.0。
- owner 管理的 **DSH 0.2.2-alpha.2** 宿主；不得静默替换其他版本。
  源码标签 `dsh-v0.2.2-alpha.2`，源码提交
  `f3803c96974ad4a94195fb97e5c637e9c2a3afe3`。npm 包内嵌构建身份与此源码 pin 分开。
- 精确 peers：Cordis `4.0.6-alpha.1`、dsh-tools `0.2.2-alpha.2`、
  Schemastery `3.18.6-alpha.1`。客户端 React/服务由宿主提供；tarball 不捆绑这些依赖或 Python。

从 [RPNH v0.1.0rc3](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3)
取得**匹配配套**：

1. [Python wheel](https://github.com/Deng-0119/RPNH/releases/download/v0.1.0rc3/rpnh_harness-0.1.0rc3-py3-none-any.whl)
2. [operator tarball](https://github.com/Deng-0119/RPNH/releases/download/v0.1.0rc3/dsh-rpnh-operator-0.1.0-rc.3.tgz)

若任一资产缺失则停止，不用旧包或同名包代替。保留旧环境、profile 与 Registry。

## 安装到独立环境

下载两个资产到 owner 控制目录，在该目录执行：

```sh
python3 -m venv .venv-rpnh-stock
.venv-rpnh-stock/bin/python -m pip install ./rpnh_harness-0.1.0rc3-py3-none-any.whl
export RPNH_PYTHON="$(pwd)/.venv-rpnh-stock/bin/python"
export RPNH_CONTROL_ROOT=/absolute/path/to/your/existing/task-control
# 在精确 alpha.2 DSH 环境中：
dsh plugin --profile rpnh-operator add "$(pwd)/dsh-rpnh-operator-0.1.0-rc.3.tgz"
dsh --profile rpnh-operator --dump-config
```

官方 profile 流程加载包内 `cordis.patch.yml`；整个 RPNH 仓库根不是 bundle。
`RPNH_PYTHON` 必须为匹配、非 editable 安装的绝对解释器路径；`RPNH_CONTROL_ROOT`
必须是已存在、owner 控制的绝对目录。桥接采用 `python -I -m cpn.dsh.operator`，
不通过 cwd/PYTHONPATH 选择模块。

patch 读取上述两个环境变量。覆盖配置时保留 `rpnh-operator` 行 id 和包名。
下面只是配置片段，不是完整 profile：

```yaml
controlRoot: /absolute/path/to/your/existing/task-control
python: /absolute/path/to/.venv-rpnh-stock/bin/python
enableControl: false
```

先用只读配置。新空目录可验证空列表，但不是已有任务库。必须先经原 RPNH
CLI/Python TaskControl 流程建立任务；插件加载/读取不会创建任务或恢复 pending
launch。参见[checkpoint 与恢复指南](https://github.com/Deng-0119/RPNH/blob/v0.1.0rc3/docs/guides/checkpoint-recovery_ZH.md)。

## 原权威、控制与未知结果

五个读工具均以 `rpnh_task_` 开头：list、status、result、net、checkpoints。
owner 显式 `enableControl: true` 才增加 stop、resume、reopen，沿用 DSH 原审批。
只在已有授权下启用；resume/reopen 可继续原已授权模型与外部工具工作。已有 host
拒绝不会被覆盖，不新建 Registry、PN、调度器或权限库。

结果及图引用来自原 TaskControl/Registry 事实。普通 resume 选最近有效 committed
checkpoint；显式 reopen 选早期已提交切点并保留后续历史，不撤销外部效果。
`STOP_REQUESTED` 不表示 drain 已完成。

派发前取消为 `NOT_DISPATCHED`；派发后取消或丢失响应为 `OUTCOME_UNKNOWN`：
先查原任务状态/checkpoint，不自动重放写操作。取消桥接等待不会停止独立 worker；
卸载插件同样不停止已有任务。

## 手动更新与卸载

原生 RPNH 版本面板区分上游与适配。存在原生 Connection 时，统一 Python checker
可查询合格发行；`RPNH_UPDATES=off` 禁用自动检查。本版没有合格自动更新 offer，
也不声明 managed 安装能力。

将来有合格 offer 时，owner-only 原生接口提供更新/暂不/跳过版本。对 stock 宿主，
更新仅展示匹配包的手动指引，不热安装、重启 worker、迁移数据或恢复任务。
手动替换配套前退出对应宿主。卸载使用官方 profile 包管理流程，保留任务数据。

## 验证边界

[发行验证摘要](https://github.com/Deng-0119/RPNH/blob/v0.1.0rc3/docs/validation/dsh-stock-0.1.0rc3_ZH.md)仅说明最终配套的限定
检查。空列表 Loader 检查不证明真实任务完成、UI、审批、worker恢复或模型质量。
不据此认定 managed 冷安装、升级、回退通过。operator tarball 不含测试或私有运行记录。
