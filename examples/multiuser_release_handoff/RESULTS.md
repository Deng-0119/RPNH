# Original episode: results and source boundaries

[中文](RESULTS_ZH.md) · [Example](README.md) · [Curated machine-readable checks](result-summary.json)

The final read-only audit at 2026-10-16T20:18:43Z reports PASS: 98/98 required checks, 106 PASS / 2 optional UNKNOWN out of 108. It made no model calls, business rerun or Registry writes. This public summary projects selected fields from that audit; it does not redistribute its raw logs, private world, Registry databases or provider transcripts.

## Exact business outcomes

- Canonical local Drive note `sql-export-guard_release_note.txt` reached version 2. The stable mutable slot is `logical_slot:7196b636140b5ac497f6c8aeec492412`; current immutable body is `resource:fd86078165c4518fb9b40fb38fc40375` / `resource_version:bf4afb66fee356dda64db05f85fbcca8`. Slot stability does not mean immutable body identity remains unchanged.
- Original SQL commit `961cbb286424a90892f8111f79ded17d0837c138` is retained on `coord/sql-export-guard` in the local `platform_admin/mini-seed-repo`, at `sql/patches/sql_export_guard.sql`. The exact SQL is preserved in result-summary.json. Its SELECT returns `{}` / `pending` for SQL NULL payload/status while retaining non-NULL values; it does not modify stored rows. JSON null, empty strings and SQL NULL are distinct.
- QA checklist commit `4d68b6105fc7cbc3e2632ba75b5d590fbae0ae20` follows the original SQL commit, at `qa/checklists/sql-export-guard_qa_checklist.md`. The original three-line checklist template is preserved separately from actual validation findings.
- Exactly one incident was created: `incident:c576433b180840b1bd1819f7e1c1c9ad:v1`; [original incident fields](incident-summary.json) remain unchanged. Actual validation evidence was attached separately.
- QA and support reread v2 and released exact claims before their final confirmations. Verification/accepted-confirmation times were QA `2026-10-16T18:41:10.188093Z` / `18:43:04.500471Z`, support `2026-10-16T20:02:58.502536Z` / `20:04:30.029975Z`. Both bind the current note version and original SQL commit. Final handoff publication was observed; later recipient consumption was not.

The v2 note includes rollout prerequisites, bounded SQL NULL/non-NULL and downstream checks, isolation of affected exports, preservation of failed artifacts/logs/source `IS NULL` evidence, prior-query restoration and controlled regeneration. Reverting the query does not repair previously exported/imported artifacts. Written guidance and static checks do not establish successful database tests or executed rollback.

## Historical runtime, recovery and accounting

The tested run02 runtime was `7e27f27c3474ceb5a1ce4b82e4df624c28529f30`; it includes retained runtime dependencies beyond public base `798b4260e33cfb7f6476eee17e9f48de9ba998f2`. The historical result is not relabeled as a clean run of that public base or of a later distribution. Accepted explicit checkpoint-reentry and HOST JSON repairs are public; aggregate call-cap/context/head-query support and the retained Petri input adjustment must also be present for the fresh example source. Follow the matching [run guide](RUNNING.md).

Original failures remain part of the episode: stale queued-message authority, orphan Source roots during explicit reentry, and nested JSON serialization before support backend dispatch. Checkpoint restoration used permanent native state and reconciled effects in the same world. Operations/support reran only unfinished work; developer and QA stayed terminal. A fresh run cannot substitute for this recovery evidence.

| Window | Adapter submissions | Returned responses |
|---|---:|---:|
| Original baseline | 67 | 64 |
| Run01 | 72 | 71 |
| Run02 | 13 | 13 |
| Continuation total | 85 | 84 |
| Cumulative | 152 | 148 |

Run02 comprises operations 6/6 and support 7/7; designer/developer/QA add zero. Available CLI completed-turn records total 149: input 4,821,361; cached input 338,304; output 65,509; reasoning 5,417. One stopped operations CLI completed without an adapter return, so 149 CLI records and 148 returns differ. Three baseline attempts lack CLI usage, which remains UNKNOWN. Cached input is a subset of input; reasoning is not added again to output. Billing and provider-internal HTTP attempts remain UNKNOWN. Counts exclude software-worker sessions.

The business window exited 0 with peak 1,288,105,984 bytes and OOM0. The read-only audit and capture windows also exited 0 with OOM0. These are measured windows, not a general minimum-memory requirement.

## Evidence limits

Original judge: NOT_RUN; official score: NOT_COMPARABLE_ADAPTED. No matched control, superiority result, production deployment, external identity federation or whole-product certification. Developer/QA optional requested historical-cut completeness remains UNKNOWN; retained terminal captures are real. The public source curation adds no new live execution claim.

## Public-entry verification

[Release validation](release-validation.json) records a separate source/package check: the guide's pinned original-task retrieval and configuration work, the no-provider native preflight passes, 80 selected runtime/example checks pass without skips, and three driver control tests pass after reproducing and fixing two closeout defects. The isolated installed core also passes schema/cap-transport/CLI checks. These checks do not add model-provider calls to the historical accounting or rescore the original business episode.
