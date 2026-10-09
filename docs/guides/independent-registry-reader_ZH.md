---
name: rpnh-independent-registry-reader
description: "以独立合法权限打开公共 Registry 读会话和比较查看器。"
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: independent-registry-reader.md
  revision: "2026-10-09.1"
  status: implemented
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

## 用 Python 查询单个产物来源

同一会话还提供固定的 `product_origin_v1` 查询，支持 canonical `petri_output`、
`workspace_write` resource 根和精确 `operation_result/v1` 根。通过已有授权的引用或
reader 取得带 source 身份的精确根；查询不按名称/路径查找，也不推断 source。
Resource 根使用 `SourceQualifiedResourceRef` 包装原两字段 `ResourceVersionRef`；
result 根使用 `SourceQualifiedVersionRef` 包装精确 typed `VersionRef`。

以下应用函数接收已授权的 root 和可信配置路径。`consume_page` 是应用自己的 callback。
示例请求默认三个 includes，保持已捕获 cut，并在每次续页时重复完整请求：

```python
from cpn.rpnh.collaboration import open_read_host_session


def read_product_origin(host_config_path, root, consume_page):
    with open_read_host_session(host_config_path) as session:
        cut = session.capture_cut(root.source_id)
        request = {
            "root": root,
            "at_cut": cut,
            "include": None,
            "page_size": 20,
        }
        cursor = None
        while True:
            page = session.query_product_origin_v1(**request, cursor=cursor)
            consume_page(page)
            cursor = page["continuation"]
            if cursor is None:
                break
```

`include=None` 按顺序包含 `producer_execution`、`start_inputs`、`claims`。显式页大小
`20` 要求 session 最大值至少为 20；HOST 限制可能更严时，可用 `page_size=None`
选择默认 `min(20, effective maximum)`。Profile 的有效最大值是
`min(100, session.limits.max_page_size)`。循环中不得更改 page size 或重新捕获 cut。
等价便捷函数为 `query_product_origin_v1(session, root, cut, include=None,
page_size=None, cursor=None)`。

仅需生产者证明时，使用已经打开的 session 和它签发的 cut：

```python
def read_producer_only(session, root, cut):
    return session.query_product_origin_v1(
        root, cut, include=("producer_execution",)
    )
```

这会返回完整六字段 `root_proof`，不返回行或 continuation；也不查找 Start 或枚举
claim token/消费情况。只有需要相应关系且现有 grant 允许其固定字段时，才另加
`start_inputs` 或 `claims`。默认 includes 需要两组权限；查询不会静默丢弃未授权关系。
所有请求字段权限在对象存在性或依赖完整性检查前预检。
精确字段清单见[会话参考](../reference/registry-read-sessions_ZH.md)。

生产者证明验证必需的 publication、admission、completion、settlement 闭包。
Resource 的 `root_role="registered_output"` 还表示它是 canonical result 输出列表的精确
成员。非成员只有在同样完整闭包成立后才能标记为 `invocation_produced_resource`；
result 根的角色是 `operation_result`。Result 根不需要输出列表权限，也不展开 outputs。

Start 行保留原始位置及实际输入 resource 版本，包括 substitution 和重复 resource。
Claim 行区分 consumed 与 non-consuming claims，resource 可以为 null。披露 claim 的
resource 引用不授权读取目标。这些行不读取业务正文，也不声称输入内容被实际阅读或影响
了模型。

第一页之前，全部请求 candidate 和 endpoint 已完成校验。Partial coverage 只表示已验证
行尚未交付完，不表示证明缺失或扫描失败。每页都重复 proof。Cursor 仅在本 session 内
有效，共用 index-query 槽预算，不能与 index cursor 互换或用于已变更请求。重放复用
已验证 rows，但仍在序列化后检查当前权限和到期时间。

本查询范围有限且为单 source，不枚举递归祖先、其他 outputs、工具调用因果或内容影响。
不支持的 include 返回 `UNSUPPORTED_RELATION`；合法 changed-net settlement 返回
`UNSUPPORTED_SETTLEMENT_SHAPE`。晚 promotion 不改写旧 cut，历史读取仍要求当前权限。
必要 proof 或单行无法放入有界响应时返回 `LIMIT_EXCEEDED`，不先返回前半成功。
此 Python API 不新增比较查看器路由、CLI origin 命令、owner 操作或 Registry 事实写入。

## 比较和验证边界

比较只使用公共读会话。定义、配置、材料、运行态是独立四轴；可靠 mapping、
部分 mapping、完整双网是不同证据模式。人工视觉配对不会变成作者身份。
返回完整拓扑保持原 source cut，并要求整对完整读取权限。

单元/API、安装制品、实际 owner-control 执行和真实浏览器生命周期验证分别
记账。Node DOM 模拟、合成响应、准备成功或返回 owner handle 都不能证明业务
terminal 或真实浏览器验收。此离线流程不调用真实 provider；实际执行项及未
解决 blocker 以注明日期的验证记录为准。
