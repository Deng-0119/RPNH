---
name: rpnh-viewer-user-contributions
description: "在实际采用的 Petri 网中查看精确的提议贡献记录。"
metadata:
  document-kind: guide
  audience: trusted-host-developer
  language: zh-CN
  counterpart: viewer-user-contributions.md
  revision: "2026-10-12.1"
  status: maintained
  basis: "original Registry facts; existing HOST and read-session authority"
---

[English](viewer-user-contributions.md) | [中文](viewer-user-contributions_ZH.md)

# 实际 Viewer 中的贡献记录

“用户贡献”面板直接叠加在现有完整 Petri 网中。选择记录中的 actor 后，其余节点变淡，
完整图的上下文仍保留。在面板点击节点可定位实际 PN 节点，查看变更字段路径、精确的
proposal/base/candidate 引用，以及独立的审阅和采用记录。继承贡献保留提议者颜色，
使用虚线；多个保留提议者使用重叠样式；未知来源为灰色。运行状态的文字和填充色
与贡献轮廓分开。

记录中的 actor 是可信本地 HOST session 标签，不是外部账号认证，也不能证明此人
亲自编写了内容。面板称为“记录中的提议者”；作者身份和人类执行者身份保持未知，
除非未来有另外经过验证的事实契约支持。发布 principal、模型、provider 或 executor
均不能推导作者。一个变更的 author element 可能映射多个 PN 节点，不应统计成多份
独立贡献或理解为节点所有权。

## 可信 HOST 接入

在可信 HOST 中使用现有、已获授权的对象：

```python
from cpn.frontend.dashboard import RegistryDashboard
from cpn.frontend.user_contributions import UserContributionView
from cpn.frontend.server import serve_projection

view = UserContributionView(
    collaboration_host, collaboration_session, registry_read_session,
    source_id=selected_source_id,
)
provider = RegistryDashboard(
    existing_run_dir, catalog=existing_catalog,
    user_contribution_view=view,
)
serve_projection(provider, open_browser=False)
```

HOST/session 必须保留原目标 Core 绑定。原 Registry read session 必须已经允许读取
选定的运行检查点、plain 修订描述符、存储的公开 PN 投影及其精确材料闭包。本适配器
不会签发授权、创建 session、扩大权限、从 JSON 推断凭据、注册事实、编译 PN 或执行
任务。未配置时返回 501；权限变化或到期后浏览器清除叠加信息。不要把 HOST 对象
暴露给模型工具。

独立 Viewer 无法仅从 run 路径恢复可信 session handle；未显式接入时会显示未配置。
历史运行需要保留的原始 Registry 和可信 HOST/read 配置。PNG、导出的 Module、展示
名称和只供展示的汇总 JSON 无法补造缺失的 actor receipt。

## 精确含义和当前边界

- 每次请求锁定一个 net、checkpoint 及检查点完整提交序号。
- 采用需要原始 canonical receipt、decision 和精确结果 net/revision。
- 提议差异来自精确 base/candidate author element；只有沿显式 parent 路径保留的值
  才会标色。未选提议即使值相同，也不会获得贡献归属。
- 原存储投影验证提供实际 element→node lowering；没有示例专用 batch 映射或重画的网。
- 首版支持本地 parent 历史的 plain closed v1 修订；跨 source 和 selected-change
  历史不支持。
- 投影 operation、port 和 component 差异；已移除元素及仅 module/edge 的变化没有
  节点叠加。缺失来源保持未知；继承的 baseline 不会被算作采用者编写。
- 详情展示变更字段路径，不展示原始 config 值、提示词或工具输出。结果是可丢弃的
  观察投影，不是第二本事实账。

读取受事实、材料、修订和响应大小上限约束。重复读取重新检查原有两层权限；切换
source/检查点会使浏览器缓存失效。查看保留的事实不需要 provider/model 调用。
