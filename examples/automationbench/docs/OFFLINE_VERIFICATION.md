# Offline and local integration verification

[中文](OFFLINE_VERIFICATION_ZH.md) | [Example](../README.md)

The original preparation package was tested in a cloud environment where Unix
sockets were unavailable. That limitation does not apply to the retained WSL
run and is not a requirement of this example.

Local verification covered:

- the real pinned AutomationBench task loader, three API tools, simulated world,
  and strict rubric;
- the installed RPNH `TaskControl`, native worker, managed plugin entry point,
  Unix socket broker, Registry evidence, output bundle, and terminal lifecycle;
- exact tool-response and final-world agreement against direct upstream calls;
- JSON-string `null` normalization and the endpoint-specific Trello add-label
  scalar mapping; and
- a real external model-provider task followed by the retained 18-task pilot.

The current extension regression set also covers frozen 18-task cohort
resolution, stale-score rejection, normalization-event export inventory,
durable status/stop control, native and DSH host-result projection, DSH's
explicit unmetered mode above its ordinary 48-attempt default, and the real
seven-case installed-host producer. Deterministic tests and `accept-host` do not
call a real provider.

The installed-host acceptance is stronger than a startup probe: it runs a
synthetic business mutation through the actual managed-tool boundary, scores
the resulting upstream world, crosses the former 64 KiB DSH result boundary,
and verifies that stop leaves no scoreable final world. Its manifest is bound
to the selected host/code/tool condition and cannot be transferred to another
condition. Validation reparses the referenced attempt, lifecycle, tool-event,
score, and host/Registry records instead of trusting case labels alone.

Passing component tests does not recreate the retained model result. Conversely,
the real pilot demonstrates this fixed condition only; it is not a proof for all
models, hosts, tasks, or future upstream versions.
