"""Adapter regressions with original Registry and native Owner listener.

These checks call the adapter on its owner thread. Full HTTP/DOM/Harness
settlement is covered by the separate local native acceptance driver.
"""
from pathlib import Path
import tempfile
import unittest

from cpn.frontend.dashboard import RegistryDashboard
from cpn.frontend.material_management import (
    MaterialLocationTarget, MaterialManagementError, MaterialManagementViewer,
)
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.strict_contracts import ref_payload
from material_terminal_fixture import terminal_world, finish_business


class ViewerTerminalLocationTests(unittest.TestCase):
    def world(self, terminal_capable=True):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        world = terminal_world(root / "run", terminal_capable=terminal_capable)
        resource, _, _ = finish_business(world)
        self.assertIsNotNone(world.owner.terminal())
        loop = OwnerEventLoop(world.owner, root / "owner.sock")
        self.addCleanup(loop.close)
        return world, resource, loop

    def viewer(self, world, loop, enabled):
        return MaterialManagementViewer(
            RegistryDashboard(world.owner._core.run_dir, catalog=world.owner._core.catalog),
            event_loop=loop, host=world.host, session=world.session,
            terminal_materials=enabled,
            location_targets=(MaterialLocationTarget(
                "target", "Terminal position", "manage.run", "documents/after-terminal", True),),
        )

    def catalog(self, viewer, resource):
        return viewer._dispatch({"action": "location_catalog", "source_id": viewer.source_id,
                                 "resource_ref": ref_payload(resource.as_version_ref())})

    def test_explicit_terminal_contract_discloses_bounded_target_without_writes(self):
        world, resource, loop = self.world()
        before = world.owner._core.event_store.max_ordinal()
        catalog = self.catalog(self.viewer(world, loop, True), resource)
        self.assertTrue(catalog["supported"])
        self.assertEqual(len(catalog["bind_targets"]), 1)
        self.assertEqual(catalog["bind_targets"][0]["params"]["target"], "manage.run")
        self.assertEqual(catalog["bind_targets"][0]["params"]["target_resource_ref"],
                         ref_payload(resource.as_version_ref()))
        self.assertEqual(world.owner._core.event_store.max_ordinal(), before)

    def test_default_terminal_gate_rejects_catalog_and_cached_submit_without_writes(self):
        world, resource, loop = self.world()
        allowed = self.catalog(self.viewer(world, loop, True), resource)
        closed = self.viewer(world, loop, False)
        before = world.owner._core.event_store.max_ordinal()
        with self.assertRaises(MaterialManagementError):
            self.catalog(closed, resource)
        with self.assertRaises(MaterialManagementError):
            closed._dispatch({"action": "location_submit", "source_id": closed.source_id,
                "owner_command": "material_submit", "command_id": "closed:bind",
                "params": allowed["bind_targets"][0]["params"]})
        self.assertEqual(world.owner._core.event_store.max_ordinal(), before)

    def test_opt_in_does_not_upgrade_ordinary_executor_to_terminal_capability(self):
        world, resource, loop = self.world(terminal_capable=False)
        before = world.owner._core.event_store.max_ordinal()
        with self.assertRaises((RegistryConflict, MaterialManagementError)):
            self.catalog(self.viewer(world, loop, True), resource)
        self.assertEqual(world.owner._core.event_store.max_ordinal(), before)
