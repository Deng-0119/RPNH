---
name: rpnh-host-adapter-source-validation
description: "适配源码窄增量的验证及发行边界。"
metadata:
  document-kind: reference
  language: zh-CN
  counterpart: host-adapter-source-20261101.md
  revision: "2026-11-01.1"
  status: source-candidate
---

[English](host-adapter-source-20261101.md) | [中文](host-adapter-source-20261101_ZH.md)

# 适配源码进展，不是替换发行物

**本轮main源码在`bc7755d0a0111a0045c86ed4508b11e1cb4a38bc`之上增加修复；已经上传的v0.1.0rc3 Python wheel和0.1.0-rc.3 DSH插件tgz不会随main变化而包含这些新修复。** 本轮不替换发行资产、不新建release、不自动安装、不宣称managed升级资格、不改变默认客户端。不能仅靠版本标签认定源码相同。

## 最小纳入范围

- 普通已准入workspace子进程透传既有invocation/firing版本ID及tool-call ID，仅用于关联；宿主旧环境不能覆盖，无上下文不传ID。Registry/PN准入、权限与隔离保持。相关测试覆盖并发上下文、旧env、writer更替及unknown不重放，不等于owner完整checkpoint/reentry链验收。
- DSH修复派发前取消分类和bridge忽略SIGTERM的等待；升级信号只指向该子进程，exit清timer，不杀worker进程组。已接受TaskControl stop可正常停止worker；UNKNOWN不是成功、回滚或重试指令。
- Codex显式`candidate-0.166.0`保留精确binary/initialize双门与官方schema夹具；恢复启动向客户端传`resume`及精确绑定thread，避免新建第二主会话。默认0.163.0、独立exec JSONL模型后端及工具禁用不变。[显式入口及范围](../guides/codex-candidate-0166_ZH.md)。
- OpenCode默认1.19.4拒绝冲突消息投影，不改无冲突ID，原same-key owner去重保留。独立准入mutex不持事件condition跨gateway回调，避免死锁；它只保护同protocol实例，不冒称跨所有前端/command的全域身份机制。
- OpenCode2.2.5仍是显式选择的**REST/认证/非持久事件子集**，不在production launcher开放，不是完整原生前端。完整catalog、native bootstrap、live durable消息事件**尚未实现**。未支持效果拒绝，不另建事件账本或伪造seq。[子集说明](../reference/opencode-v2-candidate_ZH.md)。

## 分列证据，不累计冒充总验收

历史workspace本地窗口59个不同测试，含真实workspace子进程、本地IPC、并发上下文及atomicity夹具补齐，模型输入为确定性替身。历史适配源码窗口475个不同测试、其中新增59；这个新增59不是前一个本地59。该窗口两项IPC排除后来已在本地进入原测试主体。历史窗口不是本次公版overlay的新通过总数。

后续指定原生窗口34个不同测试（22Python+12Node），原生交互场景另记。DSH使用官方alpha.2 Loader/ApprovalService/Web、候选源代码与复制的operator声明文件，真实TaskControl/Registry及确定性本地模型/返回时机夹具：拒绝/审批取消无派发，允许stop仅一次，晚取消UNKNOWN，对账为`stopped_by_owner`，**不是业务完成**。断连/组件停用窗口没有在途bridge；保持守卫组的测试进程工厂不认证默认脱离进程组worker生命周期。此窗口不是已发布rc3 wheel/tgz重新配对验证。

正式Codex0.166.0两次真实PTY访问闲置已提交历史，实际initialize、发现、历史、冷连和unsubscribe保持同一thread、两turn、四item，无新turn或模型调用。不证明业务checkpoint恢复、活动任务存续、显式object-item游标使用，也未重跑默认0.163原生认证。官方OpenCode1.19.4用production launcher验证输入/历史/冷连，两次确定性后端响应；手工冲突验证另走HTTP/gateway夹具，不假造UI控件。未运行完整2.2.5 TUI。

首失败保留：Codex修复前请求第二thread被拒；DSH合成Plan不合规范发生于UI控制前，原失败Registry未改写成成功；浏览器UID/Unix socket长度环境失败；OpenCode驱动对identity/代理/SSE的错误假设。部分首HTTP响应字节/耗时缺失，仍明确缺失。此前独审也发现v2 ID碰撞、运行时msg_约束、历史模型证据丢失和旧版gateway通知死锁；修后通过不抹去这些缺陷。

## 公版基线overlay检查

隔离overlay保留公开rc3元数据、更新器、宿主pin及原发行文档；不带development、原始证据或无关私有增强。复用既有合规依赖，未安装、调用provider/model、实验外部服务或运行Actions。

本次定向结果：**Python97通过、4失败，32.77秒；Node12通过，2.75秒。** 四失败均为新增launcher组合在AF_UNIX构造时遭PermissionError，尚未进入修改后的spawn路径；没有绕过沙箱或加skip。相同launcher字节和四组合此前在上方独立本地环境通过，不能将该证据改写成本overlay四项通过。97+12为109个不同通过项，不是完整suite。环境Python3.12、pytest8.4.2、websockets15.0.1、Node24.22.1。

从仓库根使用已有合规依赖执行：

```sh
python -m pytest -q tests/test_workspace_call_context.py tests/test_managed_interruption_writer_fencing.py tests/test_workspace_write_atomicity.py tests/test_opencode_v225_candidate.py tests/test_codex_0166_candidate.py tests/test_codex_0166_native_local.py \
 'tests/test_codex_0161_candidate.py::test_launcher_binds_verified_profile_before_transport[None-0.163.0]' \
 'tests/test_codex_0161_candidate.py::test_resolver_checks_only_selected_binary_exact_version[None-codex-cli 0.163.0-True]'
node --test integrations/dsh/operator/transport.test.js
```

各窗口重叠，不累计成全产品评分。managed冷安装/跨版回退仍无合格目标；真实提供商/全产品资格不在通过范围。Registry及两层PN仍负责执行与恢复；前端重连不会自动恢复业务或重放外部效果。
