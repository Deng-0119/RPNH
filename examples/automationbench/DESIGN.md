# AutomationBench adapter design

[中文](DESIGN_ZH.md) | [Example](README.md) | [Historical condition](EXPERIMENT.md) | [Extensions](EXTENSIONS.md)

The adapter deliberately separates four authorities:

1. AutomationBench supplies the public task, local business world, API tool
   semantics, and strict programmatic rubric.
2. RPNH supplies the actor, execution profile, managed-tool admission,
   lifecycle, Registry, and PetriNet evidence.
3. A serialized broker owns each mutable world and records every dispatch
   before and after the upstream call.
4. Offline scoring consumes a frozen final world; it never treats the actor's
   prose report as task success.

Each task receives an independent world, worker, Registry, and first-attempt
directory. Hidden assertions and the full initial world remain host-side. The
actor sees the public prompt and tool responses only.

The retained WSL result used the native host path and Unix sockets. Current
extension code also provides a pinned DSH execution host. Both hosts use the
same frozen task/world/scorer authority, but each has its own condition-bound
acceptance evidence. DSH support is not evidence for the reported pilot. No
cloud socket fallback, second harness, or production SaaS account is required.

The benchmark CLI is the sole control authority. Shell, Basic, Codex and
OpenCode terminals may invoke it and inspect its durable status, but no
presentation surface receives a second task prompt or owns an alternate agent
loop. This keeps “execution host” separate from “control surface.”

The two compatibility mappings in `rpnh_ab.upstream` address concrete inputs
observed with the pinned upstream. They do not generalize scalar bodies across
other endpoints. Original arguments remain in append-only tool events, while a
separate normalization event identifies the mapped fields and rule.

An attempt is scoreable only after the selected host and world owner are both
quiescent and a final world has been frozen. Attempt identity, task-contract
identity, scoring-input hash and final-world hash must also match the retained
score. Scorer errors remain null rather than becoming zero. Remediation runs
are stored separately and never overwrite the strict first-attempt cohort.
