# Evidence and retention

[中文](EVIDENCE_ZH.md) | [Design](../DESIGN.md)

`tool_events.jsonl` records the model-facing arguments, admitted dispatch, and
exact upstream response. `normalization_events.jsonl` records any compatibility
mapping without replacing those original arguments. RPNH Registry projections
record managed actions and actual model-call counts. A frozen final world and
the upstream score file establish the business outcome.

New attempts additionally retain `world.initial.materialized.json` immediately
after upstream setup, before the host starts or the first tool dispatch. It is
an exclusive-create snapshot of the actual materialized world, including default
values and the actual `meta.current_time`; subsequent broker checkpoints only
replace `world.latest.json`. `initial_world_provenance.json` binds the snapshot's
exact UTF-8 file bytes (including the trailing newline), SHA-256 and byte size
to the attempt record, task-contract hashes, pinned and observed upstream commit,
and adapter Python-source digest. It also records the initial clock and service
scope. The attempt record retains source and task identity even if setup fails;
any snapshot already written is retained if a later preparation or host step
fails. Missing snapshots are never inferred from the final world.

This is passive, private evidence capture. It does not set or freeze the upstream
clock, modify the world, change prompts or tools, expose hidden task data to the
actor, or replace the original pre-materialization `scoring_input.initial_state`.
The scorer and score-eligibility rules are unchanged. It cannot recover a missing
historical initial world or clock, and it does not justify merging or rewriting
historical cohorts.

Summary rows include an optional `initial_world` provenance diagnostic with only
hashes, source identities and the initial timestamp. They do not include world
contents, service lists, prompts or hidden rubric data. A captured record is
checked against the retained snapshot, attempt and task-contract files; a
mismatch is reported independently of the score. Older attempts without this
evidence remain readable and scoreable, with initial-world status `unknown`.

These layers answer different questions. A successful tool return does not by
itself prove that the model consumed it, and a terminal prose report does not
prove the business state. A score is valid only after host and world-owner
quiescence are established.

Attempts, interruptions, errors, and score revisions are append-only. A score
is eligible only when its task ID and task-contract digest match the frozen
plan, the private task-contract file still matches the digest fixed at attempt
creation, its scoring-input and final-world hashes still match, and the retained
lifecycle proves host/world-owner quiescence. A revision first validates the
preceding score against those same inputs. A remediation path never replaces a
first attempt. Reprojection and rescoring may read frozen evidence but must not
replay business work.

The return ZIP includes `normalization_events.jsonl` when present and records
the SHA-256 and size of every exported, post-redaction byte sequence. This
inventory verifies the copy that was actually returned; it is not a hash of an
unredacted private original.

The allowlisted private return ZIP also includes the initial snapshot and its
provenance when present, using the same exact-known-credential redaction as other
private evidence. Summary/provenance hashes refer to the retained private source
files (`hash_scope: retained_private_evidence` in summaries); if export redaction
changes a copy, use `RETURN_MANIFEST.json` to verify that returned copy's bytes.
The return ZIP contains private task/world data and is not a public summary.

The historical public result contains sanitized plans and summaries only. New
extension runs keep their plan, condition-bound seven-case host acceptance,
attempt evidence and export in a caller-owned work directory. Raw Registry
databases, request/response transcripts, private execution profiles, and local
paths remain outside the repository. The checked-in summary supports auditing
the reported arithmetic, not full replay of every provider interaction.
