---
name: rpnh-independent-registry-reader
description: "Open an independently authorized public Registry read session and comparison viewer."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: independent-registry-reader_ZH.md
  revision: "2026-10-25.1"
  status: implemented
  basis: "explicit trusted local HOST; existing owner-issued authority"
---

[English](independent-registry-reader.md) | [中文](independent-registry-reader_ZH.md)

# Independent Registry reader and comparison entry

The installed entry `rpnh net --read-host-config FILE --view` opens a finite
public read session from a separate trusted local configuration. `--run` and
`--read-host-config` are mutually exclusive. The independent comparison provider
does not expose the legacy raw-net route. The original `rpnh net --run RUN_DIR`
behavior remains available separately.

This is an explicit same-OS-user HOST boundary, not remote or multi-tenant
identity authentication. The command does not create grants or obtain a writer.
An existing source owner must first issue the exact temporary observer context
through `issue_observer_access` and give the receiving HOST its configuration.
A context copied into a file is insufficient by itself: the public session
checks the canonical source grant, profile, exact task, purpose, expiry, fencing,
revocation and requested field/body/export scope on every delivery.

## Trusted local file

The version is `rpnh/registry_read_host_config/v1`. Its required top-level fields
are `schema_version`, `purpose`, and `sources`; optional fields are `limits` and
`source_set`. Unknown or duplicate fields are rejected. Every source contains:

- `source_ref`: exact source-qualified `task/v1` reference
- `access_path`: explicit preconfigured access path
- `registry_root`: absolute local source directory, with no symlink components
- `binding_generation`: nonempty receiver-owned identity for this binding
- `observer_context`: the unchanged existing owner-issued v2 observer context

An optional `source_set` has exactly `source_set_ref`, `registry_root`, and
`binding_generation`. It constrains the selected sources; it grants no access.

The configuration must be a regular nonsymlink file owned by the effective OS
user with mode `0400` or `0600`. Its parent chain must be root/user-owned and
not writable by other users, except root-owned sticky temporary directories.
The file is bounded to one MiB, read from one checked inode, and rechecked for
identity, contents and permissions before each source resolution. Replacing or
editing it invalidates the session; reopen explicitly with new owner-approved
configuration. Public responses never include its local paths or grant objects.

The CLI derives the caller identity from the OS. There is no request-provided
principal, callback, arbitrary import/module name, self-issued grant, plugin
discovery or automatic source-directory scan. Only the installed finite schema
inventory and typed readers are selected. Custom HOST applications may compose
the advanced public `RegistryReadHostBinding` API explicitly; they must maintain
the same independent authority and final-guard boundaries.

## Public Python boundary

Use `open_read_host_session(path)` as a context manager, or build a typed
`ReadSessionRequest` and `RegistryReadHostBinding`, then call
`open_registry_session(request, host=...)`. A trusted source resolver uses
`open_readonly_source(path, catalog=...)`; ordinary public adapters must not
reach through private Registry internals. Sessions offer typed `query_index`,
`read_exact`, explicit `read_material`, `capture_cut`, final guards, and `close`.
Index metadata permission does not authorize material bytes or export.

Selections and source cuts are explicit. Pagination remains fixed at the
captured per-source cut; a later append does not migrate the page. A multi-source
result is not a globally atomic snapshot. Unavailable sources, denied fields,
unknown values and absent records remain distinct. Querying does not publish an
Observation or alter Registry facts; SQLite read-only WAL sidecar behavior is
not confused with canonical fact writes.

## Stream one product's origin from the CLI

Use the installed `rpnh origin` command with an existing owner-approved HOST
configuration and an already authorized exact root reference:

```bash
rpnh origin --read-host-config HOST.json --root ROOT.json --page-size 1
```

`ROOT.json` contains the unchanged wire form of either
`SourceQualifiedResourceRef` (`rpnh/collaboration/source_resource_ref/v1`, with
`source_id` and `ref {resource_id, resource_version_id}`) or
`SourceQualifiedVersionRef` (`rpnh/collaboration/source_version_ref/v1`, with
`source_id` and `ref {entity_type, logical_id, version_id}`). The latter must be
an `operation_result/v1` with `operation_result` / `operation_result_version`
ID kinds. Each form also requires its exact `schema_version`. These are the
existing [resource reference](../../cpn/schemas/rpnh/collaboration/source_resource_ref.v1.schema.json)
and [version reference](../../cpn/schemas/rpnh/collaboration/source_version_ref.v1.schema.json)
contracts, not new lookup requests. Root input is a regular UTF-8 JSON file,
bounded to 64 KiB before parsing and to the HOST's effective query-byte limit
before capturing a cut. Duplicate keys, unknown fields, missing sources, bare
IDs, names, paths, `latest`, and generic resource `VersionRef` roots are rejected.

Without `--include`, the command preserves all three default relations:
`producer_execution`, `start_inputs`, and `claims`. An explicit selection uses
repeated `--include RELATION` options, contains unique values, and must contain
`producer_execution`. For example, producer-only delivery is:

```bash
rpnh origin --read-host-config HOST.json --root ROOT.json \
  --include producer_execution
```

`--page-size N` uses the same effective maximum and default as the Python query;
it does not change the cut or the selected relations. The command opens one
public read session using the fixed built-in catalog, captures one cut for the
root's explicit source, and follows only continuations from that same request.
It does not open a writer, read material bodies, select arbitrary modules or
catalogs, discover a source, or obtain new authority.

Stdout is NDJSON: each line is one exact original page serialized as canonical
compact ASCII JSON, including its repeated proof, coverage and original
continuation. There is no aggregate wrapper. The current page's transport
buffer is bounded by `session.limits.max_response_bytes`, plus one newline.
After serialization, the CLI calls the public `final_recheck` for the selected
source, writes and flushes the line, then discards the page and transport buffer
before requesting another page. Earlier authorized lines can remain if a later
page or sink fails; a sink failure can also leave an unfinished last line.

Exit `0` means the terminal page was flushed and the session closed. Any nonzero
exit means an incomplete stream, even if some valid lines were received. Stderr
uses fixed safe codes such as `INVALID_ROOT`, `INVALID_ARGUMENTS`,
`ACCESS_CHANGED`, `LIMIT_EXCEEDED`, and `OUTPUT_FAILED`; it never echoes exception
messages or local paths. There is no external cursor input, resume file,
automatic reopen, or persistent cursor service. Printed continuations stop being
usable when this process closes its session. Restarting is a new read with a new
cut and current authority, not continuation of an old stream.

## Query one product's origin in Python

The same session also offers the fixed `product_origin_v1` query. It supports
canonical `petri_output` and `workspace_write` resource roots, and exact
`operation_result/v1` roots. Obtain the exact source-qualified root through an
already authorized reference or reader; no name/path lookup or source inference
is performed. A resource root uses `SourceQualifiedResourceRef` around the
original two-field `ResourceVersionRef`. A result root uses
`SourceQualifiedVersionRef` around its exact typed `VersionRef`.

The following application function takes a previously authorized root and the
trusted configuration path. `consume_page` is your application callback. It uses
the default three includes, keeps the captured cut fixed, and repeats the full
request on every continuation:

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

`include=None` means `producer_execution`, `start_inputs`, and `claims`, in that
order. The explicit page size `20` requires a session maximum of at least 20;
use `page_size=None` to select the default `min(20, effective maximum)` when HOST
limits may be tighter. The profile's effective maximum is
`min(100, session.limits.max_page_size)`. Do not change page size or recapture the
cut halfway through this loop. The equivalent convenience function is
`query_product_origin_v1(session, root, cut, include=None, page_size=None,
cursor=None)`.

For a strictly producer-only request, use an already open session and its cut:

```python
def read_producer_only(session, root, cut):
    return session.query_product_origin_v1(
        root, cut, include=("producer_execution",)
    )
```

This returns the complete six-field `root_proof`, no rows, and no continuation.
It does not look for Start or enumerate claim tokens/consumption. Add
`start_inputs` or `claims` only when you want that relation and the existing
grant allows its fixed fields. Default includes need both sets of permissions;
the query never silently drops an unauthorized relation. All requested field
permissions are checked before object existence or dependency integrity.
The exact field list is in the
[session reference](../reference/registry-read-sessions.md).

The producer proof verifies the required publication, admission, completion, and
settlement closure. A resource's `root_role="registered_output"` additionally
means exact membership in the canonical result's output list. A nonmember may
be `invocation_produced_resource` only after the same complete closure succeeds;
a result root is `operation_result`. Result roots need no output-list permission
and do not expand outputs.

Start rows retain original positions and actual input resource versions, including
substitution and repeated resources. Claim rows distinguish consumed from
non-consuming claims and may have a null resource. A disclosed claim resource
reference does not authorize reading its target. These rows never read business
material or assert that input content was actually read or influenced a model.

Every requested candidate and endpoint is validated before page one. A partial
coverage state means only that validated rows remain to be delivered; it does
not mean missing proof or a failed scan. The proof repeats on each page. Cursors
are session-local, share the index-query slot budget, and cannot be exchanged
with index cursors or used with a changed request. Replay reuses the verified
rows but still checks current permission and expiry after serialization.

The query is finite and single-source. It does not enumerate recursive ancestry,
other outputs, tool-call causality, or content influence. Unsupported includes
return `UNSUPPORTED_RELATION`. Legal changed-net settlement returns
`UNSUPPORTED_SETTLEMENT_SHAPE`. Later promotion never rewrites an old cut;
historical reads still require current authority. A necessary proof or row that
cannot fit the bounded response returns `LIMIT_EXCEEDED`, without a partial
success prefix. The query adds no comparison-viewer route, owner action, or
Registry fact write. The installed `rpnh origin` CLI is only a transport over
this same public query.

## Comparison and verification limits

Comparison uses only the public read session. Definition, configuration,
materials and runtime are separate axes. Reliable mapping, partial mapping and
full-pair display are different evidence modes. A manual visual pair never
becomes author identity. Navigating back to full topology retains the original
source cuts and requires permission for the complete pair.

Unit/API checks, installed-artifact checks, actual owner-control execution and
actual-browser lifecycle checks are reported separately. A Node DOM simulation,
synthetic response, successful preparation, or returned owner handle is not
proof of a business terminal or real-browser acceptance. No real provider call
is part of this offline flow. Consult the dated validation record for executed
checks and unresolved blockers.

