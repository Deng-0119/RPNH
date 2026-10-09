#!/bin/bash
set -uo pipefail
package="$(cd "$(dirname "$0")/.." && pwd)"
python="${RPNH_PYTHON:-$package/../rpnh-recovery-20261003/source/.venv/bin/python}"
if [ ! -x "$python" ]; then
  echo "Set RPNH_PYTHON to an existing authorized Python with the required dependencies; this script never installs them." >&2
  exit 2
fi
export PYTHONDONTWRITEBYTECODE=1
name="$1"; shift
"$python" "$package/tools/source_manifest.py" "$package/source" > "$package/evidence/$name-source-before.json"
cd "$package/source"
"$python" "$package/tools/offline_pytest.py" "$@" --junitxml="$package/evidence/$name.xml" > "$package/evidence/$name.log" 2>&1
result=$?
"$python" "$package/tools/source_manifest.py" "$package/source" > "$package/evidence/$name-source-after.json"
cmp -s "$package/evidence/$name-source-before.json" "$package/evidence/$name-source-after.json"
stable=$?
printf '{"pytest_exit":%s,"source_unchanged":%s}\n' "$result" "$([ "$stable" = 0 ] && echo true || echo false)" > "$package/evidence/$name-result.json"
cat "$package/evidence/$name.log"
exit "$result"
