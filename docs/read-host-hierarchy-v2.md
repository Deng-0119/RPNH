---
name: rpnh-read-host-hierarchy-v2
description: "Organize configured Registry sources without changing existing authorization."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: read-host-hierarchy-v2.zh-CN.md
  revision: "2026-10-28.1"
  status: source-reviewed
---

# Trusted local read HOST hierarchy (configuration v2)

This organizes existing configured Registry sources into groups. It does not
create Registry parent/child authority, nested SourceSets, remote authentication,
new grants, or distributed transactions. Every leaf still resolves through the
original HOST binding and each source's existing authorization. Read responses
retain `global_atomic_snapshot: false`.

V1 configurations are unchanged. For v2 retain the existing `purpose`, `sources`,
optional `limits` / `source_set` fields, change `schema_version` to
`rpnh/registry_read_host_config/v2`, and add:

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

This is a fragment, not a complete runnable config. Both `project-a` and
`project-b` must already have exact `source_ref`, `access_path`, `registry_root`,
`binding_generation`, and owner-issued `observer_context` entries in `sources`.
Use the existing owner-controlled JSON file (0400 or 0600) and existing
`load_read_host_config` / `open_read_host_session` interfaces. Do not submit this
configuration as untrusted HTTP input or put credentials into example files.

Roots follow listed order. A node emits its sources in listed order before
visiting children in listed order. Repeated sources across different nodes emit
once at their first encounter. Repeated roots or duplicate children/sources
within one node are errors. V2 rejects ambiguous source IDs with multiple
concrete bindings; v1 behavior is unchanged.

Unknown names, empty names, cycles, unreachable nodes or sources, and extra
fields are errors. Parsing safety limits are depth 32 across every path, 1024
nodes, and 1024 entries per root/source/child list, in addition to the original
1 MiB config-file and `ReadLimits.max_sources` bounds. These are local config
parsing bounds, not model-call quotas. A DAG reached by both short and long
paths is still checked against its longest path.

The existing full-file identity check revalidates the complete config; editing
the hierarchy invalidates old sessions just like changing a binding. Reopen the
session after any config change. A `source_set` still undergoes the existing
exact manifest membership/access-path verification; grouping does not broaden
its scope. No performance improvement is claimed merely from grouping sources.

中文：[read-host-hierarchy-v2.zh-CN.md](read-host-hierarchy-v2.zh-CN.md)
