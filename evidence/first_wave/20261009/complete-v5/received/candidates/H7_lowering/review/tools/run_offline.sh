#!/bin/bash
set -euo pipefail
package="$(cd "$(dirname "$0")/.." && pwd)"
python="${RPNH_PYTHON:-/workspace/scratch/18c810e6dd59/rpnh-recovery-20261003/source/.venv/bin/python}"
name="$1"; shift
cd "$package/reviewed-source"
PYTHONHASHSEED=0 "$python" "$package/tools/offline_pytest.py" --junitxml="$package/evidence/$name.xml" "$@" > "$package/evidence/$name.log" 2>&1
