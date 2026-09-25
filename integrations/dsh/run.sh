#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="${1:?pass the launcher Python interpreter, DSH checkout, and application arguments}"
case "$PYTHON" in /*) ;; *) echo 'Python interpreter must be an absolute path.' >&2; exit 1;; esac
test -x "$PYTHON" || { echo 'Python interpreter is not executable.' >&2; exit 1; }
DSH="$(cd "${2:?pass the pinned DSH source checkout, followed by application arguments}" && pwd)"
shift 2
bash "$ROOT/integrations/dsh/prepare.sh" "$PYTHON" "$DSH"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export RPNH_DSH_CALLER_CWD="$PWD"
export TSX_TSCONFIG_PATH="$DSH/tsconfig.base.json"
cd "$DSH"
exec node --import tsx "$DSH/rpnh-integration/app.ts" "$@" --python "$PYTHON"
