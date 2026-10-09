# RPNH DSH codec offline candidate

Baseline: Deng-0119/RPNH `8dd360e4848912a998dbd83220c3f0ce0a1caa86`, checked again at freeze on 2026-10-08 UTC.

This is a reviewable codec / registered-provider DTO / detached-history candidate, not completed native compatibility or admission of DSH 0.2.1-alpha.1. Production remains pinned to `ddefc45fbc7f8e46dd73185e68295696d1297887` (0.1.6-alpha.2). `5badb15009ae1756c3afe0ae0cef1faafc290ccc` is only the source-audited candidate codec target.

Formal reasoning, tool calls/results, identity, content, raw arguments and error semantics are retained. Detached history supports V3→V3, V3→V4 and V4→V4; V4→V3 is unsupported. Read projection grants no execution/resume authority. Production open/stat/read still use the old runtime. The explicit local owner `--history` export remains unchanged. Registry/PN authority, nativeRegistry, launcher/server, prepare/verify, factory patch and bridge REVISION are not replaced.

## Evidence and limits

Final pure results: 55 Node cases, 39 Python codec/parity cases, and 9 launcher/source cases passed. Two package-build/installation cases were not run. The original independent window had 54 Node cases; a header-field constraint case was subsequently added. Final independent reruns use the same final case IDs and are not additional cases. Separate review scripts contain 1,133 and 36 assertions, including loops; these are not case counts.

The original owner regression remains 34 cases: 12 passed and 22 blocked by AF_UNIX EPERM. A later approved run was interrupted with exit130 and is not a pass. All original evidence remains. No native DSH, TypeScript typecheck, installation, real provider/model, login, Actions or push was performed during freeze.

## Contents and use

- `candidate.patch`, `changed-files.txt`, `file-manifest.json`, `patch-apply.json`: 13-path minimal product patch, exact baseline/candidate hashes and fresh apply verification.
- `source/`: 1,346 verified validation-snapshot files, not a complete checkout or dependency environment. `baseline/` contains affected pre-existing files and protected unchanged files; the original 1,339-file verification manifest remains as evidence.
- `upstream/{pin,latest}/`: 14 official source files plus both exact-revision official LICENSE files. `evidence/` retains historical results; `evidence/freeze/` binds final tests to before/after source hashes and test IDs.
- `IMPLEMENTATION_STATUS.md` and `LOCAL_VALIDATION.md`: implemented scope and remaining gates. `README_ZH.md` and `DESIGN_REVIEW_ZH.md` give the Chinese summary and design. The sibling `rpnh-dsh-codec-independent-review/` preserves original and final independent review material.

Run `python scripts/verify_bundle.py` here for read-only archive integrity verification. With `--checkout /path/to/RPNH`, it additionally verifies every affected baseline file and protected file and requires added paths to be absent. In the authoritative worktree, use `git apply --check /path/to/candidate.patch` before any authorized application; never force over changed main files.

For pure reruns only, invoke `python scripts/run_pure_gates.py --output /path/outside/bundle/results` using an already prepared Python interpreter and Node24 on PATH. It checks hashes and installed prerequisites and runs the three pure groups plus detached factory-source checks. It never installs or runs owner/native/model gates. See LOCAL_VALIDATION.md for owner/socket, package build/install, old-native regression, exact typechecking and new-native Session/lifecycle certification. New production admission remains a separate reviewed change.

RPNH MIT is in source/LICENSE, with original notices in source/THIRD_PARTY_NOTICES.md. The identical official DeepSeek MIT licenses are preserved at upstream/pin/LICENSE and upstream/latest/LICENSE, with immutable sources and Git blob hashes in evidence. No installed third-party dependencies or build outputs are included.
