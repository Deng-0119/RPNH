#!/bin/bash
set -euo pipefail
package="$(cd "$(dirname "$0")/.." && pwd)"
name="${1:-portable-final}"
bash "$package/tools/run_offline.sh" "$name" -v --tb=short -p no:cacheprovider \
  tests/test_parent_child_core.py tests/test_static_lease_reads.py tests/test_static_lease_exact_selection.py \
  tests/test_static_lease_interactions.py tests/test_event_store_functional_split.py \
  tests/test_invocation_functional_boundary.py tests/test_operation_functional_split.py \
  tests/test_registered_operation_recovery.py \
  --deselect=tests/test_static_lease_reads.py::test_new_process_cold_registry_reconstructs_active_and_settled_refs \
  --deselect=tests/test_invocation_functional_boundary.py::test_functional_modules_import_without_preloading_invocations \
  --deselect=tests/test_operation_functional_split.py::test_operation_facade_wrappers_delegate_and_preserve_contracts \
  --basetemp="$package/evidence/$name-tmp"
