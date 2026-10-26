---
name: rpnh-firing-content-contract
description: "验证 firing 产物并读取精确 owner edit 回执。"
metadata:
  document-kind: reference
  audience: developer-and-agent
  language: zh-CN
  counterpart: firing-content-contract.md
  revision: "2026-10-26.1"
  status: current
  basis: "Registry firing content contracts and owner receipts"
---

# Firing 内容契约与 owner 回执

[English](firing-content-contract.md) | 中文

Firing 内容契约在原 Registry 中将不可变来源引用、产物 schema 与 operation 绑定。已注册的 HOST-only checker 验证完整候选产物。AgentLoop 的 `submit_candidate` 先检查，再封存接受的 JSON；拒绝的候选留有记录并可修正。Success 与恢复重新核对原闭包。checker 不另建调度器，也不因返回值授予权限；业务 PN 与执行 PN 保留既有职责。

`sources` 的每项必须提供精确 `resource_ref`，例如 `{"input": {"resource_ref": {"resource_id": "...", "resource_version_id": "..."}}}`。HOST 无须计算或提供来源摘要。来源仍从原 Registry 的 acceptance cut 读取，并检查精确身份、版本、已登记字节封套及 same-task 归属。可信 checker 从 `snapshot["sources"][role]["payload"]` 获取原字节，继续负责领域内容验证；调用方提供的 payload 不能替换 Registry 字节。格式错误的来源声明抛出 `FiringContentContractError`。

已有显式 `sha256` 断言仍须验证；显式 `decoded_sha256` 仍要求来源是 JSON 字符串，并验证解码后的 UTF-8 内容。省略字段不会在来源 snapshot 中补造该字段。这些可选来源断言不改变原生 product/check 记录、schema 与端口检查、checker 绑定、原子 seal、Success 或恢复。HOST 使用 `preflight_candidate(core, execution, products=..., selected_outcome_id=...)` 验证，使用 `seal_candidate(core, execution, products=..., selected_outcome_id=..., idempotency_key=...)` 发布接受的完整产物，再按原方式将返回 authority 传给 `owner.succeed`。

准确 checker ABI 和声明 marker 见 `cpn/rpnh/firing_content_contract.py`；来源覆盖见 `cpn/components/agent_loop/content_coverage.py`。覆盖只能证明已记录的读取和已提交逻辑请求内容，不能证明 provider wire 传输或模型语义使用。

`owner.read_edit_receipt(command_id)` 在固定 Registry cut 读取原 command、result 与 adoption 事实，不重新提交、采用或修复 edit。显式重试相同 `edit` 保留当前公开实现完成 candidate-only 准备的能力。受保护的协作仍要求原授权 action，回执不是编辑许可。

`resume_pending_edit` 是窄范围恢复入口，不是通用失败续跑。获取 writer 前核对精确保留命令与 checkpoint；只有 `RESUMED` 携带 owner，回执本身不证明会话已恢复。它不执行后续 operation。普通续跑仍从有效已提交 checkpoint 恢复，不从报错操作开始。协作管理的 pending 命令不属于此窄入口范围，须保留原授权 owner 流程。参见 [checkpoint 指南](checkpoint-recovery_ZH.md)。
