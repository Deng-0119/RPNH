---
name: rpnh-checkpoint-comparison
description: 在同一 Registry、同一 exact 网内只读比较两个精确保存检查点。
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: checkpoint-comparison.md
  revision: "2026-10-09.1"
  status: reference-with-historical-validation-limits
---

[English](checkpoint-comparison.md) | [中文](checkpoint-comparison_ZH.md)

# 只读检查点比较

Viewer 可以比较**同一 Registry、同一 exact 网版本中两个明确选定的保存检查点**。
它只描述已公开的检查点事实，不执行、采用或编辑网，不评测，也不提供授权服务。
不会按同名节点或相同资源正文跨网匹配身份。

## 在 Viewer 中使用

1. 打开目标 run 的现有只读 Viewer
2. 先加载所需历史；“更早记录”在原有 reader limit 内加载更多检查点
3. 点击“比较检查点”，分别选择左右精确检查点；选项显示 commit cut 和完整 checkpoint version ID
4. 点击“读取比较”，一个请求同时返回两侧完整比较结果
5. 点击“关闭比较”丢弃结果并恢复原有实时刷新选择，或导航到其他观察位置

选项来自当前 exact 网已加载的历史及当前显示的检查点。进入旧网段不会凭空生成
该网段的历史索引：首片 UI 在该模式下只提供当前显示的一个检查点。Python/HTTP
接口可选择现有有界前驱链内、属于同一 exact 网的任意两个可达检查点。同一检查点
可选两次，但这不会补齐未知轴。不支持比较的初始声明视图保留原有刷新选择。

打开比较会暂停轮询和回放。更换选择立即清除旧结果并使在途响应失效；重复点击
不会产生多个相同在途请求。关闭、切换看板观察、浏览器前进/后退、离开页面和
恢复页面都会丢弃比较数据。读取失败清除整份旧结果，不回退到缓存或半份结果。
浏览器将页面保存到前进/后退缓存时，保留 renderer 和 resize observer，同时取消
在途读取与计时器；恢复后按所选实时刷新偏好继续轮询，关闭刷新或历史 capture
不会开启轮询。普通离开页面仍释放 renderer。取消上一个网的在途读取会同步释放
导航忙状态；已显示的保存历史 capture 仍须显式返回实时才能继续读取。

离线 Node 测试通过受控 DOM/JointJS/layout 依赖执行完整 app 启动、实际生命周期
handler 和 `NetRenderer`，覆盖重复恢复与迟到响应；这不代表真实浏览器缓存准入、
绘制、无障碍或真实 JointJS 验证通过。

## 实际比较范围

- **公开定义：**公开节点/弧字段及声明的入口、出口、终态规则拓扑；ID 只在同一 exact 网内匹配
- **精确转换绑定：**注册的 node、operation-binding、executable-binding 引用、operation ID 和公开端口记录
- **token 实例：**精确 token version ref、库所、kind、资源引用及检查点中的 active 状态；相同资源不合并实例
- **marking：**epoch、保存 token 总数及 active token 数

每行有两侧事实、字段、主体和 `reason_source=current_analysis`。它描述观察到的
差异，不编造 agent 当时行动的原因。token 出现或 active 状态改变不能单独证明
某次消费、产生、firing 或业务结果成功。

未披露的定义详情、实际模型/工具/workspace/政策、执行活动、资源正文、原生评分
和终态证据保持 `not_provided`。比较不会读取资源正文，也不会重建缺失事实。
公开 executor 声明不等于实际运行时模型或工具证据。HOST 展示注释、agent 注释、
旧 change 记录和不透明 side 扩展不进入比较 DTO。

字段缺失记为 `unavailable`，另一侧为明确 null 时也不能判成相同。完整检查点集合
中只存在于一侧的 token 标为 `present_left_only` 或 `present_right_only`，另一侧
事实是 `absent_at_selected_cut`，不叫“已删除”。任一侧不可读或检查点覆盖不完整时
整份请求失败，不将它当成空集合。“未发现差异”仅限当前公开字段；即使所有已提供
字段相同，整体 coverage 仍为 partial。

## Python 与 HTTP 接口

使用已获得授权的 `RegistryDashboard`。这个功能不建立 principal、reader grant、
Registry writer、HOST 或执行库存。

```python
from cpn.frontend.comparison_view import comparison_view

# 每个 selector 严格是 {"net_ref": exact_net_ref,
#                      "checkpoint_ref": exact_checkpoint_ref,
#                      "cut": complete_checkpoint_commit_ordinal}。
comparison = comparison_view(provider, left_selector, right_selector)
# 等价：provider.comparison_view(
#     left_selector=left_selector, right_selector=right_selector)
```

`GET /api/v2/comparison-view` 与 `HEAD` 只接受两个 query 参数：`left_selector` 和
`right_selector`，值均为 URL 编码的 JSON selector。引用使用规范的精确
`entity_type`、`logical_id`、`version_id`。cut 必须是正的安全整数，并等于对应
checkpoint 的完整提交位置。重复参数、重复 JSON 键、selector 多余字段、隐含
latest 选择和不同 exact net ref 均被拒绝。路由沿用本地 Viewer 边界，响应使用
`Cache-Control: no-store`。

响应版本为 `rpnh/checkpoint_comparison/v1`，固定
`comparison_mode=descriptive`、`comparability=not_established`、
`global_atomic_snapshot=false`。`left` 和 `right` 是收窄的公开 checkpoint envelope，
各自保留精确 selector/cut。共同 `capture` 表示当前可达性与披露核验所用观察，
不会替换两侧历史 cut。两侧分别调用已有 exact checkpoint reader。返回前再次检查
HOST binding、物理 run/DB 身份、task 身份、writer epoch 和 head。本地 inode 检查
可发现来源文件替换，但不是跨主机密码学认证，也不抵御恶意 HOST。

- `400 invalid_query`：选择格式无效或不是同一 exact 网
- `403 access_changed`：当前 binding 或物理来源改变
- `409 stale_observation`：共同 capture 已推进
- `501 unsupported`：provider 未提供比较功能
- `503 read_failed`：某个 exact 检查点无法读取/验证，或其他读取失败

错误只返回固定标识，不披露半份 frame、私有异常正文或来源存在性细节。读取仍受
`max_checkpoints` 限制；超出可达范围的旧 cut 是读取失败，不代表它从未存在。
Registry 推进后可能需要重试同一个明确选择的 pair；重试不会自动换成较新 cut。

服务端从封闭的公开 side 记录重新计算比较响应，客户端也核相同 scope 并重算差异。
未知版本、额外嵌套字段、相互矛盾的 coverage 被拒绝。所有文字和 JSON 用
`textContent` 展示，不当作 HTML、脚本、网络图片、capability 或工具指令。

## 验证范围与历史限制

定向检查：

```sh
python -m pytest -q tests/test_checkpoint_comparison.py
node --test frontend/net-viewer/tests/comparison-view.test.mjs \
  frontend/net-viewer/tests/viewer-lifecycle.test.mjs
```

Python 测试包括不使用 socket 的真实已采用 Registry 读取、合成来源/binding 替换
与披露失败，以及两个使用现有本地 socket fixture 的确定性多检查点集成用例。
禁止 `AF_UNIX` 的 sandbox 无法运行后两个用例，必须记为 blocked，不能报通过。
只运行无需 socket 的部分：

```sh
python -m pytest -q tests/test_checkpoint_comparison.py \
  -k 'not actual_registry_pair and not actual_reader_limit'
```

共用 JSON fixture 由 Python projection 生成、由 Node 消费，核验 wire 兼容性。
相关 checkpoint、导航、token-resource、Viewer 边界和打包检查仍适用。真实浏览器
交互、视觉和无障碍，以及本地 socket 多检查点用例，需在支持它们的环境中验证。
2026-10-06 云端检查中，原有两个多检查点测试在创建 `AF_UNIX` 时遇到
`PermissionError`，尚未进入断言；受支持的云端浏览器无法打开标准本地 Viewer
（`ERR_BLOCKED_BY_CLIENT`）。两项在该历史窗口均为 blocked；无 socket 读取和模拟生命周期
测试通过不能替代它们。
所有比较测试都不需要真实模型 API。本片不新增团队执行、跨来源比较、评分排名、
Registry schema 或可变 Viewer 端点。

本指南保留原 2026-10-06 验证限制，不认证后续源码版本、真实浏览器缓存准入
或组合产品。不同源码窗口见[发布验证边界](release-validation_ZH.md)。
