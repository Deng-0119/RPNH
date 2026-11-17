# Registry 资料截图 — 2026-11-17

[English](README.md) | [中文](README_ZH.md) · [用户指南](../../../guides/viewer-materials_ZH.md)

这九组图来自同一次 WSL/Linux 真实 Chromium 会话，使用合成本地资料和原 Owner、Registry、HTTP
实现。捕获源码版本为 `851b721dc22ac9796bca5c46e9c6e50f9163f960`。
读取／编辑／导航窗口共六个阶段，101.51 秒完成；该窗口没有真实模型调用。

Raw PNG 是原始截图。Annotated PNG 也保持原样：原驱动在实际 DOM 控件周围绘制红框和编号后
捕获。编号解释控件，不改变图中结果。演示 ID 是合成 Registry 身份。

## 图像与实际覆盖

| 截图 | 标注图／原图 | 编号控件 | 实际观察范围 |
|---|---|---|---|
| S01 — 资料资源 | [标注](S01-resources.annotated.png) · [原图](S01-resources.raw.png) | 1 来源／类型；2 页内筛选；3 刷新；4 资料分组 | 已加载资料页和精确版本分组。 |
| S02 — 精确记录 | [标注](S02-exact-record.annotated.png) · [原图](S02-exact-record.raw.png) | 1 精确记录；2 原字段；3 关闭 | 记录及已声明引用；正文权限另行判断。 |
| S03 — 位置历史 | [标注](S03-position-history.annotated.png) · [原图](S03-position-history.raw.png) | 1 历史；2 返回；3 刷新；4 原绑定 | 一个已观察位置的绑定历史。 |
| S04 — 完整读取 | [标注](S04-complete-read.annotated.png) · [原图](S04-complete-read.raw.png) | 1 读取面板；2 已收到文本；3 编辑收到的文本 | 已收到完整正文，原读取已确认完成。 |
| S05 — 草稿与结果 | [标注](S05-draft-and-result.annotated.png) · [原图](S05-draft-and-result.raw.png) | 1 独立草稿；2 准备／保存／关闭；3 原回执 | V1 修订为已发布 V2，独立 D2 保持未提交。 |
| S06 — 停用 | [标注](S06-disable.annotated.png) · [原图](S06-disable.raw.png) | 1 停用面板；2 查询能力；3 状态 | `management_not_configured`；此截图未执行停用。 |
| S07 — 载体 | [标注](S07-carriers.annotated.png) · [原图](S07-carriers.raw.png) | 1 载体面板；2 关系／状态控件 | `action_unavailable`；此图不能证明载体查看成功。 |
| S08 — 清理 | [标注](S08-cleanup.annotated.png) · [原图](S08-cleanup.raw.png) | 1 单载体面板；2 查询能力；3 状态 | `action_unavailable`；此截图未执行物理清理。 |
| S09 — 窄屏导航 | [标注](S09-narrow-navigation.annotated.png) · [原图](S09-narrow-navigation.raw.png) | 1 来源／类型开关；2 页内筛选 | 真实 390 × 844 视口，中文标签。 |

S01–S08 使用 1440 × 1050 视口。这些是视口截图，不表示整页可放在单屏内。

## 后续终态位置验证

`851b721` 上第一次终态位置操作真实返回 HTTP 409 `target_unavailable`，失败记录仍保留。
后续 `3354e410b04e3f9f2da5a14ae13c63b0385ef2d2` 修复通过了三项安装态权限回归，以及一次
原 Owner／HTTP／浏览器绑定→解绑旅程。后者有十个 HTTP 200 响应，只有两次管理 transition；
业务 generation 与原业务结果保持不变。刷新后的目录与 Registry 确认最终绑定已解除。

上方九图**没有**在 `3354e410` 上重拍，不能改标为最终包完整旅程，也不能当作删除／清理证据。
Viewer 源码修复后来纳入公开源码
[`f4eb4ee2`](https://github.com/Deng-0119/RPNH/commit/f4eb4ee2f77fae79d162644765ebd701d3c576c0)。
这次整合不改变原截图的源码窗口，也不证明后续 wheel／npm 包已完成安装验收。

这组截图不证明用户视觉签收、其它原生平台或完整停用／清理路径通过。
[来源元数据](provenance.json)记录图像身份与范围，不包含本机路径或私有任务链接。
