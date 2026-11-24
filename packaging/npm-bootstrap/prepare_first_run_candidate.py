"""Build an unpublished transport package from one actual wheel; never publish."""
import argparse,json,re,shutil,zipfile
from email.parser import Parser
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--convenience-source',type=Path,required=True);p.add_argument('--launcher-source',type=Path,required=True,help='Repository packaging/npm-bootstrap directory');p.add_argument('--wheel',type=Path,required=True);p.add_argument('--npm-version',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--license',type=Path,required=True)
a=p.parse_args()
if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?',a.npm_version):p.error('use a concrete npm semantic version')
if a.output.exists() or a.output.is_symlink():p.error('output must be new; do not overwrite another candidate')
if not a.wheel.is_file() or a.wheel.suffix!='.whl':p.error('use an actual built RPNH wheel')
with zipfile.ZipFile(a.wheel) as z:
 metas=[n for n in z.namelist() if n.endswith('.dist-info/METADATA')]
 if len(metas)!=1:raise ValueError('one wheel distribution required')
 m=Parser().parsestr(z.read(metas[0]).decode())
 if m['Name']!='rpnh-harness':raise ValueError('not the RPNH core wheel')
 if 'cpn/rpnh/bootstrap_entry.py' not in z.namelist():raise ValueError('wheel lacks original bootstrap')
 bootstrap=z.read('cpn/rpnh/bootstrap_entry.py')
 if b'def default_root(' not in bootstrap or b'def first_run_setup(' not in bootstrap or b'"--first-run"' not in bootstrap:
  raise ValueError('wheel lacks reviewed first-run behavior; build candidate before staging')
 if not any(n.endswith('.dist-info/WHEEL') for n in z.namelist()):raise ValueError('WHEEL metadata missing')
version=m['Version']; source=a.launcher_source.resolve(strict=True)
expected=re.fullmatch(r'(\d+\.\d+\.\d+)rc([1-9]\d*)',version or '')
if not expected or a.npm_version!=expected.group(1)+'-rc.'+expected.group(2):
 p.error('wheel and npm version must agree as X.Y.ZrcN and X.Y.Z-rc.N')
if expected.group(1)=='0.1.0' and int(expected.group(2))<=8:
 p.error('do not reuse a historical version for a first-run candidate')
if not all((source/name).is_file() for name in ('launch.mjs','first-run.mjs')):p.error('launcher-source must contain original launch.mjs and candidate first-run.mjs')
if not (a.convenience_source/'convenience.mjs').is_file() or not a.license.is_file():p.error('convenience source or license missing')
a.output.mkdir(parents=True);(a.output/'controller').mkdir()
shutil.copyfile(a.wheel,a.output/'controller'/a.wheel.name)
shutil.copyfile(a.convenience_source/'convenience.mjs',a.output/'convenience.mjs');shutil.copyfile(source/'launch.mjs',a.output/'launch.mjs');shutil.copyfile(a.license,a.output/'LICENSE')
first=(source/'first-run.mjs').read_text(encoding='utf-8')
if first.count('./release-bootstrap.mjs')!=1:raise ValueError('expected one explicit legacy binding in short command')
(a.output/'first-run.mjs').write_text(first.replace('./release-bootstrap.mjs','./entry.mjs'),encoding='utf-8')
(a.output/'first-run.mjs').chmod(0o755)
package={'name':'rpnh-bootstrap','version':a.npm_version,'private':True,'description':'RPNH bootstrap transport with exact bundled controller','type':'module','license':'MIT','repository':{'type':'git','url':'git+https://github.com/Deng-0119/RPNH.git','directory':'packaging/npm-bootstrap'},'engines':{'node':'>=20'},'bin':{'rpnh-bootstrap':'entry.mjs','rpnh':'first-run.mjs'},'files':['entry.mjs','first-run.mjs','launch.mjs','convenience.mjs','controller/','README.md','LICENSE']}
(a.output/'package.json').write_text(json.dumps(package,indent=2)+'\n')
(a.output/'entry.mjs').write_text('''#!/usr/bin/env node
import {fileURLToPath} from 'node:url';
import {realpathSync} from 'node:fs';
import {launch} from './launch.mjs';
import {convenientArguments} from './convenience.mjs';
const bundled=fileURLToPath(new URL('''+json.dumps('./controller/'+a.wheel.name)+''',import.meta.url));
export function bundledArguments(argv) {
 const split=argv.indexOf('--');
 const controller=split<0?argv:argv.slice(0,split);
 if(controller.includes('--controller-wheel')) throw new Error('This package uses its exact bundled controller; use the original launcher for another wheel');
 return ['--controller-wheel',bundled,...argv];
}
if(process.argv[1] && fileURLToPath(import.meta.url)===realpathSync(process.argv[1])) {
 try {process.exitCode=await launch(bundledArguments(convenientArguments(process.argv.slice(2))));}
 catch(e){console.error('rpnh bootstrap: '+e.message);process.exitCode=2;}
}
''')
(a.output/'entry.mjs').chmod(0o755)
(a.output/'README.md').write_text(f"""# Unpublished RPNH Linux first-run candidate

Embedded core: `{a.wheel.name}` ({version}). Node20+, uv, and a persistent Python3.11+ must be installed. No Python/uv download or npm install hook is added.

After an actual matching public release and npm publication exist, run `rpnh-bootstrap` to use the bundled core's exact release, observed Linux platform, discovered persistent Python, and XDG user data root. Explicit original arguments remain supported. The original installer confirms the selected installation and owns prepare/probe/current; no second state mechanism is added.

The shorter command is explicit: use `npm exec --package=/absolute/path/this-candidate.tgz -- rpnh`. A bare npm exec/npx by the package name selects the legacy bin.\n\nThis candidate is private and is NOT publicly installable yet. Local tarball installation/help tests do not certify full installation. Existing roots are not overwritten, optional extras/frontend recipes retain explicit requirements, unknown emulation is not native qualification. No PATH edits or automatic global application-command registration occur.
""")
print(json.dumps({'output':str(a.output),'core_version':version,'npm_version':a.npm_version,'published':False,'private':True}))
