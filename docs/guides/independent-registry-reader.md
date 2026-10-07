---
name: rpnh-independent-registry-reader
description: "Open an independently authorized public Registry read session and comparison viewer."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: independent-registry-reader_ZH.md
  revision: "2026-10-07.1"
  status: implementation-candidate
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
