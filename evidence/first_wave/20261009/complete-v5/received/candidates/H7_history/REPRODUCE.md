# Offline reproduction

Use an existing authorized Python with the dependencies already installed. This package does not install software or authorize native workers, model/provider/API calls, network access, subprocess tests, PTY operations, Actions or remote changes.

## Verify files and overlay

From this package directory:

```
/path/to/existing/python tools/verify_identity.py
cp -a inputs/H7-core-source /tmp/unique-h7-history-reconstruction
git -C /tmp/unique-h7-history-reconstruction apply --check /absolute/package/acceptance-history.patch
git -C /tmp/unique-h7-history-reconstruction apply /absolute/package/acceptance-history.patch
/path/to/existing/python tools/source_manifest.py /tmp/unique-h7-history-reconstruction > /tmp/h7-history-reconstruction.json
cmp /tmp/h7-history-reconstruction.json evidence/FINAL_SOURCE_MANIFEST.json
```

Choose a previously nonexistent disposable destination. The input source already includes the frozen H7 core; do not apply its older overlay again. The input remains a curated snapshot with the missing files/history declared by that core, not a complete repository clone.

## Selected final tests

```
RPNH_PYTHON=/path/to/existing/python bash tools/run_final_offline.sh local-final
```

The launcher runs the history tests, original author core tests, selected adjacent regressions and the original independent core boundary cases. The explicitly subprocess-based adjacent tests remain deselected. The sentinel is installed before product/pytest imports. Plugin autodiscovery is disabled. Source manifests before and after, real test exits and XML/logs are recorded. Do not modify the candidate or runner while tests are active; do not disable guards to obtain a pass.

The independent history review is separately reproducible with its included launcher and tests; see `review/REVIEW.md`. Historical logs contain their original absolute paths as evidence, not a promise those paths exist in another environment.

## Interpretation

Read `VALIDATION.md` for the exact final-source unique test inventory, preserved old failures, exclusions, unreachable lifecycle and unsupported native work. Test-only native evidence allocation is explicitly injected and never counts as real native evidence. Read-only SQLite may create SHM/WAL coordination files; the guarantee is no canonical database/event/object mutation, not zero directory effects.
