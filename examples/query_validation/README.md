# SQL query validation and provenance

## Official Viewer complete PetriNet

[![SQL initial-declaration PetriNet: 3 transitions, 8 places and 11 arcs](figures/initial-petri.png)](figures/initial-petri.png)

This original official Viewer capture shows the complete declared Petri net: **11 nodes (3 transitions and 8 places), 11 arcs**. It is an `initial_configured` declaration with no runtime marking or checkpoint, not a completed-run PetriNet capture. [Capture provenance](figures/petri-provenance.json) identifies the retained input and Viewer revision. The completed-run Detailed flow remains in [the run record](#completed-local-task-and-recorded-run).

[Open the original panorama](figures/initial-petri.png) · [Inspect nodes and arcs in the read-only Viewer](#static-pn-viewer)

This database example records actual SQLite reads, exact result comparison and a
final SQL resource through the existing Registry/PetriNet. The bundled [sol.sql](sol.sql)
was independently derived from the pinned task instruction, original query and
hash-verified database, without reading evaluator tests, golden SQL or oracle outputs.
No benchmark solver, model or provider was invoked. Original verifier execution
and browser capture are separate evaluator/Viewer responsibilities.

The motivating external task is Terminal-Bench 2's
[`query-optimize`](https://github.com/harbor-framework/terminal-bench-2/tree/2fd12b88aafdd04a52c298e3940bcb189f9766d6/query-optimize).
It asks for `/app/sol.sql` against an OEWN SQLite database. Source identity,
database hash, license limits and evaluator separation are in [sources.json](sources.json).
The repository commit is not a claim that a named benchmark dataset release was resolved.

## What the three operations demonstrate

1. `inspect_inputs` receives three admitted resource versions: a database descriptor,
   original SQL and candidate SQL. It verifies the exact database bytes, SQL digests
   and candidate format, and publishes the schema plus a query request.
2. `execute_queries` executes both queries against separate digest-checked read-only
   snapshots. It publishes two distinct result resources containing the exact column
   names, ordered rows, query plans, input references and observed execution time.
3. `compare_results` joins those settled resources. The terminal report points to
   their exact resource versions and states whether columns and ordered rows match.
   A sibling `final_sql` resource retains the candidate text, digest, comparison status
   and candidate-result reference; both outputs derive from the two delivered results.
   Duplicate rows are preserved. A mismatch is a completed observation, not a
   successful task solution; the CLI returns nonzero for mismatch.

HOST tools use the registered tool gateway. `PublishResource` uses the existing
Petri output binding and `derived_from` for exactly the delivered input references.
`ExecutionServices`, `OwnerEventLoop` and `Orchestrator` own admission, dispatch and
settlement. There is no alternate scheduling, authority, resource type or Registry.
The final report is read through `run_terminal_evidence/v1`, not inferred from a
local `equivalent` flag or a completed Python function.

The database resource is an explicit external-file descriptor (path plus SHA-256),
not a claim that the database itself is stored in Registry. Each operation copies
one opened input stream into a private snapshot and verifies its digest before
SQLite opens it. Replacing or modifying the external file is rejected. The database
must remain available for execution; exported query results and read-only Registry
readback do not require it afterward. Treat database files as trusted task inputs,
not as untrusted arbitrary SQLite binaries.

## Run with the real task inputs

From a checkout where RPNH is already available:

```bash
python -m examples.query_validation.run \
  --database /app/oewn.sqlite \
  --original-sql /app/my-sql-query.sql \
  --candidate-sql examples/query_validation/sol.sql \
  --run-dir /tmp/query-observation-run \
  --output-dir /tmp/query-observation-export \
  --query-timeout 1800
```

Both directories must be fresh. This command enforces the pinned database and
original-SQL hashes. It does not obtain the database, install anything, author or
rewrite `sol.sql`, call a model, fetch evaluator files, or execute a grader.
Candidate SQL is an owner-supplied input, not the upstream reference solution.

SQLite uses `mode=ro`, `query_only`, and a restrictive authorizer. ATTACH/DETACH,
PRAGMAs, writes, temporary DDL and extension loading are rejected. Results exceeding
10,000 rows are rejected rather than silently truncated. The `--query-timeout`
option (default 120 seconds, up to 1,800) is a SQLite progress-handler bound; it is
not a subprocess wall-clock or memory sandbox. These are observation limits,
not changes to the official task verifier.

Output: `evidence.json`, `report.json`, `projection.json`, `dashboard.json`, and
`sol.sql` exported from the settled final-SQL resource. A mismatch still exports the
candidate with `equivalent_to_original=false`; export does not certify a solution.
The evidence contains SQL and actual rows; keep it private until data rights and
intended disclosure have been checked. `--readback` reconstructs these exports
from an existing Registry without rerunning any query. Database snapshots are
removed when each query finishes. Generated Registries/exports are not source files.

## Viewer

```bash
python -m examples.query_validation.view --run-dir /tmp/query-observation-run
```

This uses the official `RegistryDashboard` and read-only Viewer. Select Detailed
flow or PetriNet: this tool-only example intentionally has no model-agent overview.
Select `execute_queries.run` to inspect its admitted request and two output
resources, then `compare_results.run` to inspect their join, report and `final_sql` output.
Its dashboard projection is an observation, never the source of execution authority.

The bundled [initial declaration frame](figures/declaration-frame.json) is generated
by the official compiler and projection functions: three transitions, eight places
and eleven edges. It contains no runtime results and is not a browser screenshot.

Local native completion (2026-10-12) reached terminal `complete` with three
PUBLISHED firings and five products. Exact columns and all 500 ordered rows matched.
The exported SQL matched the frozen candidate bytes. Five native selfchecks passed.
The original query took 163.652 seconds and the candidate 0.320 seconds in one
observation; these timings do not establish an original-verifier performance pass.
The owner loop closed normally. Full local logs retain SQLite connection
`ResourceWarning` messages; core connection management was not changed.

## Independent candidate and validation boundary

The original query repeatedly scans unindexed senses in correlated subqueries.
The candidate preserves its aggregate, joins, filters and order, materializes the
first 500 words, then groups and ranks their senses once. Counts are retained:
the pinned database contains duplicate word/synset pairs. Final sorting preserves
the original ordered output. Candidate formation and EXPLAIN traces are retained
in local task evidence; the source SQL is 1,186 bytes, SHA256
`729471f70d1a64f7b1f2a28cd099d9a345e7f97355e840f55d12f403c781c0b3`.

Five bounded selfchecks cover format, read-only access, exact columns/order/duplicates,
Registry provenance, final SQL export and source-independent readback. Run with the
default native AF_UNIX transport:

```bash
python -m examples.query_validation.selfcheck -v
```

The cloud preparation used base `b8d5942e768b2d59862b8e3eb7efa41a274f6307` and
could only test an explicit pipe transport. Local completion uses actual product base
`e9fc93daf82914940ba48a86e1245b0f3f9f7667` plus example-local changes. These are
different source conditions; the native run does not use the pipe flag. Selfchecks
use synthetic data only; the real task uses the hash-verified 50,606,080-byte OEWN DB.
Database, query-result rows and Registry files stay local. OEWN is CC-BY 4.0 with
Princeton/OEWN attribution; redistribution of the pinned SQLite conversion remains
unresolved. Authorized local use does not approve redistribution.

The author never runs or reads the original verifier or its expected outputs.
The parent evaluator receives the frozen candidate separately. Consult its report
for original PASS/FAIL and environment adaptations, and its Viewer evidence for
actual browser capture. A source commit is not a resolved dataset release.

The original verifier additionally checks database integrity, exact outputs and
columns, single-query format/size, and the solution median against the evaluator's
golden query over five iterations (threshold 1.05×). This adapter does not reproduce
or weaken those checks and makes no performance pass claim. Single-query elapsed
times here are observations with snapshot overhead excluded, not a fair performance
benchmark. `original_verifier` stays `not_run` and `benchmark_pass` stays null.

Example completion and a mature-harness comparison are separate. A baseline run
is not a prerequisite for illustrating provenance, and matching SQL outputs alone
does not establish a harness speed or reliability advantage.

## Completed local task and recorded run

[![Completed SQL run in Detailed flow: 8 displayed nodes; original PetriNet has 11 nodes and 11 arcs](figures/recorded-run.png)](figures/recorded-run.png)

This separate runtime capture uses **Detailed flow**, which folds three handoff places. It is not the complete PetriNet panorama above.

The independently frozen `sol.sql` passed all **six unchanged upstream tests** in one 176.76-second evaluator run. A narrow invocation plugin mapped five absolute paths; existing Python/SQLite/pytest replaced the upstream installation wrapper. The original `test.sh`, Docker limits, CTRF and stock harness were not run. This is an adapted verifier result, not a full official dataset benchmark. The evaluator reference stayed separate from the author, and the candidate/database/verifier bytes remained unchanged. Immutable native resources still say `original_verifier: not_run`, reflecting their pre-evaluation publication; the later evaluator receipt is separate.

This is the actual native completed run: Registry head **421**, three settled operations, zero unsettled records, and two final tokens. The image preserves the original Viewer bytes, including a technical checkpoint footer and observation time; it contains no local filesystem path or user identity map. It is not the initial declaration frame.

Using the Viewer command above, select **PetriNet → compare_results.final_sql → Executions → View registered resource** for the final SQL token. Select `execute_queries.run` for the recorded execution, then move the history slider to the first checkpoint and select an input place to inspect its original token registration. Browser checks exercised all three operations, the report and final-SQL registrations, and a historical input registration. The Registry event head, count, writer epoch and main database metadata were unchanged; normal WAL/SHM auxiliary state is reported separately. The stock Viewer shows references and registration metadata, not database rows or payload bodies. No task model/provider calls were made.

<a id="static-pn-viewer"></a>

### Read-only declaration PetriNet Viewer

From the source checkout root with a compatible RPNH environment, open the bundled declaration without a Registry, database, model call or task execution:

```bash
python - <<'PYTHON'
import json
from pathlib import Path
from cpn.frontend.server import serve_projection
frame = json.loads(Path("examples/query_validation/figures/declaration-frame.json").read_text(encoding="utf-8"))
class Snapshot:
    def __call__(self):
        return frame["net"]
    def dashboard(self, cursor=None):
        if cursor is not None:
            raise ValueError("Static declaration has no execution history")
        return frame
serve_projection(Snapshot(), host="127.0.0.1", port=0, open_browser=False)
PYTHON
```

Open the printed localhost URL and choose **PetriNet → Fit to view**. Zoom, pan and select nodes or arcs for full labels and native details. The declaration has no runtime marking or execution history. Stop the viewer with Ctrl+C. Use the separate [runtime Viewer command](#viewer) for an existing completed run.
