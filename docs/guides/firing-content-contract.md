---
name: rpnh-firing-content-contract
description: "Validate firing outputs and read exact owner edit receipts."
metadata:
  document-kind: reference
  audience: developer-and-agent
  language: en
  counterpart: firing-content-contract_ZH.md
  revision: "2026-10-26.1"
  status: current
  basis: "Registry firing content contracts and owner receipts"
---

# Firing content contracts and owner receipts

English | [中文](firing-content-contract_ZH.md)

A firing content contract binds immutable source references and output schemas to
an operation in the original Registry. A registered HOST-only checker validates
the complete proposed product bundle. The `submit_candidate` AgentLoop tool
preflights and seals accepted JSON products; rejected candidates remain recorded
and can be corrected. Success and recovery recheck the registered closure. A
checker does not create a second scheduler or grant authority by returning a
value. Business and execution PN layers retain their existing responsibilities.

Each `sources` entry requires an exact `resource_ref`, for example
`{"input": {"resource_ref": {"resource_id": "...", "resource_version_id": "..."}}}`.
The HOST need not compute or supply a source digest. The source is still read
from the original Registry at the acceptance cut, with its exact identity,
version, registered byte envelope and same-task ownership checked. The trusted
checker receives those original bytes in `snapshot["sources"][role]["payload"]`
and remains responsible for domain content validation. Caller-supplied payloads
cannot replace the Registry bytes. Malformed source declarations raise
`FiringContentContractError`.

Existing explicit `sha256` assertions are still verified; an explicit
`decoded_sha256` still requires a JSON string and verifies its decoded UTF-8
content. Omitting either field does not create it in the source snapshot.
These optional source assertions do not change native product/check records,
schema and port checks, checker binding, atomic sealing, Success or recovery.
HOST integrations use `preflight_candidate(core, execution, products=...,
selected_outcome_id=...)` to validate and `seal_candidate(core, execution,
products=..., selected_outcome_id=..., idempotency_key=...)` to publish the
accepted bundle; pass the returned authority to `owner.succeed` as before.

See `cpn/rpnh/firing_content_contract.py` for the exact checker ABI and declaration
marker, and `cpn/components/agent_loop/content_coverage.py` for source coverage.
Coverage establishes recorded reads and submitted logical-request content, not
provider-wire transmission or a model's semantic use.

`owner.read_edit_receipt(command_id)` reads original command, result and adoption
facts at a fixed Registry cut. It does not resubmit, adopt or repair an edit.
An explicit identical `edit` retry retains the existing current-public ability to
complete candidate-only preparation. Protected collaboration still requires its
existing authorized action; a receipt is not permission to edit.

`resume_pending_edit` is a narrow recovery API, not general failure continuation.
It verifies the exact retained command and checkpoint before acquiring a writer.
Only a `RESUMED` result carries an owner; a receipt alone does not prove a resumed
session. It executes no operation suffix. Ordinary continuation remains recovery
from a valid committed checkpoint, not from the failed operation. Collaboration-
managed pending commands are not covered by this narrow entry and must retain
their authorized owner workflow. See the [checkpoint guide](checkpoint-recovery.md).
