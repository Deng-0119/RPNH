import {test} from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {mkdtempSync,mkdirSync,writeFileSync,chmodSync,symlinkSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {defaultRoot, main} from './first-run.mjs';

function setup(t, tty=true, exit=[0,null]){
  const h=mkdtempSync(join(tmpdir(),'rpnh-first-run-'));
  t.after(()=>rmSync(h,{recursive:true,force:true}));
  const args=[];
  let output='';
  const emit=new EventEmitter();
  let killed=null;
  const io=(tty)=>({isTTY:tty,write(s){output+=s;}});
  return {
    h,args,emit,killed:()=>killed, output:()=>output, opts:{
      home:h,env:{},stdin:io(tty),stdout:io(tty),stderr:io(false),legacy:'/test/legacy.mjs',
      signals:emit,spawnImpl(exe,a,opts){
        args.push([exe,a,opts]);const child=new EventEmitter();
        child.kill=s=>{killed=s;};
        process.nextTick(()=>child.emit('exit',...exit));return child;
      }
    }
  };
}
function install(f){
  const root=defaultRoot({},f.h);mkdirSync(root,{recursive:true,mode:0o700});
  const bin=join(root,'bin');mkdirSync(bin,{mode:0o700});
  const target=join(bin,'rpnh');writeFileSync(target,'# fixture only\n');chmodSync(target,0o700);
  return target;
}
test('fresh no-argument terminal launches exact legacy with opt-in once', async t=>{
 const f=setup(t);assert.equal(await main([],f.opts),0);
 assert.deepEqual(f.args,[['/test/legacy.mjs',['--','--first-run'],{stdio:'inherit',shell:false}]]);
 assert.equal(f.emit.listenerCount('SIGINT'),0);
});
test('existing managed metadata is left to exact executable; no recursive PATH or uv', async t=>{
 const f=setup(t,false);const path=install(f);
 assert.equal(await main(['--version'],f.opts),0);
 assert.deepEqual(f.args,[ [path,['--version'],{stdio:'inherit',shell:false}] ]);
});
test('help, non-TTY and cold arguments do not start setup',async t=>{
 const f=setup(t,false);
 assert.equal(await main(['--help'],f.opts),0);assert.match(f.output(),/Usage/);
 assert.equal(await main([],f.opts),2);assert.equal(await main(['--version'],f.opts),2);
 assert.deepEqual(f.args,[]);
});
test('incomplete existing roots and linked binary are not bootstrapped', async t=>{
 const f=setup(t);const root=defaultRoot({},f.h);
 mkdirSync(root,{recursive:true,mode:0o700});
 assert.equal(await main([],f.opts),2);assert.deepEqual(f.args,[]);
 const target=install(f);rmSync(target);symlinkSync('/bin/sh',target);
 assert.equal(await main([],f.opts),2);assert.deepEqual(f.args,[]);
});
test('cancellation signal keeps its exit status',async t=>{
 const f=setup(t,true,[null,'SIGINT']);
 const waiting=main([],f.opts);
 f.emit.emit('SIGINT');assert.equal(f.killed(),'SIGINT');
 assert.equal(await waiting,130);
});
test('absolute XDG honored and relative XDG ignored',()=>{
 assert.equal(defaultRoot({XDG_DATA_HOME:'/absolute'},'/home/test'),'/absolute/rpnh');
 assert.equal(defaultRoot({XDG_DATA_HOME:'relative'},'/home/test'),'/home/test/.local/share/rpnh');
});
