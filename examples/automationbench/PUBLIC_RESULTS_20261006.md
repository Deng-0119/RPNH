# AutomationBench public results — 2026-10-06

[English](PUBLIC_RESULTS_20261006.md) | [中文](PUBLIC_RESULTS_20261006_ZH.md)

The first18 condition used **freeze04**: 18 planned, 16 started, 14 scored,
5 PASS / 9 FAIL / 4 BLOCKED. The separate **repair** condition executed four
tasks: 1 PASS / 3 business FAIL, with matching native and independent scores.
The old 14 scored tasks were not rerun. These are first18 plus repair4 records,
not an 18-task campaign on repaired source or a combined 6/18 success rate.
The [2026-10-02 pilot](RESULTS.md) is an earlier, separate experiment.

## Conditions and identity

AutomationBench and its unmodified strict native scorer are pinned to
[`4a8e1061254004d9dac807054eed33fad7d1ff14`](https://github.com/zapier/AutomationBench/tree/4a8e1061254004d9dac807054eed33fad7d1ff14).
The seed is `20261006`; selection excluded 38 prior/helper tasks and froze one
task per domain × integration-width cell before calls, without using rubric
difficulty or scores. C3 was a post-stop execution partition of three already
frozen marketing tasks, not a preregistered condition or new selection.

The requested model was `gpt-5.6-terra`, medium, via the native local-process
bridge, without fallback. This is an observed execution condition, not a
product default, preferred provider or account recommendation. Response model
IDs and provider request IDs remain null. SaaS worlds were locally simulated.
Aliases such as `support-0044` are adapter aliases, not official native task
IDs. The official loader has no dedicated native ID column; native IDs remain
null. Upstream names, zero-based row indices, original example IDs and source
links are retained separately in the task table.

## First18 and repair4

| Alias | First18 status / partial | Repair kind | Repair status / partial | Repair submissions / responses |
|---|---|---|---|---|
| finance-0009 | PASS / 1.0 | not rerun | — | — |
| finance-0002 | FAIL / 0.5 | not rerun | — | — |
| finance-0022 | FAIL / 0.6666666666666666 | not rerun | — | — |
| hr-0021 | FAIL / 0.16666666666666666 | not rerun | — | — |
| hr-0001 | FAIL / 0.0 | not rerun | — | — |
| hr-0055 | BLOCKED / null | fresh-world verification | FAIL / 0.8 | 21 / 21 |
| marketing-0025 | FAIL / 0.5 | not rerun | — | — |
| marketing-0096 | FAIL / 0.6 | not rerun | — | — |
| marketing-0090 | PASS / 1.0 | not rerun | — | — |
| operations-0005 | PASS / 1.0 | not rerun | — | — |
| operations-0073 | PASS / 1.0 | not rerun | — | — |
| operations-0052 | FAIL / 0.0 | not rerun | — | — |
| sales-0091 | FAIL / 0.2 | not rerun | — | — |
| sales-0054 | PASS / 1.0 | not rerun | — | — |
| sales-0048 | FAIL / 0.25 | not rerun | — | — |
| support-0063 | BLOCKED / null | fresh-world verification | PASS / 1.0 | 8 / 8 |
| support-0044 | BLOCKED / null | first execution | FAIL / 0.47058823529411764 | 20 / 20 |
| support-0011 | BLOCKED / null | first execution | FAIL / 0.0 | 17 / 17 |


First18 hr-0055 stopped at unsupported-item rejection (exact type/root cause
unknown); support-0063 stopped at provider capacity. Both started with an
unknown submission outcome and no score. support-0044 and support-0011 did not
start. Their first18 scores stay null, not zero; all four stay in the 18-task
denominator. Complete first18 pass rate and mean remain unknown.

The separate `simple-0177` smoke passed with 9 submissions / 9 responses;
it is excluded from first18. Main counts are 164 / 162, or 173 / 171 including
smoke. Two failed submissions have unknown usage; complete usage, billed cost
and wire-request counts remain unknown. A CLI submission is not a wire request.

Repair counts are 20/20, 17/17, 21/21 and 8/8 for
support-0044, support-0011, hr-0055 and support-0063 respectively, totaling 66/66. All four
reached host terminal and host/world-owner quiescence. Native score revision 1
and independent revision 2 agree; evaluation completion does not turn business
FAIL into PASS. There was no extra live smoke, fallback, replay of an old
unknown submission or extra capacity retry.

Retained canonical input=1,985,969 and output=25,260 yield total=2,011,229 across
66 responses, not one context window. Cached input=303,360, cache-write input=0
and reasoning output=10,248 are separate subsets, not additions to that total.
Upstream usage origins were not retained; these fields are not independently
observed provider billing. Cost, wire count and response model IDs remain null.
All 66 Registry and CLI bindings used locally declared context 272000, and
66 successful stderr budget summaries were verified. Actual initial
materialized worlds and clocks were not captured and remain null.

## Failure interpretation

Eight of the old nine business FAILs have supported primary agent-behavior
causes. sales-0048 retains a public-contract gap: the agent computed health
correctly but wrote Description; scoring expected health_status while the
documented update fields omitted HealthStatus. No corrected score is substituted.
The finance-0002 amount-format scorer caveat and marketing-0025 date/route
ambiguity do not remove their separately supported business omissions.

- support-0044: missed discoverable Report Config fields `Exempt_Org_ID` and
  `Batch_Reference`, included internal q07/q08 and omitted report identification
  and correct affected counts despite working reads, tags, append and mail.
  The exact `data-quality-issue` versus `quality_issue` tag naming remains a
  public-contract uncertainty; not every subscore has a uniquely resolved cause.
- support-0011: read only the default Customers sheet, missing Win-back Config,
  Spend History and Outreach Log. All 22 business calls were search/read (POST
  was contact search), with no email, tag, append or Slack writes. One unsupported
  GET 404 does not establish a necessary-write obstruction.
- hr-0055: omitted Ravi's HIPAA notification after missing the required department
  transfer email and relying on the old tracker. Sending mail worked. The
  BambooHR route question does not explain this scored omission.

The repair supplement supports category 1 for these three attempts: discovery,
selection or execution shortcomings, not general model incapability or guaranteed
counterfactual success. No causal harness obstruction of the required operations
was established for these three. Old unsupported-item root cause stays unknown;
nonrecurrence does not establish a fix. Capacity evidence did not prove context
overflow or general service recovery.

## Scope and public files

SpreadsheetBench V1 and SWE-bench Verified remain BLOCKED by existing adapter
and environment gaps. Conditional Office runs were not triggered (NOT_RUN);
GAIA/tau2 remain candidates. Human review was NOT_RUN, pretraining contamination
is UNKNOWN, and strong worker OS isolation is UNPROVEN. This is neither a full
leaderboard nor model-ranking, contamination-free or full mechanism acceptance.

Writer repair tests: 52; independent tests: 50 distinct cases. They overlap and
are not 102 unique tests. Two separate installed native-host offline preparation
windows each passed 7 cases. These are prior evidence, not new publication tests.
See [finite validation](../../docs/guides/release-validation.md) and
[bridge configuration](../../docs/guides/configuration.md).

- [Task identities and upstream links](results/public-tasks-20261006.json)
- [Condition-separated scores](results/public-scores-20261006.json)
- [Counts, conditions and provenance](results/public-provenance-20261006.json)

These are curated projections of retained frozen-cohort, native and
independent evaluation, failure-analysis and final-review records. Private raw
evidence remains outside this repository. No gold answers, input dataset, world,
transcript, private RESULTS copy or ZIP is included. Preserve the
[existing notices](THIRD_PARTY_NOTICES.md) and
[upstream license](https://github.com/zapier/AutomationBench/blob/4a8e1061254004d9dac807054eed33fad7d1ff14/LICENSE);
third-party API structures carry their own rights.
