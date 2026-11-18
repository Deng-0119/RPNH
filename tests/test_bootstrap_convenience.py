import importlib.util,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from cpn.rpnh import bootstrap_entry as m
class Defaults(unittest.TestCase):
 def test_default_original_observations_unknown_emulation(self):
  captured={}
  def update(argv):
   captured['argv']=argv
   for name in ('--platform','--runtime-bindings'):
    path=Path(argv[argv.index(name)+1]);captured[name]=json.loads(path.read_text());captured[name+'_path']=path
   return 0
  host={'platform':'linux','machine':'x86_64','os_release':'observed','libc':None}
  with patch.dict('os.environ',{'XDG_DATA_HOME':'/synthetic/data'}):
   self.assertEqual(m.main(['--python','/p'],base_python=lambda _:Path('/p'),observe=lambda *_:SimpleNamespace(path=Path('/p'),version='3.12.0',implementation='cpython',architecture='x86_64'),platform_observe=lambda:host,update_main=update),0)
  self.assertIsNone(captured['--platform']['emulated']);self.assertIn('/synthetic/data/rpnh',captured['argv']);self.assertFalse(captured['--platform_path'].exists())
 def test_nonbasic_requires_explicit_platform(self):
  with self.assertRaises(SystemExit):m.main(['--python','/p','--extra','mcp'],update_main=lambda _:self.fail())
if __name__=='__main__':unittest.main()
