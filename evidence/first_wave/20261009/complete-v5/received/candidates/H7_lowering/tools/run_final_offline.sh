#!/bin/bash
set -euo pipefail
package="$(cd "$(dirname "$0")/.." && pwd)"
name="${1:-final}"
bash "$package/tools/run_offline.sh" "$name-author" -v --tb=short tests/test_bound_child_lowering.py tests/test_bound_child_declarations.py
bash "$package/tools/run_offline.sh" "$name-compatibility" -v --tb=short tests/test_typed_author_interoperation.py tests/test_compiler_json_contract.py tests/test_worker_schema_compat.py tests/test_control_ir_compiler.py
bash "$package/tools/run_offline.sh" "$name-core" -v --tb=short tests/test_parent_child_core.py tests/test_acceptance_history.py tests/test_static_lease_reads.py tests/test_static_lease_interactions.py tests/test_static_lease_exact_selection.py --deselect=tests/test_static_lease_reads.py::test_new_process_cold_registry_reconstructs_active_and_settled_refs
