# Portable independent replay

The review report and all recorded evidence are preserved unchanged. Their original cloud paths describe past execution, not dependencies required for replay.

The five runnable scripts here retain the reviewed test logic. Only three fixture-location expressions were replaced by an explicit RPNH_SOURCE checkout lookup. See packaging-adaptations.patch/json; exact original script bytes are retained as non-executing text in original_scripts/. No product source was changed for packaging.

From this package, use an existing supported Python:

```sh
PYTHONDONTWRITEBYTECODE=1 python tools/run_independent_review.py --source /path/to/applied/RPNH --mode pytest
PYTHONDONTWRITEBYTECODE=1 python tools/run_independent_review.py --source /path/to/applied/RPNH --mode lowlevel
PYTHONDONTWRITEBYTECODE=1 python tools/run_independent_review.py --source /path/to/applied/RPNH --mode indices
PYTHONDONTWRITEBYTECODE=1 python tools/run_independent_review.py --source /path/to/applied/RPNH --mode empty-consume
PYTHONDONTWRITEBYTECODE=1 python tools/run_independent_review.py --source /path/to/applied/RPNH --mode read-edit
```

The wrapper binds the checkout and its test fixtures, rejects sockets/URL retrieval, and does not install software or invoke a provider. The reviewed source hashes and patch identity must be checked before replay. Packaging reruns are repeat evidence, not new unique case counts.

For the independent pre-existing pure regression window, use that full checkout with tools/offline_pytest.py:

```sh
PYTHONDONTWRITEBYTECODE=1 python /path/to/package/tools/offline_pytest.py -p no:cacheprovider -q tests/test_marking_modularization.py tests/test_registered_operation_recovery.py tests/test_structural_evidence.py -k 'not test_inspector_routes_real_execution_and_preserves_exact_request and not test_scheduler_cannot_turn_disabled_operation_into_enabled_one'
```

The deselected cases require native OwnerEventLoop sockets and are not cloud D0 PASS results.
