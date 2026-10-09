# Codex 0.161 minimal protocol candidate

Implemented and narrowly validated in pure JSON/Registry tests. Native client
certification remains pending. The exact 0.155 default, normal CLI, installed
clients, Registry identities, authority and release status are unchanged.

The explicit `candidate-0.161.0` profile uses the existing compatibility manifest
as its only capability table. Binary version, immutable server profile,
initialize version and thread.cliVersion agree exactly; unknown versions/selectors
are rejected. Object item anchors resolve one new cut and an exact committed
turn's existing two safe slots into the original exclusive native anchor. All
continuations remain v1 strings. Unknown history timestamps and new resume defaults
are truthful null/[] values. No new store, reader, grant or persistent cursor table.

The base lock records the 715468d subset + Reader + effort codec + final owner
history df0c3090 + verified d92 product overlay. 8dd is a later documentation HEAD,
not a claim of full-HEAD certification. The six-file patch contains only this
candidate increment: three product files, one new test module and two language
versions of its documentation. The included 1,044-file source is the exact composed
snapshot; prior overlays are prerequisites, not duplicated patch changes.

Final tests: 122 history/profile cases (69 new + 53 inherited), eight selected
existing default-boundary cases, and sixteen pure fake runner/log cases passed.
Independent 34-case review overlaps these and is reported separately. The initial
pre-d92 run is retained as historical evidence only. No native client/socket,
Rust compilation, model/API, installation, login, push or Actions run occurred.

Run `python verify_package.py` for hash and clean six-file application checks.
See `README_ZH.md`, `reports/VERIFICATION.json`, `design/COMPLETION_ZH.md` and
`native-gate/NATIVE_GATE_ZH.md` for exact provenance, scope and pending gates.
The native handoff prepares synthetic roots once, reuses the same canonical paths
across exact-version lanes sequentially, marks missing binaries BLOCKED, and stops
on unexpected RPC/errors. Log completeness never substitutes for visual/native
certification. The additional object WebSocket probe is separate from stock TUI.
