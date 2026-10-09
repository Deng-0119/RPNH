# Local gates after candidate review

Do not install dependencies, log in, call a provider/model, start Actions, push, change version gates or rewrite an old Registry to run these checks.

## Apply check

Use the authoritative Deng-0119/RPNH worktree. Confirm its current main and compare every affected baseline hash in `file-manifest.json`; unrelated main changes can be evaluated separately. Run `git apply --check candidate.patch` first. This candidate was generated from main `8dd360e4848912a998dbd83220c3f0ce0a1caa86`; it has no dependency on the separate Codex history patch.

## Pure checks (both layouts, no DSH runtime)

From the repository root, using existing Node 24 and the already prepared project Python environment:

    node --test integrations/dsh/src/message-codec.test.ts
    python -m pytest -q tests/test_dsh_message_codec.py
    python -m pytest -q tests/test_dsh_distribution.py -k 'not distribution_contains_runtime_and_public_source_assets and not console_help_works_from_installed_distribution'

Expected as of this candidate: 55 Node, 39 Python codec/parity, 9 launcher/source tests. These are not upstream native smoke or a TypeScript typecheck.

The portable bundle also provides `scripts/run_pure_gates.py`. Invoke it with your existing prepared Python interpreter and an explicit output directory outside the frozen bundle. It verifies source hashes, checks existing Node/Python prerequisites, and runs only the three pure groups above plus detached factory-source checks. It does not install dependencies or run owner/native/model gates. The packaged `source/` is a verified 1,346-file validation snapshot, not a complete repository checkout; use the authoritative worktree for package builds and native integration.

## Existing-owner Python gate (local environment must allow its Unix socket)

    python -m pytest -q tests/test_dsh_backend.py tests/test_dsh_long_path.py tests/test_net_view_dsh_adapter_integration.py

Keep original tests and owner loop. Do not replace transport or remove failing cases to obtain a green result. Cloud result was 12 pass/22 socket-permission failures. Recheck private provider-policy non-persistence, exact grants/correlation, managed-tool error outcomes, budget limits, denial, interruption, explicit resume and no replay.

Package build/installed-distribution tests are a separate authorized local gate. The fixture is included by the added package-data entry; no build/install was run here.

## Old supported DSH gate

Use an already prepared official checkout at `ddefc45fbc7f8e46dd73185e68295696d1297887` with its frozen dependencies. Keep the existing `integrations/dsh/verify.sh` exact gate. Run the project's existing native integration/lifecycle tests and relevant typecheck under its documented prepared workflow. This establishes old support has not regressed. Do not interpret pure Node tests as passing this stage.

## New candidate gate, separate from production admission

Use an already prepared official source/dependency environment at `5badb15009ae1756c3afe0ae0cef1faafc290ccc` only for reviewed isolated candidate tests:

1. Typecheck the changed consumer signatures and source declarations against that exact upstream.
2. Feed `projectHistory(V3 fixture, candidateRevision)` into genuine V4 Session creation/restoration, then verify IDs/content/source, open/stat/read header consistency and flush equality without Registry writes or dispatch.
3. Test V4→V4 and V4→V3 rejection; old active/old completed history must not gain new-revision write or resume permission.
4. A zero-input genuine Cordis/Session/Agent lifecycle test has zero model/tool dispatch. A fake-model deterministic offline execution test is a different gate and must be named separately.

This candidate contains no production-REVISION replacement or pin-bypass runner. New configured/offline execution support, matching bridge/backend registration identity, and the final exact support manifest require a subsequent explicitly reviewed admission change after these checks. Old active checkpoints remain tied to the original supported runtime.
