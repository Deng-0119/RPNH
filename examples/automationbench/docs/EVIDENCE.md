# Evidence and retention

[中文](EVIDENCE_ZH.md) | [Design](../DESIGN.md)

`tool_events.jsonl` records the model-facing arguments, admitted dispatch, and
exact upstream response. `normalization_events.jsonl` records any compatibility
mapping without replacing those original arguments. RPNH Registry projections
record managed actions and actual model-call counts. A frozen final world and
the upstream score file establish the business outcome.

These layers answer different questions. A successful tool return does not by
itself prove that the model consumed it, and a terminal prose report does not
prove the business state. A score is valid only after host and world-owner
quiescence are established.

Attempts, interruptions, errors, and score revisions are append-only. A
remediation path never replaces a first attempt. Reprojection and rescoring may
read frozen evidence but must not replay business work.

The public example contains sanitized plans and summaries only. Raw Registry
databases, request/response transcripts, private execution profiles, and local
paths remain outside the repository. The checked-in summary supports auditing
the reported arithmetic, not full replay of every provider interaction.
