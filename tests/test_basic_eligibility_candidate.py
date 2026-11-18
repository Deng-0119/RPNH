"""Synthetic official transport, real original wheel readers; no public qualification."""
from copy import deepcopy
from pathlib import Path
import unittest
from tests.test_published_bootstrap import BootstrapTestCase,bootstrap
from tests import test_install_materials as material_fixtures
fixture_wheel = material_fixtures.fixture_wheel

class BasicSelection(BootstrapTestCase):
 def no_history(self):
  self.fixture.validation['results']=[]
  self.fixture.support['combinations'][0]['evidence']=[]
 def test_historical_kernel_and_python_patch_do_not_block_request(self):
  self.no_history();row=self.fixture.support['combinations'][0]
  row['platform']['os_release']='historical-kernel'
  row['runtimes']['python']['version']='3.12.1'
  with self.transports():d=self.fixture.inspect().to_document()
  self.assertEqual(d['runtime_bindings'],self.fixture.runtimes)
  self.assertEqual(d['platform'],self.fixture.platform)
  self.assertFalse(d['qualification_gaps']['qualification_granted'])
  self.assertEqual(d['validation']['results'],[])
  self.assertFalse(self.root.exists())
 def test_two_material_rows_require_explicit_selection(self):
  self.no_history();a=self.fixture.support['combinations'][0];b=deepcopy(a)
  b['id']='other-python-row';b['runtimes']['python']['version']=a['runtimes']['python']['version']
  self.fixture.support['combinations'].append(b)
  with self.transports():
   with self.assertRaises(bootstrap.PublishedBootstrapError):self.fixture.inspect()
   d=self.fixture.inspect(combination='other-python-row').to_document()
  self.assertEqual(d['combination'],'other-python-row')
 def test_wrong_arch_still_rejected(self):
  self.no_history();self.fixture.support['combinations'][0]['platform']['architecture']='unrelated-arch'
  with self.transports(),self.assertRaises(bootstrap.PublishedBootstrapError):self.fixture.inspect()

class RealWheelMetadata(unittest.TestCase):
 def fixture(self):
  x=material_fixtures.MaterialsTests('runTest');x.setUp();self.addCleanup(x.doCleanups);return x
 def test_python_patch_requires_python_is_still_enforced(self):
  x=self.fixture();v=x.runtimes['python']['version'].split('.');v[-1]=str(int(v[-1])+1)
  x.document['python_wheels'][0]=fixture_wheel(x.root,'rpnh-harness',x.version,requires_python='>='+'.'.join(v))
  with self.assertRaisesRegex(ValueError,'does not support'):x.load()
 def test_python_full_version_marker_uses_target(self):
  x=self.fixture();v=x.runtimes['python']['version']
  x.document['python_wheels'][0]=fixture_wheel(x.root,'rpnh-harness',x.version,[f"missing-dep; python_full_version == '{v}'"])
  with self.assertRaisesRegex(ValueError,'closure'):x.load()
 def test_platform_release_marker_uses_target(self):
  x=self.fixture();v=x.platform['os_release']
  x.document['python_wheels'][0]=fixture_wheel(x.root,'rpnh-harness',x.version,[f"missing-dep; platform_release == '{v}'"])
  with self.assertRaisesRegex(ValueError,'closure'):x.load()
