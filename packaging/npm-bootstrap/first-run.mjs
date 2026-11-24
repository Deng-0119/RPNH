#!/usr/bin/env node
import {spawn} from 'node:child_process';
import {accessSync, constants, lstatSync, realpathSync} from 'node:fs';
import {constants as osConstants, homedir} from 'node:os';
import {basename, isAbsolute, join} from 'node:path';
import {fileURLToPath} from 'node:url';

export function defaultRoot(env=process.env, home=homedir()) {
  const xdg=env.XDG_DATA_HOME;
  return join(xdg && isAbsolute(xdg) ? xdg : join(home,'.local','share'),'rpnh');
}

const usage='Usage: rpnh [arguments]\nOn Linux, run rpnh in a terminal to install and begin setup on first use.\nExisting installations accept the original RPNH CLI arguments.\nFor custom installation or recovery, use rpnh-bootstrap or the installation guide.\n';

function inspect(root, {uid, stat, real, access}) {
  let s;
  try {s=stat(root);} catch (e) {if(e.code==='ENOENT') return null; throw e;}
  const baseError=new Error('Existing installation root is not a private managed directory; nothing was changed.');
  if (!s.isDirectory() || s.uid!==uid || (s.mode&0o077) || real(root)!==root) throw baseError;
  const bin=join(root,'bin'), b=stat(bin), exe=join(bin,'rpnh');
  if (!b.isDirectory() || b.uid!==uid || (b.mode&0o077) || real(bin)!==bin) throw baseError;
  const f=stat(exe);
  if (!f.isFile() || f.uid!==uid || (f.mode&0o077) || real(exe)!==exe) throw baseError;
  access(exe,constants.X_OK);
  return exe;
}

function forward(exe,args,{spawnImpl,signals,stderr}) {
  return new Promise(resolve=>{
    let child,done=false;
    const forwardSignal = name => () => {if (!done) child.kill(name);};
    const slots=['SIGINT','SIGTERM'].map(name=>[name,forwardSignal(name)]);
    const finish=code=>{ if(done) return; done=true; for(const [name,handler] of slots) signals.removeListener(name,handler); resolve(code); };
    try {child=spawnImpl(exe,args,{stdio:'inherit',shell:false});}
    catch(e){stderr.write('rpnh: '+e.message+'\n');finish(127);return;}
    for(const [name,handler] of slots) signals.on(name,handler);
    child.once('error',e=>{stderr.write('rpnh: '+e.message+'\n');finish(127);});
    child.once('exit',(code,signal)=>finish(code??(128+(osConstants.signals[signal]||1))));
  });
}

export async function main(args,{platform=process.platform,env=process.env,home=homedir(),
    stdin=process.stdin,stdout=process.stdout,stderr=process.stderr,signals=process,
    uid=process.getuid?.(),spawnImpl=spawn,stat=lstatSync,real=realpathSync,access=accessSync,
    // Release assembler ensures this is the exact legacy bin path of this tarball.
    legacy=fileURLToPath(new URL('./release-bootstrap.mjs',import.meta.url))}={}) {
  if(args.length===1&&['--help','-h'].includes(args[0])) {stdout.write(usage);return 0;}
  if(platform!=='linux'||uid===undefined) {stderr.write('rpnh: this first-run launcher supports Linux/WSL2 only.\n');return 2;}
  const root=defaultRoot(env,home);
  let exe;
  try {exe=inspect(root,{uid,stat,real,access});}
  catch(e){stderr.write('rpnh: cannot use existing install: '+e.message+'; no reinstall was attempted.\n');return 2;}
  const call={spawnImpl,signals,stderr};
  if(exe) return forward(exe,args,call);
  if(args.length||!stdin.isTTY||!stdout.isTTY) {
    stderr.write('rpnh: not installed; run rpnh without arguments in a Linux terminal.\n');return 2;
  }
  return forward(legacy,['--','--first-run'],call);
}
if (process.argv[1] && fileURLToPath(import.meta.url) === realpathSync(process.argv[1])) {
  process.exitCode = await main(process.argv.slice(2));
}
