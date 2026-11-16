#!/usr/bin/env node
// Transport only: the wheel owns its Python entry and managed installer.
import { spawn } from 'node:child_process';
import { realpathSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { constants } from 'node:os';

export function command(argv) {
  const split = argv.indexOf('--');
  if (split < 0) throw new Error('Use --controller-wheel PATH --controller-python PATH [--offline] -- <bootstrap arguments>');
  const config = argv.slice(0, split);
  let wheel, python, offline = false;
  for (let i = 0; i < config.length; i++) {
    if (config[i] === '--controller-wheel' && wheel === undefined && config[i + 1]) wheel = config[++i];
    else if (config[i] === '--controller-python' && python === undefined && config[i + 1]) python = config[++i];
    else if (config[i] === '--offline' && !offline) offline = true;
    else throw new Error('Unknown or repeated controller option');
  }
  if (!wheel || !wheel.endsWith('.whl')) throw new Error('An explicit local controller .whl is required; no floating version is selected');
  if (!python) throw new Error('An explicit installed controller Python is required; runtime downloads are disabled');
  python = realpathSync(python);
  if (!statSync(python).isFile()) throw new Error('Controller Python must be a regular executable');
  wheel = realpathSync(wheel);
  if (!statSync(wheel).isFile()) throw new Error('Controller wheel must be a regular file');
  const args = argv.slice(split + 1);
  if (!args.length) throw new Error('Bootstrap arguments are required');
  return ['uv', [...(offline ? ['--offline'] : []), 'run', '--python', python, '--no-python-downloads', '--no-project', '--no-config', '--isolated', '--with', wheel,
    'python', '-I', '-m', 'cpn.rpnh.bootstrap_entry', ...args]];
}

export async function launch(argv, {spawnImpl = spawn, signals = process, stderr = process.stderr} = {}) {
  let exe, args;
  try { [exe, args] = command(argv); }
  catch (e) { stderr.write(`rpnh bootstrap: ${e.message}\n`); return 2; }
  return new Promise(resolve => {
    let child, finished = false;
    const handlers = new Map();
    const done = code => {
      if (finished) return;
      finished = true;
      for (const [name, handler] of handlers) signals.removeListener(name, handler);
      resolve(code);
    };
    try { child = spawnImpl(exe, args, {stdio:'inherit', shell:false}); }
    catch (e) { stderr.write(`rpnh bootstrap: ${e.message}\n`); done(127); return; }
    for (const signal of ['SIGINT', 'SIGTERM']) {
      const handler = () => { if (!finished) child.kill(signal); };
      handlers.set(signal, handler); signals.on(signal, handler);
    }
    child.once('error', e => {
      stderr.write(`rpnh bootstrap: ${e.code === 'ENOENT' ? 'uv is required but not available on PATH; no application was installed by this launcher' : e.message}\n`);
      done(127);
    });
    child.once('exit', (code, signal) => done(code ?? (128 + (constants.signals[signal] || 1))));
  });
}
if (process.argv[1] && fileURLToPath(import.meta.url) === realpathSync(process.argv[1])) {
  process.exitCode = await launch(process.argv.slice(2));
}
