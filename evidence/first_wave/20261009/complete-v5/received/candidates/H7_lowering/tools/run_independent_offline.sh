#!/bin/bash
set -euo pipefail
package="$(cd "$(dirname "$0")/.." && pwd)"
python="${RPNH_PYTHON:?Use an existing authorized interpreter}"
name="${REVIEW_RUN_NAME:-independent-reproduced}"
cd "$package/source"
"$python" "$package/tools/source_manifest.py" . > "$package/evidence/$name-before.json"
set +e
PYTHONHASHSEED=0 "$python" "$package/review/tools/offline_pytest.py" -v --tb=short -p no:cacheprovider \
  "$package/review/tests/test_independent_bound_lowering.py" \
  "$package/review/tests/test_independent_bound_revision.py" \
  "$package/review/tests/test_independent_bound_catalog.py" \
  tests/test_local_takeover_ordinary_revision.py \
  --junitxml="$package/evidence/$name.xml" "$@" > "$package/evidence/$name.log" 2>&1
status=$?
set -e
"$python" "$package/tools/source_manifest.py" . > "$package/evidence/$name-after.json"
printf '%s\n' "$status" > "$package/evidence/$name.exit"
exit "$status"
