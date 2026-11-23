---
name: rpnh-linux-npm-candidate
description: "Linux、Viewer 与 npm 源码整合的发布范围、证据与限制。"
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: linux-npm-candidate.md
  revision: "2026-11-22.1"
  status: source-reviewed
---

[English](linux-npm-candidate.md) | [中文](linux-npm-candidate_ZH.md)

# Linux、Viewer 与 npm 源码整合

## 当前发行状态

[RPNH v0.1.0rc8](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc8) 是 GitHub 预发行；[`rpnh-bootstrap@0.1.0-rc.8`](https://www.npmjs.com/package/rpnh-bootstrap/v/0.1.0-rc.8) 已发布到 npm。在 WSL2 x86_64／CPython 3.13.12 的 Basic 范围内，GitHub 和 npm 两条匿名路径均通过安装、激活和版本回读。使用 Node.js 20+（含 npm）、PATH 中的 `uv` 和支持 `venv` 的持久 CPython。npm 首发时的 `next` 与 `latest` 均指向此预发行，标签不代表稳定版。

```bash
npx rpnh-bootstrap@0.1.0-rc.8
```

在终端中按提示确认 npm 首次下载，再输入 `UPDATE` 安装并激活 Basic。激活后使用程序打印的受管 `rpnh` 启动器路径。此流程不运行 `rpnh init`，不登录或调用模型。

全局引导器备选：

```bash
npm install -g rpnh-bootstrap@0.1.0-rc.8
rpnh-bootstrap
```

[rc6 已在 GitHub 预发布](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc6)，包含匹配的 Linux x86_64 Basic 应用 wheel、CPython 3.12/3.13 依赖 wheel、两份 v3 清单，以及 npm 格式的 GitHub 附件。该候选已通过限定的离线 CLI 检查。首次匿名发行元数据已回读，但该次下载在 GitHub 重定向后超时，原始失败继续保留。正式 rc6 npm 格式 tgz 后续已在 WSL2／CPython 3.13.12 Basic 通过 A4 安装与激活验收。这是历史 RC6 证据，不代表 RC6 已发布到 npm registry。本结果不证明升级、回滚、可选前端或 macOS 支持。

Linux/npm运行闭包已经进入公开main，基线为 `3d61bcf17b22deee28c5db7a427cda0e81a7f6bd`。2026-11-17的Viewer/查询更新仅整合已审核候选的13个产品文件和13个测试/夹具文件。这条 11 月 17 日记录仅指历史源码更新，公网 npm 发行状态见上文。已有Release资产保持不变，不能用开发构建覆盖rc3资产。

## 本次内容

npm薄入口仍委托Python bootstrap和原受管安装生命周期。Linux进程监督保留原进程组归属及后代清理。重开会话只观察待执行工作，不暗中重新启动。共享运行绑定、AgentLoop版本化身份、DSH结果、可选MCP/账户提供方适配和skill输入继续基于原Registry/PN。

Viewer更新覆盖Registry导航、资料可用性/正文策略、受管资料位置和连接生命周期。终态位置仍需原合同显式准入，普通executor不会因此获得终态能力。关系查询保留正向/反向/双向过滤、逻辑ObjectRef端点处理和同次读取的关系行复用。不新增平行权限或事实存储机制。

## 已核实的有限范围

- 基线离线证据：67项安装/平台、36项组件、7项Node传输及分发包构建。
- 精确公开基线3d61bcf在2026-11-16的WSL2/Linux三项原生组合全部通过：Registry worker/重放6.15秒、AgentLoop身份/回执33.02秒、DSH有序并行成功与失败72.61秒。使用脚本模型与原夹具传输，不是新一轮真实服务商API或原生DSH前端验收。早期dot启动/诊断失败保留为历史，不再重复列为这三项的待验。
- Viewer/查询整合源码独立20项限定测试通过；准确私有安装包关系14项、版本7项通过。各组有重叠，不相加宣称唯一覆盖数。八文件扩大测试曾被人工中止，不计完整通过。
- 准确私有wheel `0.1.0rc4.dev2026111702`在WSL2/Linux6.6.87.2、CPython3.13.12独立安装后，原三项终态位置原生测试首次全部通过，0失败/错误/跳过，15.273秒；使用真实AF_UNIX OwnerEventLoop，模型调用0。
- 浏览器证据按版本分别保留：早期普通六阶段/九图与后续终态HTTP/DOM流程不是同一源码窗口。最终安装包成功重开已有run及Registry页面，不等同重新验收所有浏览器流程。用户人工视觉签收尚未确认。

## 源码模板与已发布安装包

`packaging/npm-bootstrap` 是私有源码模板（`0.0.0-private`），不是已发布的 npm 包。验收时 RC8 正式 GitHub 附件与 npm tarball 逐字节一致。已有发行资产保持不变；全新安装证据不覆盖原地升级或回滚。

本轮公开范围是Linux/WSL2；macOS不是本次源码更新的前置，也不由上述结果证明。生产账户提供方接线、完整前端/API认证和旧发行迁移仍应按各自范围处理。

[English](linux-npm-candidate.md)
