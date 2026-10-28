---
name: rpnh-read-host-hierarchy-v2
description: "组织已配置的 Registry 来源，不改变既有授权。"
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: read-host-hierarchy-v2.md
  revision: "2026-10-28.1"
  status: source-reviewed
---

# 可信本地读取 HOST 层级配置 v2

本功能仅将已配置的 Registry 来源分组，不建立新的 Registry 父子权威、嵌套 SourceSet、远程认证、授权或分布式事务。每个叶节点仍走原 HOST 绑定和该来源的原权限校验；读取结果仍明确 `global_atomic_snapshot: false`。

v1 配置保持不变。使用 v2 时保留原 `purpose`、`sources`、可选 `limits` / `source_set`，将 `schema_version` 改为 `rpnh/registry_read_host_config/v2`，增加以下片段：

```json
{
  "hierarchy": {
    "roots": ["workspace"],
    "nodes": {
      "workspace": {"sources": ["project-a"], "children": ["references"]},
      "references": {"sources": ["project-b"], "children": []}
    }
  }
}
```

这不是完整可执行配置。`project-a` 和 `project-b` 必须已在原 `sources` 中具备精确的 `source_ref`、`access_path`、`registry_root`、`binding_generation` 和已签发的 `observer_context`。继续使用原所有者控制的 0400/0600 JSON 文件和 `load_read_host_config` / `open_read_host_session`。不得把该配置当 HTTP 非可信请求提交，也不要在示例中放凭据。

顺序：按 roots 列表；每个节点先按顺序输出 sources，再按顺序遍历 children。不同节点重复引用同一来源时仅保留首次位置。同一列表内重复 root/source/child 是错误。v2 拒绝同一 source ID 的多个具体绑定；v1 原行为不变。

未知、空名称、环、不可达节点或来源、多余字段均被拒绝。配置解析安全边界为所有路径深度 32、节点数 1024、单 roots/sources/children 列表 1024 项，并继续受原 1 MiB 文件限制和 `ReadLimits.max_sources` 约束。这不是模型调用次数或实验上限。DAG 同时存在短路径与长路径时仍按最长路径校验。

原完整配置文件身份复验保留；修改层级与修改绑定一样使旧会话失效，需重新打开会话。`source_set` 仍使用原精确成员与访问路径校验，分组不会扩大权限。仅有分组功能不代表性能已提高。

English: [read-host-hierarchy-v2.md](read-host-hierarchy-v2.md)
