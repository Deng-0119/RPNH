#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DSH="$(cd "${1:?pass the fixed DSH checkout}" && pwd)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export RPNH_DSH_SOURCE="$ROOT"
export RPNH_DSH_RESULTS="$ROOT/integrations/dsh/results"
mkdir -p "$RPNH_DSH_RESULTS"
test "$(git -C "$DSH" rev-parse HEAD)" = ddefc45fbc7f8e46dd73185e68295696d1297887
python "$ROOT/integrations/dsh/patch_upstream.py" "$DSH"
mkdir -p "$DSH/rpnh-integration"
cp "$ROOT/integrations/dsh/src/"*.ts "$DSH/rpnh-integration/"
cat > "$DSH/vitest.rpnh.config.ts" <<'TS'
import { defineConfig } from 'vitest/config'
import tsconfigPaths from 'vite-tsconfig-paths'
import { standardDecoratorPlugin, vitestExecArgv } from './vitest.shared.ts'
export default defineConfig({ plugins:[standardDecoratorPlugin(),tsconfigPaths({projects:['./tsconfig.base.json']})],
 test:{ include:['rpnh-integration/*.spec.ts'], testTimeout:300000, hookTimeout:300000,
   execArgv:vitestExecArgv, fileParallelism:false, bail:1, reporters:['default','junit'],
   outputFile:{junit:process.env.RPNH_DSH_RESULTS + '/dsh-integration.xml'} } })
TS
cd "$DSH"
pnpm exec vitest run --config vitest.rpnh.config.ts 2>&1 | tee "$RPNH_DSH_RESULTS/dsh-integration.log"
