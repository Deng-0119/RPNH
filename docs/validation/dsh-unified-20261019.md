---
name: rpnh-dsh-unified-validation
description: "Offline validation of the official DSH Web UI, frontend selection and shared MainSession."
metadata:
  document-kind: validation
  audience: operator-and-developer
  language: en
  counterpart: dsh-unified-20261019_ZH.md
  revision: "2026-10-19.2"
  status: offline-validated
---

[English](dsh-unified-20261019.md) | [中文](dsh-unified-20261019_ZH.md)

# Official DSH Web and unified frontend validation — 2026-10-19

`rpnh --frontend dsh` uses the published official Web index, native client plugins,
Web/Connection/Typert transports and module loader. A bound RPNH Remote BFF maps
those UI contracts to the Python MainSession shared with Basic, Codex and OpenCode.
The official browser assets are unchanged; a small RPNH client extension opens the
launcher-bound session.

The pinned target is DSH source `687f282e8625c6b8d11ff58211a90524be2e4317`, npm
assets `0.2.1-rc.2`, Linux/WSL2 and Node `22.22.1`. This version does not bundle a
terminal TUI. [Earlier frontend validation](frontends-20261019.md) retains its
Codex, OpenCode and legacy headless scope.

## Behavior and execution authority

- `rpnh config frontend dsh --dsh-source CHECKOUT` saves a preference independently
  of provider/model configuration. An explicit `--frontend` wins; `auto` retains
  Codex/Basic selection. `DSH_SOURCE` takes precedence over the saved source path.
- Before taking the MainSession owner lease, the launcher checks the source pin,
  Node and dependencies, prepares 31 pinned official packages, generates Loader
  configuration and checks entry points. Missing packages download on first use;
  complete cached packages work offline. Checks detect missing startup JS/CSS or
  client entries; failed downloads retain the previous managed package.
- Native Web uses a loopback listener and official connection authentication. The
  host connects to Python through inherited private pipes. RPNH owns Registry
  writes, provider calls, tools, stopping and recovery. Stock AgentLoop, LLM, tools
  and session persistence are not loaded. There is one durable session authority;
  transient UI events serve presentation.
- History retains Registry timestamps. Unknown historical models stay unknown
  rather than being attributed to the current profile. Native model and effort
  selectors use available RPNH profiles; switching to a model without effort
  support does not revive the previous effort.
- The adapter supports bound-session text, native model selection and the RPNH
  slash menu. It explicitly rejects creation of another native session. File
  uploads, arbitrary workspace editing and native tool execution are outside this
  adapter's scope.
- Cold opening neither submits a prompt nor resumes a paused turn. Stop requests
  a real checkpoint stop. `/rpnh-resume` and `/rpnh-rollback` use shared controls
  through the native command menu. Closing stops only a main task started or
  explicitly resumed by this frontend and waits for its checkpoint; it does not
  acquire ownership of existing running or terminal tasks.
- The legacy `rpnh-dsh` headless entry keeps its session format and recovery contract.

## Official UI validation in this follow-up

| Window | Result and scope |
|---|---|
| Python preferences, CLI, launcher and official assets | 54 passed in 3.80 s, including missing-entry repair, versioned reuse, failed-download retention, inherited pipes and owner lifecycle. |
| Official Web and actual Chromium | 15 passed in 9.96 s, zero skipped: native history, new input, stop, cold reopening, resume command, A/high to B/no-effort selection, Host/Origin, private FDs and EOF. Python RPC is scripted in this window. |
| Production launcher / real Registry / official browser | Five phases passed: fresh commit, cold history and startup stop, cold checkpoint resume and commit, shutdown pause, and cold paused reopening. Local fixture calls were `0→1→1→2→2→2`; five explicit SIGINT exits returned 130. Logical turn/attempt directory and Registry input/answer were checked. Stop covers startup before provider dispatch. |
| Wheel and documentation | Build, isolated target installation, official modules/JSON, installed help and frontend preferences passed; 16 documents, 624 local targets and 46 Bash fences passed. This is not the full installed test suite. |

Additional real rollback validation passed: native `/rpnh-rollback` retains the
logical turn and attempt directory, records Registry state `interrupted`, and adds
no provider call. The next input was also admitted, checkpoint-stopped and rolled
back. The composer remains available and both committed answers remain. The
five-stage run ended with turn three genuinely paused; after the extra controls,
the retained session has two committed and two interrupted turns with no active
turn. Child Registries and checkpoints remain. All official-stage attempts total
three local fixture submissions: two in the passing run and one fresh commit
before attempt two's selector failure. Remote provider/model calls remain zero.

The first prototype loaded modules and a renderer only; it was not a UI pass.
Later prototypes rendered the official sidebar, chat and composer. Formal browser
checks exposed missing step events: cold history rendered, while a newly completed
answer had no body. The presentation projection now supplies native step/start
and step/end. Native command lookup provider registration was also corrected to
the official contract; initial failures are retained. Regressions cover silent
false success from New Session and dropped public session errors: creation is
explicitly rejected, and native errors are emitted on opening and changes, with
the checkpoint notice preserved.

Real rollback also exposed a presentation mismatch: native DSH `interrupted`
means an orphaned turn after an unexpected exit, whereas this RPNH state comes
from owner rollback. Only the displayed ending now maps to `aborted/user`; Registry
state is preserved and paused turns remain open. The before-fix failure, original
false crash text and passing regression are retained. The old scripted fixture
incorrectly removed a turn; it now retains the interrupted lineage. No rebaseline
was added for that fixture error.

Independent review closed two asset/history issues: a missing startup JS file no
longer qualifies for cache reuse, and unknown historical model evidence is not
assigned to the current profile. Before-fix failures and passing checks are kept.
The nine-file implementation type window had zero owned diagnostics and 109
upstream diagnostics; only rollback regressions and the display mapping followed.
The complete upstream TypeScript graph is not a passing window. No real remote provider/model calls were made. This report does not certify
all DSH capabilities, live providers, every platform or later revisions.

## Published component stage and inherited fixes

The earlier published stage used DSH chat/composer/theme components and native
AgentLoop/Session without the complete official Web shell. Its five-stage real
Registry acceptance and final 11 native tests remain historical evidence; they
do not substitute for the official UI window above. Python 159, startup-stop/
launcher 27 and final ownership 10 windows retain their scopes and overlap; do
not add their counts.

Inherited fixes register frontend ownership only after `RESUME_STARTED`, schedule
owner reconciliation by a monotonic deadline even under continuous reads, and
handle startup stop before native run identity is committed using only the
unique live main-task handle already owned by this frontend. Stop still waits
for real checkpoint evidence. BrokenPipe on normal child closure enters existing
transport error handling. Registry authority is unchanged; process exit does not
stand in for a checkpoint or completion. The first local fixture's noncanonical
JSON was correctly rejected; only fixture serialization was corrected.

The first two official end-to-end failures were driver selectors: empty and
existing sessions use different placeholders, and the sidebar, breadcrumb and
message repeat the prompt text. Original evidence is retained; only the local
driver was corrected. Neither attempt counts as a complete pass.

See the [DSH guide](../guides/dsh.md) for setup and controls.
