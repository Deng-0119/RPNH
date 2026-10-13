# Controlled local project entry

[中文](project-entry_ZH.md) · [Complete project example](../../examples/project_collaboration/README.md)

This entry combines the original Registry, UserCollaborationHost, protected Branch, plain merge, owner replacement, scoped readers and official Viewer. One trusted OS user operates an owner and two controlled contributor principals through fixed sockets. Client JSON cannot select its actor, principal, Core, session or role. This is controlled local collaboration, not independent-human or remote-account authentication; OS access to these sockets remains trusted.

The example carries a complete reusable project. Its business PN has implementer/reviewer agents; they write and review the domain code. Subordinate execution nets handle harness file/workspace mechanisms in the same Registry, bound to exact business invocations/firings, with separate marking and checkpoints. AgentLoop records do not by themselves establish execution-net evidence.

## Prepare the environment and original HOST

Run from the canonical product checkout with a prepared, clean task-local interpreter bound to that source. Record `sys.executable`, the actual `cpn` import path, exact tested revision and local changes, and separately verify remote `origin/main` before a formal run. Distribution metadata alone cannot prove current source. The example's 0.1.1 compatibility package uses pandas 3.0.3/pyarrow 24.0.0 and harness >=rc2; preserve frozen upstream pins and original packages. Prefer compatible existing libraries over blind downgrades. This guide does not install dependencies.

Stock workspace execution requires Linux x86_64, `/bin/bash`, `/usr/bin/unshare` and the original namespace/Landlock/seccomp isolation interfaces. Read-only platform/metadata checks cannot establish kernel support or provider availability. Original startup must load the existing approved profile and reach READY; the actual runtime must validate isolation.

A tool namespace may map UID/ownership differently from the ordinary WSL host. If original owned-file checks reject system paths because of that mapping, enter the distribution through official `wsl.exe` as the ordinary intended WSL user. Do not change ownership, permissions, UID mappings or the original gate. If AF_UNIX is refused in a different environment, preserve that failure; it is not a universal WSL limitation.

For an approved credential already supplied by environment variable, forward only its **existing variable name** through process-local `WSLENV`. Never place values on command lines or in evidence, and never copy private profiles into the package. In a PowerShell launcher, operator-selected variables below denote the distribution, ordinary user, canonical checkout, task interpreter and existing project root. `$CredentialNames` contains only names already used by the approved profile and present in this process:

```powershell
$PreviousWSLENV = $env:WSLENV
try {
    $ForwardedNames = @($CredentialNames | ForEach-Object { "${_}/u" })
    $env:WSLENV = (@($PreviousWSLENV) + $ForwardedNames |
        Where-Object { $_ }) -join ':'
    wsl.exe --distribution $WslDistribution --user $WslUser `
        --cd $ProductCheckout --exec $TaskPython `
        -m cpn.rpnh.collaboration.project_local serve --root $ProjectRoot
} finally {
    $env:WSLENV = $PreviousWSLENV
}
```

Use this HOST command for an already initialized project; use the same ordinary WSL entry for initialization or the complete driver. Existing approved accounts and credentials suffice. No persistent environment change or new credential is required.

## Initialize, serve and call

In the following shell examples, `PROJECT_ROOT` is a fresh, short path inside the writable workspace; `PROJECT_PACKAGE` and `PROJECT_INPUTS` point to the chosen package and its entry fixture. Select the prepared interpreter as `python`:

```bash
python -m cpn.rpnh.collaboration.project_local --help
python -m cpn.rpnh.collaboration.project_local init \
  --root "$PROJECT_ROOT" --package "$PROJECT_PACKAGE" \
  --inputs "$PROJECT_INPUTS" --contributors 2
python -m cpn.rpnh.collaboration.project_local serve --root "$PROJECT_ROOT"
```

`init` imports through original `plan_subnet_import`/`import_subnet`, creates new identities and copy-origin, and establishes the target's own protected Branch. It safely stops owners without executing the task. `--module module.json` is available instead of `--package`, but a Module alone does not carry the full task. Repeat `--dependency` for locked dependency packages. Inputs map entry names to `{"schema_id": "...", "value": ...}`; stock TEXT values are strings.

For later business execution, add `--execution-profile "$APPROVED_PROFILE"` at initialization, selecting an existing locally approved profile. Its private selector is local only and never part of the portable package. `init` registers original bindings without calling a model. Without that selector, collaboration remains available but `run` refuses.

`serve` checks fixed configuration and transport, then resumes via original `resume_run`. Wait for READY and use its returned routes. It does not overwrite existing sockets or directly open a Core writer. From a second terminal:

```bash
printf '%s\n' '{"op":"status"}' | \
  python -m cpn.rpnh.collaboration.project_local call \
    --socket "$PROJECT_ROOT/owner.sock"
```

Use `--request request.json` instead of stdin when appropriate. Responses are `{ok,result}` or `{ok:false,error,message}`. Each write needs a distinct stable `command_id`; retry the exact operation with identical ID and parameters, and use a new ID for changed content. A rejection can leave original validation/delivery/candidate facts; preserve them. In the following requests, a ref means the full returned source-qualified exact JSON reference, not a name or digest.

## Requests and authority

| Operation | Request fields and meaning |
| --- | --- |
| `status` | No extra fields. Read original project facts and caller actor/session; owner receives `branch_ref` and `head_revision_ref`. |
| `read-plan` (owner) | `revision`, `include_runtime`. Suggest typed/body/export scopes from exact history and optional runtime; grants nothing. |
| `grant-read` (owner) | `subject_session`, `command_id`, `expires_at`, `record_types`, `material_refs`, `export_refs`, `export_destinations`. Explicitly select exact scopes; expiry is timezone-aware within one hour. No default whole-Registry access. |
| `invite`, `join` | Owner invites with `subject_session`, supported `role` (`propose`/`read`), `command_id`; invited route joins with `invitation`, `command_id`. Required fact reads must be granted separately; join grants no body/export access. |
| `module`, `history` | `revision`. Read exact Module/element mapping or at most 128 exact ancestor records; contributors need current scoped authority. History beyond the limit is refused, not called complete. |
| `propose` | `base`, `branch`, `head`, complete `document`, complete `element_ids`, `reason`, `command_id`; optional `previous_proposal`. Start from the exact current head (`base=head`), retain explicit locator→stable-ID mapping, and give new elements new IDs. Human input goes through a real deterministic validation operation → stock TEXT → original delivery/proposal. |
| `review` | `proposal`, `disposition` (`approve`/`request_changes`/`comment`), `reason`, `command_id`. Comment is not approval. |
| `analyze` (owner) | `base`, `local`, `incoming`, `command_id`. Original analyzer returns differences/conflicts and exact analysis ref. |
| `merge` (owner) | `analysis`, `choices`, `command_id`. Cover every conflict with `conflict_id`, `choice` (`base`/`local`/`incoming`/`delete`), `reason`, `delete_element_ids` (empty when not deleting). Creates a real two-parent revision; does not advance Branch. |
| `accept` (owner) | `revision`, `branch`, `head`, `proposals`, `reviews`, `command_id`. Supply all required exact approvals; original commit rechecks eligibility, revocation and stale head. Return original decision/receipt; runtime stays unchanged. |
| `runtime-status` (owner) | No extra fields. Inspect net/checkpoint, exact marking tokens, active firings and pending edits. This is not task scoring. |
| `adopt` (owner) | `receipt`, `branch`, `head`, `runtime`, `marking_mapping`, `retire_token_refs`, `owner_inputs`, `command_id`. Use current original runtime and exact token-version→candidate-place mapping; include empty containers explicitly. Original quiescence/budget/workspace/token gates apply. Only the actual receipt proves adoption. |
| `restore-content` | `restore_from`, `base`, `branch`, `head`, `reason`, `command_id`. Old content must be in current head's exact ancestor chain. Validated human restoration creates a new proposal; still review→accept→adopt. Keep surviving IDs only where current identity/kind proves continuity; other elements receive new IDs. |
| `download` | `revision`, `destination`. Original export requires exact closure/export/destination authority. Returns `archive_base64` and `dependency_archives_base64`. Decode locally and initialize a separate receiver; do not copy source permissions or databases. |
| `viewer`, `viewer-open` (owner) | Read official dashboard JSON or open the actual loopback Viewer. Original permission is rechecked per request; contribution readers retain their own scopes/expiry/budget. Full non-owner Viewer service is not provided here. |
| `run` (owner) | `command_id`. Use trusted startup profile and original owner dispatcher/Orchestrator. Requires existing task/model/budget authorization. Returns REQUESTED and original `execution_ref`; poll as described below. |
| `revoke`, `revoke-read` (owner) | Contribution eligibility: `subject_session`, `command_id`. Reading authority: `grant`, `command_id`. These permissions are distinct. |
| `stop-host` (owner) | `command_id`. Safely stop original owners; active execution refuses stop. Does not erase pending edits or run history. |

## Co-create, restore and reuse

Follow the [complete example](../../examples/project_collaboration/README.md): import full seed → two controlled-principal human validations → first real conflict → explicit A-resolved revision citing B and A's previous proposal → reanalysis/complete choices → true two-parent M → protected acceptance. M contains both A's reviewer instruction and B's connected capacity constraints. A-resolved's proposer remains A; B's proposal, revision reason and M parents carry coordination evidence. Do not invent per-field authorship.

Compare runtime before/after each acceptance. Before the first business firing, independently adopt M, then a new successor with base content, then a new successor with M content. Use actual current marking each time. Restoration preserves history; it does not reset Branch, resurrect spent tokens or reverse files. Download exact original M, not its later single-parent successor. The independent receiver uses its own downloaded input/environment/license closure, new IDs and explicit owner authority. Source and receiver then execute the same original task with stock agents.

## Execution status and recovery

Poll `runtime-status.host_run_request` until RETURNED/FAILED for the exact command and launch. Only `RETURNED.result` carries native stop reason, terminal and model counts; a terminal is not a verifier score. REQUESTED/RUNNING/RETURNED is process-local observation, not persisted scheduling authority. The transport thread parses fixed routes and uses original `OwnerEventLoop.submit_host`; Core and Orchestrator stay on the original owner lane. Human validation can temporarily occupy that dispatcher. Unrestricted socket concurrency is not established.

For ordinary safe stop/resume, preserve original principal/session and wait for READY. Readers need explicit new grants when expired/revoked; reader reopen never becomes writer authority. An interrupted validation with no successful original TEXT cannot be repaired by synthetic delivery or fresh identities.

For a failed business execution, preserve original requests, launch receipt, checkpoint, three Registries, failure facts and counters. Use the original native checkpoint-reentry path to drain/reconcile the original work and persist explicit reopen authorization; it creates a new execution generation while retaining history. This is distinct from rerunning the fresh-directory driver or arbitrary terminal rollback.

After reentry, the local HOST permits **only exact old-command/old-launch replay** when original run authority is `running` and a persisted `reopen_authorization_ref` exists. The original command ID and launch receipt are reused only after this persisted native checkpoint reentry; a new execution generation does not mean a new run command. Original `bind_execution` still verifies the exact association and all original gates. No new retrospective association is created. Existing unsettled firings or absent reopen authorization block replay. A credential failure recorded as `NOT_SUBMITTED` with provider HTTP 0 remains in the original record after recovery; later results need their own receipts.

Protected pending correction/retraction, arbitrary terminal reentry, runtime hot migration and graph/open-region/assembly/selected-change exchange remain outside this bounded route. Do not clear pending state or change owner/security flags to force progress.

## Read budgets and settled artifacts

`init --read-closure-limit 16777216` explicitly selects a 16 MiB cumulative read budget for the example (default 4 MiB); it grants no refs and leaves individual body/response bounds unchanged. The fixture's deduplicated closure is 4,553,881 bytes across 70 exact bodies/four revisions, with 61 material refs and largest body 1,941,458 bytes.

`artifacts-plan` (owner, no extra fields) derives exact task/net/checkpoint/firing bindings and workspace/path/resource/byte metadata from settled workspace revisions. Explicitly include the selected material refs in `grant-read`, then `artifact-read` with `checkpoint`, `workspace`, `path` returns checked `content_base64` and bindings. The path is an exact archive key, never a HOST filesystem selector. No live private working tree is read.

The collector handles only the exact `RegistryReadSessionError` / `limit exceeded` response by asking the original owner for a fresh reader with the **same selected scope**, then retrying that identical checkpoint/workspace/path request **once**. The first receiver export hit retained-snapshot limits on its sixth file; that failure is preserved, and the bounded retry subsequently exported all 20 files. Other errors propagate. This neither increases read caps nor bypasses expiry/revocation; an oversized single body still fails.

Limits: 128 workspace ancestors, 4 MiB total descriptors, 16 MiB selected archives and output bodies, 2,048 files and 4 MiB per file. Reads recheck current checkpoint and original authority; exceeding a bound refuses rather than silently expanding it.

Collect source/receiver outputs, implementation, complete-instruction review, attempts/failures and exact dependencies. Report unchanged original assertions with disclosed path relocation separately from actual original-layout `test.sh`/CTRF/reward. The grader is visible. Genuine official Viewer captures must match the run's revision/net/checkpoint and show agent overview, full PN, contributions, adoption/restoration, resources and actual subordinate execution nets where available.

## Historical Viewer read scope

Viewing a previous net segment or its contributions requires the exact compiler-wire body for that retained runtime net, as well as the selected revision's collaboration facts. A grant containing only the current net's compiler body can legitimately return HTTP 403 for older M/base views. Select the historical body explicitly through the original owner grant; do not relax the read gate.

For fresh journeys, the driver records an inert `read-plan` with `include_runtime=true` immediately after each successful original adoption, while that adopted net is current. Its final runtime grant unions those captured same-project scopes with the current plan. Recording a plan grants no authority; the later explicit owner grant remains authoritative. Export behavior and grants with `include_runtime=false` are unchanged. Operators using the complete driver do not need to invent a Core workflow.

In this retained run, normal read-only inspection recovered only the two exact old compiler-wire bodies from preserved adoption nets, verified their schema/envelope and equality with the actual native Modules, and explicitly selected them in the owner grant. The historical base contribution request then changed from 403 to 200. This was a scope-configuration correction, with no new API, Core writer, permission relaxation or model call. Source historical navigation/capture is now accepted, with all five checked historical/contribution API requests returning HTTP 200. From the current completed view, use Previous Net Segment to inspect the base-content restoration, then original M; inspect the actual adoption references and M parents, then return to the current completed view. This browser result does not substitute for independent caller-code review.

## 2026-10-13 native inspection window

As of 2026-10-13, ordinary WSL native validation from both controlled contributors, true two-parent M, three pre-business adoptions, exact M download and independent receiver import with disjoint IDs have passed. Original-checkpoint recovery retained the same three Registries and original failures/counters. A real no-model regression passed in 27.70s; two preceding fixture failures are preserved. Exact replay support retains the original `bind_execution` gate.

Both original native tasks completed; independent full-instruction review accepted **both** projects. Results below refer to the same original source/receiver executions, not replacement run commands.

| Verified result | Source | Receiver |
| --- | --- | --- |
| Registered requests / model responses | 23 / 22 | 20 / 20 |
| Business firings | 3: one old interrupted firing and two completed roles | 2 completed roles |
| Exact scoped artifact export | 18 files / 47,815 bytes | 20 files / 52,089 bytes |
| Original assertions, path adapted | 3/3 PASS | 3/3 PASS |
| Original `test.sh` | NOT_RUN | NOT_RUN |
| Agent-written implementation, within each exported workspace | `workspace/merger.py` | `solution/merger.py` |

There were **43 registered requests against the authorized maximum of 48**, with 42 responses. Source retains its original single `NOT_SUBMITTED` credential failure, which made zero provider HTTP requests. These are registration/response counts, not a claim about unreported provider retry attempts. Native recovery reused the exact old command ID and launch receipt only after persisted checkpoint-reentry authorization; the execution generation changed, not the run command. Polling merely observed the original executions, and the original HOSTs remained uninterrupted.

For each project, original assertions retain all checks, with only four disclosed `/app` output-literal relocations. The grader was **VISIBLE**; these results are not blind official-container scores. Independent semantic review matched all three original input bytes/hashes and found generic implementations without hardcoded answers. Each output has all four correct rows/20 cells and exactly three field conflicts for user 101 (`name`, `email`, `created_date`). Both Parquet outputs are byte-identical; their conflict JSON differs only by the source's terminal newline. Optional status normalization (`active`/`true`) remains a disclosed instruction ambiguity, not a finding. Receiver `check_out` recomputation was byte-identical to its original deliverables; it did not repair them.

Source and receiver implementation hashes differ. The receiver independently wrote its implementation after importing the exact original M downloaded **before business execution**; no source solution was copied through that package. Each actual Registry business PN has **5 nodes / 6 arcs and two agent roles**. Source has three AgentLoop instances including the old failure; receiver has two. Loop-instance counts are distinct from subordinate execution-net evidence.

Both actual official Viewer capture windows completed with **zero reported gaps**: source captured 101 PNGs and receiver 28 PNGs. Each overview shows two agent nodes/one edge, and each complete business PN shows five nodes/six arcs with unclipped geometry. Actual child execution-net diagrams also fit without clipping after the sidebar layout correction. The receiver execution API returned HTTP 200 with real file-materialization/workspace-finalization `map_ready`/sealed evidence.

Source navigation followed the native **Previous Net Segment** control twice: first to the adopted base-content restoration, then to original two-parent M and its first adoption. Both M parents and all eight supplied historical references were actually visible; returning to the original completed view was verified. All five checked source historical/contribution API requests returned HTTP 200 after the two exact old compiler-wire bodies were explicitly selected in the owner grant. Original Core/read gates remained unchanged.

This bounded journey completed native execution, original path-adapted scoring, full-instruction review and actual Viewer checks in the 2026-10-13 native inspection window. The first browser attempt's socket-path-length failure before HTTP remains preserved. Genuine images and their exact observation references are linked below; the model registration total remains 43.

## Inspection window and actual images

Actual official Viewer images from the 2026-10-13 native inspection window. [Image provenance and per-image records](../../examples/project_collaboration/figures/provenance.json) bind exact source provenance, PNG digests, revision/net/checkpoint and observation position. The 17 PNGs total 4,908,456 bytes and are unchanged; companion JSON omits only localhost URL and local run_dir, retaining original panel text, refs and position. Source capture is H3474/E8 and receiver-04 capture is H2441/E4; a later administrative owner read-grant renewal advanced receiver to H2444, so heads are not claimed equal across windows. Historical cuts, checkpoint selector hints and capture heads remain distinct.

![Source completed agent overview: implementer → reviewer, two agents and one handoff.](../../examples/project_collaboration/figures/source-overview.png)

Source completed agent overview: implementer → reviewer, two agents and one handoff. source-05 · cut 3474 · selector 3401 · [Exact references, panel text and position](../../examples/project_collaboration/figures/source-overview.json).

![Original M contribution observation at historical cut 574, before business execution; this is not the completed state.](../../examples/project_collaboration/figures/source-M-contributions.png)

Original M contribution observation at historical cut 574, before business execution; this is not the completed state. source-05 · cut 574 · selector 574 · [Exact references, panel text and position](../../examples/project_collaboration/figures/source-M-contributions.json).

![Exact first-M adoption receipt version is visible in the historical selected-observation JSON.](../../examples/project_collaboration/figures/source-M-adoption.png)

Exact first-M adoption receipt version is visible in the historical selected-observation JSON. source-05 · cut 574 · selector 574 · [Exact references, panel text and position](../../examples/project_collaboration/figures/source-M-adoption.json).

![Actual attached workspace-finalization execution PN, with its own checkpoint/tokens and map_ready/sealed evidence.](../../examples/project_collaboration/figures/source-execution-finalization.png)

Actual attached workspace-finalization execution PN, with its own checkpoint/tokens and map_ready/sealed evidence. source-05 · cut 3474 · selector 3401 · [Exact references, panel text and position](../../examples/project_collaboration/figures/source-execution-finalization.json).

![Independent receiver complete business PN: five nodes/six arcs and two completed roles.](../../examples/project_collaboration/figures/receiver-petri.png)

Independent receiver complete business PN: five nodes/six arcs and two completed roles. receiver-04 · cut 2441 · selector 2386 · [Exact references, panel text and position](../../examples/project_collaboration/figures/receiver-petri.json).

[Complete image set and run results](../../examples/project_collaboration/README.md).
