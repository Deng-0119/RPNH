// Defaults only; all installation remains in the original Python installer.
import {spawnSync} from 'node:child_process';
export function discoverPython({run=spawnSync}={}) {
  const code="import json,sys;print(json.dumps({'path':sys.executable,'base':sys.prefix==sys.base_prefix,'version':list(sys.version_info[:2])}))";
  for(const name of ['python3','python']) {
    const r=run(name,['-I','-c',code],{encoding:'utf8',timeout:15000,shell:false});
    if(r.status!==0) continue;
    try {const p=JSON.parse(r.stdout); if(p.base && typeof p.path==='string' && p.path.startsWith('/') && p.version[0]===3 && p.version[1]>=11) return p.path;} catch {}
  }
  throw new Error('A persistent Python 3.11+ is required. Install Python or pass --controller-python PATH; no Python is downloaded automatically.');
}
export function convenientArguments(argv,{discover=discoverPython}={}) {
  const split=argv.indexOf('--');
  const raw=split<0?argv:argv.slice(0,split);
  const control=raw.flatMap(x=>x.startsWith('--controller-python=')?['--controller-python',x.slice('--controller-python='.length)]:[x]);
  const app=split<0?[]:argv.slice(split+1);
  const pos=control.indexOf('--controller-python');
  const python=pos<0?discover():control[pos+1];
  if(!python || python.startsWith('--')) throw new Error('--controller-python requires a path');
  const c=pos<0?[...control,'--controller-python',python]:control;
  const a=[...app];
  const has=name=>a.some(x=>x===name||x.startsWith(name+'='));
  if(!has('--python')&&!has('--runtime-bindings')&&!has('--candidate-wheel')&&!has('--help')) a.push('--python',python);
  return [...c,'--',...(a.length?a:['--python',python])];
}
