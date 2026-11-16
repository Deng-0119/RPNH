import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {command, launch} from './launch.mjs';
const dir=mkdtempSync(path.join(tmpdir(),'rpnh entry 空格 '));
const wheel=path.join(dir,'controller fixture.whl');writeFileSync(wheel,'fixture, not a wheel');
const args=['--controller-wheel',wheel,'--controller-python',process.execPath,'--offline','--','--root','目录 with spaces;$(false)','--release','v0.1.0','--python','/persistent/python','--platform','observed.json'];
process.on('exit',()=>rmSync(dir,{recursive:true,force:true}));
test('exact shell-free argv and offline transfer',()=>{
 const [exe,a]=command(args);assert.equal(exe,'uv');assert.equal(a[0],'--offline');assert.ok(a.includes('cpn.rpnh.bootstrap_entry'));assert.ok(a.includes('-m'));assert.ok(a.includes('-I'));assert.ok(!a.some(x=>x.endsWith('bootstrap_entry.py')));
 assert.ok(a.includes('--isolated'));assert.deepEqual(a.slice(-8),args.slice(-8));
 assert.equal(a[a.indexOf('--with')+1],wheel);
});
test('reject absent/remote/floating or duplicate source before spawn',async()=>{
 for(const a of [[],['--controller-wheel','rpnh/latest','--','--help'],['--controller-wheel',wheel,'--controller-wheel',wheel,'--','--help']]) {
 let calls=0;assert.equal(await launch(a,{spawnImpl(){calls++;},stderr:{write(){}}}),2);assert.equal(calls,0);
 }
});
test('installer exit status retained and no shell/stdio replacement',async()=>{
 const child=new EventEmitter();const sig=new EventEmitter();child.kill=()=>{};
 const result=launch(args,{signals:sig,spawnImpl(exe,a,opts){assert.deepEqual(opts,{stdio:'inherit',shell:false});queueMicrotask(()=>child.emit('exit',23,null));return child;}});
 assert.equal(await result,23);assert.equal(sig.listenerCount('SIGINT'),0);
});
test('missing uv is actionable and never launches another installer',async()=>{
 const child=new EventEmitter();child.kill=()=>{};let message='';let calls=0;
 const result=await launch(args,{signals:new EventEmitter(),stderr:{write(s){message+=s;}},spawnImpl(){calls++;queueMicrotask(()=>child.emit('error',Object.assign(new Error(),{code:'ENOENT'})));return child;}});
 assert.equal(result,127);assert.equal(calls,1);assert.match(message,/uv is required/);
});
test('forwards interrupt to controller without touching application root',async()=>{
 const child=new EventEmitter();const sig=new EventEmitter();const killed=[];child.kill=s=>{killed.push(s);queueMicrotask(()=>child.emit('exit',null,s));};
 const promise=launch(args,{signals:sig,spawnImpl(){return child;}});sig.emit('SIGINT');
 assert.equal(await promise,130);assert.deepEqual(killed,['SIGINT']);assert.equal(sig.listenerCount('SIGTERM'),0);
});
test('metadata cannot publish or run an installation hook',async()=>{
 const {readFileSync}=await import('node:fs');const p=JSON.parse(readFileSync(new URL('./package.json',import.meta.url)));
 assert.equal(p.private,true);assert.deepEqual(Object.keys(p.scripts),['test']);assert.equal(p.dependencies,undefined);
});
test('actual executable transport retains Unicode argv and exit code (fixture only)',async()=>{
 const {chmodSync,readFileSync}=await import('node:fs');const {spawnSync}=await import('node:child_process');
 const fixture=path.join(dir,'uv');const output=path.join(dir,'argv.json');
 writeFileSync(fixture,'#!'+process.execPath+'\nrequire("fs").writeFileSync(process.env.RPNH_TEST_ARGS,JSON.stringify(process.argv.slice(2)));process.exit(17);\n');chmodSync(fixture,0o700);
 const r=spawnSync(process.execPath,[new URL('./launch.mjs',import.meta.url).pathname,...args],{env:{...process.env,PATH:dir+path.delimiter+process.env.PATH,RPNH_TEST_ARGS:output},encoding:'utf8'});
 assert.equal(r.status,17,r.stderr);assert.deepEqual(JSON.parse(readFileSync(output)),command(args)[1]);
});
