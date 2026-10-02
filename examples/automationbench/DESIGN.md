# AutomationBench adapter design

[中文](DESIGN_ZH.md) | [Example](README.md) | [Results](RESULTS.md)

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

The local WSL result uses the native host path and Unix sockets. DSH support in
the adapter is optional and is not evidence for the reported pilot. No cloud
socket fallback, second harness, or production SaaS account is required.

The two compatibility mappings in `rpnh_ab.upstream` address concrete inputs
observed with the pinned upstream. They do not generalize scalar bodies across
other endpoints. Original arguments remain in append-only tool events, while a
separate normalization event identifies the mapped fields and rule.

An attempt is scoreable only after the native host and world owner are both
quiescent and a final world has been frozen. Scorer errors remain null rather
than becoming zero. Remediation runs are stored separately and never overwrite
the strict first-attempt cohort.
