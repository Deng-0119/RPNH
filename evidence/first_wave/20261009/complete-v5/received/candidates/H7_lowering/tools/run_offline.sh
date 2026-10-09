#!/bin/bash
set -u
package="$(cd "$(dirname "$0")/.." && pwd)"
name="$1"; shift
python="${RPNH_PYTHON:?Use an existing authorized interpreter}"
cd "$package/source"
"$python" "$package/tools/source_manifest.py" . > "$package/evidence/$name-before.json"
PYTHONDONTWRITEBYTECODE=1 "$python" "$package/tools/offline_recorded.py" "$package/source" "$package/evidence/$name-results.json" "$@" -p no:cacheprovider --junitxml="$package/evidence/$name.xml" > "$package/evidence/$name.log" 2>&1
status=$?
"$python" "$package/tools/source_manifest.py" . > "$package/evidence/$name-after.json"
printf '%s\n' "$status" > "$package/evidence/$name.exit"
exit "$status"
