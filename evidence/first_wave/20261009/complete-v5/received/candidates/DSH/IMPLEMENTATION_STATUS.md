# DSH codec candidate: implementation and gate status

2026-10-08 UTC. Baseline: Deng-0119/RPNH main `8dd360e4848912a998dbd83220c3f0ce0a1caa86`.

## Result

A reviewable local candidate now provides exact-revision V3/V4 tool-message codecs in TypeScript and Python, a pure registered-provider DTO converter, and a detached history projection. Existing adapter consumers remain bound to the original production `REVISION`. This is not support admission for DSH 0.2.1-alpha.1.

- V3 tool wrappers become V4 first-class tool messages without changing message ID, call ID, content, error semantics or raw tool arguments.
- Formal assistant reasoning/text, tool calls and results are retained. V3 plugin context sources use the official released producer mapping, preserving metadata.
- Full history supports V3→V3, V3→V4 and V4→V4. V4→V3 full history is unsupported. A single canonical tool result may encode either layout.
- A denied/blocked turn may end in an undispatched tool call. A provider request may not contain an unpaired result/call. The two boundaries are deliberately distinct.
- Historical absent `isError` stays absent. Managed execution still requires a boolean. If an outer observation records an error, an absent/false message flag cannot disguise it as success.
- Only the existing formal message/event slots enter Session projection. Literal user text is not redacted. No generic privacy filter or new business state is added; the explicit local `--history` export is unchanged.
- Existing Python tool construction/validation and TypeScript provider/offline reads use the codec with the old exact revision. The assembler uses the `createAssistantMessage` helper present in both official revisions.
- A revision guard runs before allocating the agent scope. Detached read conversion grants no write/resume permission. Registry records, nativeRegistry identity, registrations, pin, protocol and no-replay ownership remain unchanged.

## Important implementation limit

The new-version history path is the dependency-free `projectHistory(..., candidateRevision)` candidate. Production `RegistryProjectionPersistence.open/stat/read`, launcher and bridge are still the old supported runtime. They do not constitute a completed V4 cold-reopen integration. Candidate Session construction, open/stat header consistency, exact-revision typecheck and genuine new DSH lifecycle remain local certification gates. No helper or script bypasses production `prepare.sh`/`verify.sh` pin checks.

The known private-policy regression test remains relevant but its owner stage was environment-blocked here. Synthetic sentinel checks prove only that this codec does not copy arbitrary outer envelope fields; they do not certify all Registry/privacy behavior.

## Executed checks

| Gate | Result | Scope |
|---|---|---|
| Current RPNH source identity | 24 DSH files verified; all required cpn/tests/integrations dependencies copied only on exact Git-blob match | 1,339 current-main files locally verified; remaining repository docs/evidence/examples not a complete checkout |
| Official source identity | 14 files verified | old/new message, assembler, Session types/implementation, factory; latest tool/source migration |
| Factory source transform | old/new pass | pure detached strings, six unique anchors, idempotence, partial-patch rejection |
| Node codec/history/provider DTO | 55 pass | dependency-free Node built-in test runner, Node 24.19.0; no official DSH runtime |
| Python codec + TS/Python fixture parity | 39 pass | existing prepared Python environment, no installation |
| Existing launcher/source tests | 9 pass, 2 intentionally not run | package-build and installed-console tests excluded because installation/package certification is out of scope |
| Existing Python backend/long-path/reader suite | 12 pass; 22 environment-blocked | every failed test reached AF_UNIX PermissionError; original log and JUnit retained |
| Separately requested sandbox-approved retry | interrupted, exit 130 | started before parent's no-more-escalation direction; stopped afterward; partial log retained, no pass claim |
| Typecheck old/new DSH | not run | no prepared TypeScript/DSH dependency tree used |
| Native old/new DSH | not run | no install, real provider, model, login, Actions or remote write |

The earlier independent-review window ran 54 Node and 39 Python cases. The later candidate added the header-field constraint case, bringing the Node suite to 55. The final freeze and the new independent-review window both ran 55 Node and 39 Python cases. These are repeated executions of the same final test IDs and are not counted twice. The independent probes have 1,133 and 36 assertions, including looped assertions; these are not test cases and are not added to the case totals. Original evidence and the earlier 54-case review are retained unchanged.

Final freeze evidence is under `evidence/freeze/`, including per-case identities, source hashes before and after tests, official-license readback, and exact baseline/upstream hash verification. All 1,346 candidate source files remained byte-identical during the freeze. The patch contains 13 changed paths and applies cleanly to fresh copies of their exact baseline files. Package transport verification is separate from native or integration certification.

## Files and conflicts

See `changed-files.txt`, `file-manifest.json`, `candidate.patch`, and `patch-apply.json`.

No Codex history package file, NativeRegistry API, MainSession history reader, provider configuration, Registry core, PN runtime, or prior frozen package was edited. Shared-file conflict risk is confined to `pyproject.toml`, where the DSH JSON fixture package-data glob is added. DSH distribution test coverage includes that new fixture. There is no version or dependency change.
