---
name: rpnh-firing-content-contract
description: "Validate firing outputs and read exact owner edit receipts."
metadata:
  document-kind: reference
  audience: developer-and-agent
  language: en
  counterpart: firing-content-contract_ZH.md
  revision: "2026-10-25.1"
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
