#!/bin/bash
set -uo pipefail
review="$(cd "$(dirname "$0")/.." && pwd)"
source="${RPNH_SOURCE:-$(cd "$review/.." && pwd)/source}"
python="${RPNH_PYTHON:?Set RPNH_PYTHON to an existing Python with the authorized dependencies. This runner never installs software.}"
results="${REVIEW_RESULTS_DIR:-$review/reproduced-results}"
mkdir -p "$results"
results="$(cd "$results" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
"$python" "$review/tools/source_manifest.py" "$source" > "$results/source-before.json"
"$python" - "$results/source-before.json" <<'PY_VERIFY'
import json, sys
manifest = json.load(open(sys.argv[1]))
expected = 'a3af1d85ffbc8acac72e719fed316ed3c848229fce92026c62e571cb77a913a2'
if manifest['file_count'] != 994 or manifest['manifest_sha256'] != expected:
    raise SystemExit('Refusing to test a different source identity')
PY_VERIFY
identity_status=$?
if [ "$identity_status" != 0 ]; then exit "$identity_status"; fi
cd "$source" || exit 2
PYTHONPATH="$source:$source/tests:$review/tests${PYTHONPATH:+:$PYTHONPATH}" \
    "$python" "$review/tools/offline_pytest.py" "$review/tests" -vv \
    --junitxml="$results/review.xml" > "$results/review.log" 2>&1
result=$?
"$python" "$review/tools/source_manifest.py" "$source" > "$results/source-after.json"
cmp -s "$results/source-before.json" "$results/source-after.json"
stable=$?
printf '{"pytest_exit":%s,"source_unchanged":%s}\n' "$result" "$([ "$stable" = 0 ] && echo true || echo false)" > "$results/result.json"
cat "$results/review.log"
if [ "$stable" != 0 ]; then exit 2; fi
exit "$result"
