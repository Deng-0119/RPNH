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

The adapter regression set reached 10 passing tests after the Trello fix. The
current public example adds result-integrity, documentation, and isolated
inspection checks. Deterministic tests do not call a real provider.

Passing component tests does not recreate the retained model result. Conversely,
the real pilot demonstrates this fixed condition only; it is not a proof for all
models, hosts, tasks, or future upstream versions.
