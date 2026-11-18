import importlib.util,sys,tempfile,unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from tests.test_published_bootstrap import BootstrapFixture
from cpn.rpnh import published_install as m
class Unknown(unittest.TestCase):
 def test_real_request_retains_unknown_and_no_qualification(self):
  with tempfile.TemporaryDirectory() as t:
   f=BootstrapFixture(Path(t));f.platform['emulated']=None;f.validation['results']=[];f.support['combinations'][0]['evidence']=[]
   with patch.object(m,'get_json',f.get_json):
    d=m.inspect_bootstrap(release=f.tag,channel='stable',platform=f.platform,runtime_bindings=f.runtimes).to_document()
   self.assertIsNone(d['platform']['emulated']);self.assertFalse(d['qualification_gaps']['qualification_granted']);self.assertEqual(f.wheel_calls,[])
 def test_extra_unknown_refused(self):
  with tempfile.TemporaryDirectory() as t:
   f=BootstrapFixture(Path(t),extras=['mcp']);f.platform['emulated']=None
   with self.assertRaises(m.PublishedBootstrapError),patch.object(m,'get_json',f.get_json):m.inspect_bootstrap(release=f.tag,channel='stable',platform=f.platform,runtime_bindings=f.runtimes,root_extras=['mcp'])
 def test_different_python_materials_select_actual_minor_not_first(self):
  with tempfile.TemporaryDirectory() as t:
   f=BootstrapFixture(Path(t));f.validation['results']=[];f.support['combinations'][0]['evidence']=[]
   selected=f.support['combinations'][0];other=deepcopy(selected);other['id']='wrong-minor';other['runtimes']['python']['version']='3.8.0'
   f.support['combinations'].insert(0,other)
   with patch.object(m,'get_json',f.get_json):
    d=m.inspect_bootstrap(release=f.tag,channel='stable',platform=f.platform,runtime_bindings=f.runtimes).to_document()
   self.assertEqual(d['combination'],selected['id'])
 def test_same_minor_ambiguous_materials_not_arbitrarily_chosen(self):
  with tempfile.TemporaryDirectory() as t:
   f=BootstrapFixture(Path(t));f.validation['results']=[];f.support['combinations'][0]['evidence']=[]
   other=deepcopy(f.support['combinations'][0]);other['id']='same-minor';f.support['combinations'].append(other)
   with patch.object(m,'get_json',f.get_json):
    with self.assertRaises(m.PublishedBootstrapError):m.inspect_bootstrap(release=f.tag,channel='stable',platform=f.platform,runtime_bindings=f.runtimes)
    d=m.inspect_bootstrap(release=f.tag,channel='stable',platform=f.platform,runtime_bindings=f.runtimes,combination='same-minor').to_document()
   self.assertEqual(d['combination'],'same-minor')
 def test_single_material_set_does_not_regress_to_historical_minor_gate(self):
  with tempfile.TemporaryDirectory() as t:
   f=BootstrapFixture(Path(t));f.validation['results']=[];f.support['combinations'][0]['evidence']=[]
   f.support['combinations'][0]['runtimes']['python']['version']='3.8.0'
   with patch.object(m,'get_json',f.get_json):
    d=m.inspect_bootstrap(release=f.tag,channel='stable',platform=f.platform,runtime_bindings=f.runtimes).to_document()
   self.assertEqual(d['runtime_bindings'],f.runtimes)
if __name__=='__main__':unittest.main()
