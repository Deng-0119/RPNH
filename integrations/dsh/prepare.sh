#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="${1:?pass the launcher Python interpreter and pinned DSH source checkout}"
case "$PYTHON" in /*) ;; *) echo 'Python interpreter must be an absolute path.' >&2; exit 1;; esac
test -x "$PYTHON" || { echo 'Python interpreter is not executable.' >&2; exit 1; }
DSH="$(cd "${2:?pass the pinned DSH source checkout}" && pwd)"
test "$(git -C "$DSH" rev-parse HEAD)" = ddefc45fbc7f8e46dd73185e68295696d1297887 || {
  echo 'Wrong DSH revision. Use the revision in integrations/dsh/UPSTREAM.json.' >&2; exit 1;
}
node -e 'const [major,minor]=process.versions.node.split(".").map(Number); if (!(major===22&&minor>=19||major>=24)) { console.error("DSH needs Node ^22.19.0 or >=24; Node 24 is tested."); process.exit(1) }'
test -x "$DSH/node_modules/.bin/tsx" || { echo 'Install pinned DSH dependencies with pnpm 11.7.0 --frozen-lockfile first.' >&2; exit 1; }
"$PYTHON" "$ROOT/integrations/dsh/patch_upstream.py" "$DSH" >&2
mkdir -p "$DSH/rpnh-integration"
cp "$ROOT/integrations/dsh/src/"*.ts "$DSH/rpnh-integration/"
echo 'Managed DSH application prepared; no task or provider call was started.' >&2
