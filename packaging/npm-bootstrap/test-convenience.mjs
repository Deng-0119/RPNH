import assert from 'node:assert/strict';
import {discoverPython,convenientArguments} from './convenience.mjs';
assert.deepEqual(convenientArguments([],{discover:()=>'/python'}),['--controller-python','/python','--','--python','/python']);
assert.deepEqual(convenientArguments(['--controller-python','/p','--','--python','/target','--root','/r'],{discover:()=>{throw 1}}),['--controller-python','/p','--','--python','/target','--root','/r']);
assert.deepEqual(convenientArguments(['--','--candidate-wheel','x','--materials','m'],{discover:()=>'/p'}),['--controller-python','/p','--','--candidate-wheel','x','--materials','m']);
let n=0;assert.equal(discoverPython({run:()=>({status:0,stdout:JSON.stringify({path:'/p',base:++n>1,version:[3,12]})})}),'/p');
assert.throws(()=>discoverPython({run:()=>({status:127})}),/persistent Python/);
console.log('PASS default argv, explicit target, candidate compatibility, venv skipped, missing Python diagnostic');

for (const option of ['--python=/explicit','--runtime-bindings=/r.json','--candidate-wheel=/w']) {const a=convenientArguments(['--',option],{discover:()=>'/auto'});assert.equal(a.filter(x=>x==='--python').length,0);assert.ok(a.includes(option));}
assert.deepEqual(convenientArguments(['--controller-python=/explicit','--','--help'],{discover:()=>{throw 1}}),['--controller-python','/explicit','--','--help']);
assert.throws(()=>convenientArguments(['--controller-python='],{discover:()=>{throw 1}}),/requires a path/);
