# Linux、Viewer 与 npm 源码整合

## 当前发行状态

现在 [rc5 预发布](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc5) 已附 core `0.1.0rc5`、可选 OAuth HOST `0.0.8`、Linux x86_64 CPython 3.12/3.13 Basic 依赖 wheel、两份 v3 清单和 npm 格式 GitHub 附件。这不代表 npm registry 已发布。本地候选安装已通过其限定检查；实际公网应用安装仍待本地验收。dot 尝试在创建应用根目录之前因元数据读取错误退出，另一次只读诊断在 HTTPS 代理 CONNECT 阶段超时。这些结果不能证明原失败原因或公网安装成功。不覆盖此前已发布的wheel二进制。

Linux/npm运行闭包已经进入公开main，基线为 `3d61bcf17b22deee28c5db7a427cda0e81a7f6bd`。2026-11-17的Viewer/查询更新仅整合已审核候选的13个产品文件和13个测试/夹具文件。这是源码更新，不是新的稳定wheel或公网npm发行。已有Release资产保持不变，不能用开发构建覆盖rc3资产。

## 本次内容

npm薄入口仍委托Python bootstrap和原受管安装生命周期。Linux进程监督保留原进程组归属及后代清理。重开会话只观察待执行工作，不暗中重新启动。共享运行绑定、AgentLoop版本化身份、DSH结果、可选MCP/账户提供方适配和skill输入继续基于原Registry/PN。

Viewer更新覆盖Registry导航、资料可用性/正文策略、受管资料位置和连接生命周期。终态位置仍需原合同显式准入，普通executor不会因此获得终态能力。关系查询保留正向/反向/双向过滤、逻辑ObjectRef端点处理和同次读取的关系行复用。不新增平行权限或事实存储机制。

## 已核实的有限范围

- 基线离线证据：67项安装/平台、36项组件、7项Node传输及分发包构建。
- 精确公开基线3d61bcf在2026-11-16的WSL2/Linux三项原生组合全部通过：Registry worker/重放6.15秒、AgentLoop身份/回执33.02秒、DSH有序并行成功与失败72.61秒。使用脚本模型与原夹具传输，不是新一轮真实服务商API或原生DSH前端验收。早期dot启动/诊断失败保留为历史，不再重复列为这三项的待验。
- Viewer/查询整合源码独立20项限定测试通过；准确私有安装包关系14项、版本7项通过。各组有重叠，不相加宣称唯一覆盖数。八文件扩大测试曾被人工中止，不计完整通过。
- 准确私有wheel `0.1.0rc4.dev2026111702`在WSL2/Linux6.6.87.2、CPython3.13.12独立安装后，原三项终态位置原生测试首次全部通过，0失败/错误/跳过，15.273秒；使用真实AF_UNIX OwnerEventLoop，模型调用0。
- 浏览器证据按版本分别保留：早期普通六阶段/九图与后续终态HTTP/DOM流程不是同一源码窗口。最终安装包成功重开已有run及Registry页面，不等同重新验收所有浏览器流程。用户人工视觉签收尚未确认。

## npm与正式发行仍单独收尾

`packaging/npm-bootstrap`仍为`private: true`、`0.0.0-private`。该目录是源码传输模板，不是生成后的rc5发行附件。模板历史说明要求本地controller wheel、持久Python和uv；生成后的附件内嵌匹配的rc5 controller并自动选择Python。目前不声明新的可用公网npm安装命令。

rc5已提供固定版本、匹配controller/application wheel与真实发行元数据/资产。剩余事项是实际公网获取与安装后应用启动验收，再完成npm registry发布及registry路径验证。已有本地tarball激活不等于registry下载或跨发行升级。不得覆盖已有rc3或虚构资产身份。

本轮公开范围是Linux/WSL2；macOS不是本次源码更新的前置，也不由上述结果证明。生产账户提供方接线、完整前端/API认证和旧发行迁移仍应按各自范围处理。

[English](linux-npm-candidate.md)
