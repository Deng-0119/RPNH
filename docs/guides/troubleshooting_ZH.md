---
name: rpnh-troubleshoot
description: "Diagnose configuration, ownership, lifecycle and adapter errors without destroying evidence."
metadata:
  document-kind: troubleshooting
  audience: operator-and-developer
  language: zh-CN
  counterpart: troubleshooting.md
  revision: "2026-09-25.2"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](troubleshooting.md) | [中文](troubleshooting_ZH.md)

# 排障与安全恢复

## 先定位失败边界
记录发行/源码版本、前端、精确 profile、命令、退出码和最小脱敏错误。受影响 Registry/run 与 catalog 原件保留在私有位置。先区分安装、配置、准入、物理执行、结算、展示；某层失败不能证明另一层成功。

| 现象 | 首先检查 | 恢复及数据影响 |
|---|---|---|
| 找不到 rpnh 或导入错包 | 环境、entry point、源码目录外的 `cpn.__file__` | 新环境重装批准产物，不删除运行数据 |
| init/doctor/plugins/auto 不认识 | 是否只有 core main，而非引导/插件增量 | 使用 config init/build/use 和 basic，不发明别名 |
| Codex 版本被拒绝 | 精确 `codex-cli 0.155.0` 与 binary 路径 | 使用固定版本或 basic，不改变模型 |
| Codex resume 报告 `persisted_profile_unavailable` | 被省略 thread 原来记录的精确 profile 是否仍已安装且可选 | 恢复同一个用户自有 profile 后再 resume；RPNH 不会换模型，也不会改写该 thread Registry |
| 无 profile、manifest 不一致 | catalog、execution 目录、清单、覆盖变量 | 备份 catalog 后重建，不单改派生文件 |
| 缺凭据、ready false | 变量名及当前进程环境，不显示值 | 在 git 以外配置，离线重查，不默认探测 |
| ready true 但调用失败 | key、精确路由/协议/模型及授权调用证据 | 区分配置和真实可达，不静默 fallback |
| schema/身份不匹配 | 输入 schema、实现或 profile 版本 | 使用兼容配置，不给旧记录改版本标签 |
| owner/writer 冲突 | 原 owner 进程与精确 run 目录 | 通过 owner 停止/协调，不盲目清锁或新开 writer |
| 进程消失却无结果 | terminal evidence、interrupted/unknown 状态 | 不报成功；确认效果边界后才显式恢复 |
| resources-only 空图 | 是否真的声明资源节点 | 无资源时有效，不为展示造节点 |
| DSH 拒绝 provider/model、输入或响应预算 | 离线／配置模式、精确 selection 和整体 2 MiB frame | 使用所选共享 profile；配置工具需要显式纯 native-plugin allowlist，不由 provider 授权 |
| DSH 拒绝受管工具目录 | 绝对路径的 `--plugin-config`、配对的 `--managed-tool NAME=PLUGIN/OPERATION`、唯一小写名称、object 输入 schema 和 pure effect | 修正所选声明／配置，不关闭 guard 或自动挂载任意已安装工具 |
| `managed DSH tool result limit exceeds 64 KiB` | 所选 operation 声明的 `max_result_bytes`，不是某次实际结果大小 | SDK 默认 1 MiB，未修改的 `demo/add` 继承此值。新运行使用显式有界的兼容 operation，不改写旧身份 |
| 工具参数或下一响应预算被拒绝 | 所选输入 schema；工具声明、历史、最坏结果和下一响应 | 执行前修正请求或获准限额；预期结果很小不能覆盖声明的上界 |
| 有关联的 `isError: true` 工具结果 | 已持久登记的 managed-plugin 失败 receipt | 工具执行失败，模型可以处理该失败；不能改标成功值或盲目重试 |
| DSH resume 拒绝 stale execution | 精确 completion 事件、原 registration/profile/catalog、writer 历史、effect/workspace 边界 | 使用原 selection 与 allowlist；缺少合格证据不授权重新运行 provider/tool |
| 空 DSH 会话返回 `failed` 而非 `idle` | 最新 turn 是否真正已经提交 | 这是失败而非成功完成；保留历史，不伪造终态 |
| 插件不解析或 digest 变化 | entry point、版本、handler 字节、配置/资源 | 安装匹配包或新建经批准 run，不绕过恢复身份校验 |

受管工具和 completion 的详细参数与支持边界见
[适配指南](adapters_ZH.md)和 [DSH 指南](dsh_ZH.md)。

## 停止、恢复与未知效果
请求停止不代表 owner 已到安全检查点，应确认 Registry 结果。主 `/rollback` 仅改变主对话权威，保留子证据，不撤销已结算文件、外部写入或模型费用。

超时或传输断开可能使提交/效果状态未知。再次调用前先查物理调用和 operation 状态；“未收到响应”不等于“未执行”。不要给 CLI、插件 handler、DSH bridge 或 owner 命令套通用重试以掩盖失败。

配置 DSH 在模型尝试登记前干净停止，可以按原显式 selection 恢复。精确输出验证后的 `registered_operation_completion_recorded/v1` 可支持对单个 stale running firing 补结算，而不再次调用 HOST/provider/tool。它不涵盖该事件前的任意断点、冲突／后续 writer 事实，也不自动结算声明 HOST effects 或绑定 workspace 的 outcome。见 [runtime 恢复](../reference/runtime-registry_ZH.md)。

完整 provider 原始响应与 operation-completion 事件是不同阶段。`submission_unknown` 或部分响应字节是失败诊断，不是部分成功，也不能证明远端没有执行。provider 权威查询／幂等仅是实际支持它们的具体契约的可选能力，不能由通用兼容 endpoint 推定。

活跃主回合应在同一会话走既有 reconciliation。owner-stopped 子任务恢复自己的 Registry，而不是由聊天记录重建。没有经过明确审查的操作授权，不改变已有精确 provider/model。

## 日志与问题报告
提供预期/实际行为、代码 SHA、平台/Python、去敏命令，以及失败发生在准入前、物理派发后还是结算阶段。只附审查过的最小复现。token 文本、原始输入、prompt、响应、中间值、workspace 和调用日志都可能敏感；仅改文件名或附 hash 不是隐私保证。

不公开密钥、原始 Registry、完整环境变量、签名下载 URL 或私有 endpoint。原始证据保持不变，另做脱敏副本。安全/权限问题在维护者提供私有渠道后私下报告，本文不虚构公开 issue 地址。

## 验证与非目标
先重跑最小相关确定性检查，再考虑已授权的真实复现。链接/站点构建不验证运行恢复。harness 管理结构准入和记录，不是通用业务授权策略、OS sandbox 或自动外部效果补偿服务。

代码边界：`cpn/rpnh/user_config.py`、`control_server.py`、`task_control.py`、`harness.py`、`registry/event_store.py`、`registry/firing_recovery.py`，可选 `cpn/plugins/{api,catalog,managed_tools}.py`、`cpn/dsh/backend.py`、`integrations/dsh/src/app.ts`。另见[架构](../architecture/design_ZH.md)。
