---
name: rpnh-independent-registry-reader
description: "以独立合法权限打开公共 Registry 读会话和比较查看器。"
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: independent-registry-reader.md
  revision: "2026-10-07.1"
  status: implementation-candidate
  basis: "explicit trusted local HOST; existing owner-issued authority"
---

[English](independent-registry-reader.md) | [中文](independent-registry-reader_ZH.md)

# 独立 Registry 读取和比较入口

安装后执行 `rpnh net --read-host-config FILE --view`，可从独立可信本机配置
打开有限生命周期的公共读会话。`--run` 与 `--read-host-config` 互斥。
独立比较 provider 不提供旧版原始网图路由；原有 `rpnh net --run RUN_DIR`
行为仍单独保留。

这是显式的同一 OS 用户 HOST 边界，不是远程或多租户身份认证。命令不签发
grant，也不取得 writer。既有源 owner 须先通过 `issue_observer_access` 签发
精确的临时 observer context，再向接收 HOST 提供配置。仅将 context 放进文件
不构成权限：公共会话在每次交付前检查源中的规范 grant/profile、精确 task、
purpose、到期时间、fencing、撤权及字段/正文/导出范围。

## 可信本机文件

版本为 `rpnh/registry_read_host_config/v1`，顶层必需字段为 `schema_version`、
`purpose`、`sources`，可选字段为 `limits`、`source_set`。未知或重复字段会被
拒绝。每个 source 包含：

- `source_ref`：精确带源身份的 `task/v1` 引用
- `access_path`：显式预配置的访问路径
- `registry_root`：绝对本机源目录，路径组件不得是符号链接
- `binding_generation`：接收端为该绑定给出的非空身份
- `observer_context`：保持原样的既有 owner 签发 v2 observer context

可选 `source_set` 严格包含 `source_set_ref`、`registry_root`、
`binding_generation`。它只约束所选源，不授予访问权限。

配置必须为当前有效 OS 用户拥有的普通非符号链接文件，权限为 `0400` 或
`0600`。父目录链须由 root/当前用户拥有，且不可由其他用户写入；root 拥有的
sticky 临时目录除外。文件上限为一 MiB，通过同一经检查 inode 读取，每次解析
源之前复核身份、内容和权限。替换或编辑文件会使会话失效；应根据新获 owner
批准的配置显式重开。公共响应不包含本机路径或 grant 对象。

CLI 从 OS 派生 caller 身份，不接受请求传入 principal、callback、任意导入或
模块名、自签 grant、plugin discovery 或自动目录扫描。只选择安装版有限 schema
清单及 typed readers。自定义可信 HOST 可显式组合高级公共
`RegistryReadHostBinding` API，但必须维护同样的独立权限和末次校验边界。

## 公共 Python 边界

可将 `open_read_host_session(path)` 用作上下文管理器；或构造类型化
`ReadSessionRequest` 和 `RegistryReadHostBinding`，再调用
`open_registry_session(request, host=...)`。可信 source resolver 使用
`open_readonly_source(path, catalog=...)`；普通公共 adapter 不应穿透 Registry
私有实现。会话提供类型化 `query_index`、`read_exact`、显式 `read_material`、
`capture_cut`、末次权限校验和 `close`。索引元数据权限不授权正文或导出。

源选择及 source cut 均显式给出。分页固定于各源已捕获的 cut；后续 append
不会迁移页面。多源结果不是全局原子快照。源不可用、字段未披露、unknown
和记录不存在保持区别。query 不发布 Observation，也不改变 Registry 事实；
SQLite 只读 WAL sidecar 行为不冒充规范事实写入。

## 比较和验证边界

比较只使用公共读会话。定义、配置、材料、运行态是独立四轴；可靠 mapping、
部分 mapping、完整双网是不同证据模式。人工视觉配对不会变成作者身份。
返回完整拓扑保持原 source cut，并要求整对完整读取权限。

单元/API、安装制品、实际 owner-control 执行和真实浏览器生命周期验证分别
记账。Node DOM 模拟、合成响应、准备成功或返回 owner handle 都不能证明业务
terminal 或真实浏览器验收。此离线流程不调用真实 provider；实际执行项及未
解决 blocker 以注明日期的验证记录为准。
