---
name: rpnh-checkpoint-comparison
description: Compare two exact saved checkpoints in one Registry and one exact net, read-only.
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: en
  counterpart: checkpoint-comparison_ZH.md
  revision: "2026-10-09.1"
  status: reference-with-historical-validation-limits
---

[English](checkpoint-comparison.md) | [中文](checkpoint-comparison_ZH.md)

# Read-only checkpoint comparison

The viewer can compare **two explicitly selected saved checkpoints from the same
Registry and the same exact net version**. This is a descriptive display of public
checkpoint facts, not execution, adoption, editing, evaluation, or an authority
service. It does not match nodes across different nets by name or resource bytes.

## Use the viewer

1. Open the existing read-only viewer for the intended run
2. Load the checkpoint history you need; “Earlier records” loads additional
   entries within the existing reader limit
3. Select **Compare checkpoints**, then choose the left and right exact
   checkpoints. Each choice shows its commit cut and full checkpoint version ID
4. Select **Read comparison**. Both sides are fetched as one comparison request
5. Select **Close comparison** to discard the result and resume the prior live
   refresh preference, or navigate to another observation

The choices come from the loaded history for the displayed exact net, plus the
currently displayed checkpoint. Opening an old net segment does not invent a
history index for that segment; its displayed checkpoint alone is available in
this initial UI slice. The Python/HTTP interface can select any two reachable
checkpoints in the same exact net within the existing bounded predecessor chain.
Choosing the same checkpoint twice is supported, but does not make unknown axes
complete. Unsupported/initial-configured views keep their previous refresh choice.

Opening comparison pauses polling and replay. A new selection immediately clears
the previous result and invalidates pending responses. Repeated read clicks do not
create duplicate in-flight requests. Close, newer dashboard navigation, browser
Back/Forward, page departure, and page restoration discard comparison data. A
failed request clears the entire previous result; there is no cached half-pair
fallback. When the browser saves the page in its back/forward cache, the renderer
and resize observer are retained while outstanding reads and timers are cancelled.
On restoration, polling follows the selected live-refresh preference; a disabled
preference or historical capture does not start polling. Ordinary page departure
still disposes the renderer. Cancelling a previous-net read releases its busy
navigation controls synchronously; saved historical captures remain paused until
Return to live.

Offline Node tests exercise full app startup, the actual lifecycle handlers and
`NetRenderer` with controlled DOM/JointJS/layout dependencies, including repeated
restoration and late responses. This is not actual browser cache-admission,
paint, accessibility, or real JointJS verification.

## What is compared

- **Public definition:** public node/arc fields and declared entry, exit, and
  terminal-rule topology. Node and arc IDs are scoped by the same exact net
- **Exact transition bindings:** registered node, operation-binding and
  executable-binding refs, operation IDs, and public input/output port records
- **Token occurrences:** exact token version refs with place, kind, resource ref,
  and active-at-checkpoint status. Equal resource refs do not merge occurrences
- **Marking:** epoch, total saved token count, and active token count

Rows carry left/right facts, a field and subject, and
`reason_source=current_analysis`. They describe observed values; they do not
invent the reason an agent acted. A token's presence or active-status change is
not evidence of a specific consumption, production, firing, or successful result.

The following axes stay `not_provided`: undisclosed definition details, effective
model/tools/workspace/policy, activity, resource contents, native scores, and
terminal evidence. The comparison does not fetch resource bodies or reconstruct
these missing facts. Public executor declarations are not effective runtime model
or tool evidence. HOST presentation, agent annotations, old change records, and
opaque side extensions are excluded from the comparison DTO.

A missing field is `unavailable`, including when the other side explicitly
contains null. A token present only in one complete checkpoint collection is
`present_left_only` or `present_right_only`, with the other fact
`absent_at_selected_cut`; it is never labeled “deleted.” Unreadable or partial
checkpoint sides fail the whole request instead of becoming empty collections.
“No differences” applies only to the displayed public fields. Overall coverage
remains partial even when all provided fields match.

## Python and HTTP interface

Use an already authorized `RegistryDashboard`; this feature does not construct a
new principal, reader grant, Registry writer, HOST, or execution inventory.

```python
from cpn.frontend.comparison_view import comparison_view

# Each selector is exactly {"net_ref": exact_net_ref,
#                          "checkpoint_ref": exact_checkpoint_ref,
#                          "cut": complete_checkpoint_commit_ordinal}.
comparison = comparison_view(provider, left_selector, right_selector)
# Equivalent: provider.comparison_view(
#     left_selector=left_selector, right_selector=right_selector)
```

`GET /api/v2/comparison-view` and `HEAD` accept exactly two query parameters:
`left_selector` and `right_selector`, each a URL-encoded JSON selector. References
use the exact canonical `entity_type`, `logical_id`, and `version_id` form. Cuts
must be positive safe integers and equal their checkpoint's complete commit.
Duplicate parameters, duplicate JSON keys, extra selector fields, implicit latest
selection, and different exact net refs are rejected. The route is served through
the existing local viewer boundary and sends `Cache-Control: no-store`.

The response is `rpnh/checkpoint_comparison/v1` with fixed
`comparison_mode=descriptive`, `comparability=not_established`, and
`global_atomic_snapshot=false`. `left` and `right` contain narrowed public
checkpoint envelopes; each retains its own exact selector/cut. Their common
`capture` identifies the current observation used for reachability and disclosure,
not a replacement historical cut. Each side is read independently through the
existing exact checkpoint reader. The provider's HOST binding, physical run/DB
identity, task identity, writer epoch, and head are checked again before return.
The local inode check detects source replacement; it is not cross-host
cryptographic authentication or a defense against a malicious HOST.

- `400 invalid_query`: malformed selection or different exact nets
- `403 access_changed`: current binding or physical source changed
- `409 stale_observation`: the shared capture advanced
- `501 unsupported`: the provider does not supply comparisons
- `503 read_failed`: an exact side cannot be read/validated or another read fails

Errors are fixed labels and disclose no partial frames, private exception text,
or source existence detail. Readers remain bounded by `max_checkpoints`; an older
unreachable cut is a read failure, not a claim that it never existed. Registry
changes can require retrying the same explicit pair. No retry selects newer cuts.

The server reconstructs the comparison from closed public side records before
responding; the client checks the same scope and recomputes the rows. Unknown
versions, unexpected nested fields and contradictory coverage are rejected.
All display text and JSON are rendered with `textContent`; data is never HTML,
script, a network image, a capability, or a tool instruction.

## Validation scope and historical limits

Focused checks:

```sh
python -m pytest -q tests/test_checkpoint_comparison.py
node --test frontend/net-viewer/tests/comparison-view.test.mjs \
  frontend/net-viewer/tests/viewer-lifecycle.test.mjs
```

The Python suite includes a socket-free real adopted Registry read, synthetic
source/binding replacement and disclosure failures, and two deterministic
multi-checkpoint integration cases using the existing local-socket fixture.
A sandbox that blocks `AF_UNIX` cannot run those latter two cases: record them as
blocked rather than claiming they passed. To run just the socket-free cases:

```sh
python -m pytest -q tests/test_checkpoint_comparison.py \
  -k 'not actual_registry_pair and not actual_reader_limit'
```

The shared JSON fixture is produced from the Python projection and consumed by
Node to check wire compatibility. Related checkpoint, navigation, token-resource,
viewer-boundary and packaging checks remain relevant. Actual browser interaction,
visual layout and accessibility, and the local-socket multi-checkpoint cases need
verification in an environment that supports them. In the 2026-10-06 cloud check,
the original two multi-checkpoint tests stopped at `AF_UNIX` creation with
`PermissionError` before assertions. The supported cloud browser could not open
the standard local viewer (`ERR_BLOCKED_BY_CLIENT`). Both checks were blocked in that historical window;
passing socket-free reads and synthetic lifecycle tests do not replace them. No real model API call is
required for any comparison test. This slice adds no team execution, cross-source
comparison, score ranking, Registry schema, or mutable viewer endpoint.

This guide retains the original 2026-10-06 validation limits. It does not certify
later source revisions, actual browser cache admission, or the combined product.
See [release validation boundaries](release-validation.md) for separate source windows.
