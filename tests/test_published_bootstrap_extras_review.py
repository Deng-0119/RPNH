"""Independent checks for the recovered Basic published-extras candidate.

All release metadata and asset responses are local BootstrapFixture transport.
The success case executes real venv/pip/prepare/probe/current with reduced
fixture packages. No remote service, native-task qualification or real SDK is
exercised here.
"""
from contextlib import contextmanager
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cpn.rpnh import install_v2, published_install, update_cli, updates
from tests.test_published_bootstrap import BootstrapFixture


class RecoveredExtrasReview(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rpnh-extras-independent-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "managed"
        self.number = 0

    def fixture(self, **options):
        self.number += 1
        directory = self.base / f"fixture-{self.number}"
        directory.mkdir()
        return BootstrapFixture(directory, **options)

    @contextmanager
    def transports(self, fixture, *, get_json=None):
        with mock.patch.object(published_install, "get_json", side_effect=get_json or fixture.get_json), \
                mock.patch.object(published_install, "_open_wheel", side_effect=fixture.open_wheel):
            yield

    def cli_arguments(self, fixture):
        platform = self.base / "platform.json"
        runtimes = self.base / "runtimes.json"
        platform.write_text(json.dumps(fixture.platform))
        runtimes.write_text(json.dumps(fixture.runtimes))
        return ["bootstrap", "--root", str(self.root), "--release", fixture.tag,
                "--platform", str(platform), "--runtime-bindings", str(runtimes), "--json"]

    def test_explicit_single_and_combined_choices_never_enable_implicitly(self):
        for extras in (("mcp",), ("skills",), ("skills", "mcp")):
            fixture = self.fixture(extras=extras)
            with self.subTest(extras=extras), self.transports(fixture):
                for options in ({}, {"combination": "basic-native-fixture"}):
                    with self.assertRaises(ValueError):
                        fixture.inspect(**options)
                document = fixture.inspect(root_extras=extras).to_document()
                self.assertEqual(document["materials"]["root_extras"], sorted(extras))
                self.assertEqual(document["qualification_gaps"]["root_extras"]["selected"], sorted(extras))
                self.assertEqual(document["qualification_gaps"]["root_extras"]["status"],
                                 "matches_exact_backed_selection")
                self.assertEqual(document["qualification_gaps"]["dependency_materials"]["status"],
                                 "exact_selection_matches_backed_metadata")
                self.assertEqual(fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_invalid_duplicate_or_unsupported_extras_reject_before_metadata(self):
        fixture = self.fixture()
        invalid = [None, "mcp", {"mcp"}, [1], ["MCP"], ["mcp", "mcp"], ["all"], ["skills", "unknown"]]
        with mock.patch.object(published_install, "get_json", side_effect=AssertionError("invalid selection read metadata")) as read:
            for selected in invalid:
                with self.subTest(selected=selected), self.assertRaises(ValueError):
                    fixture.inspect(root_extras=selected)
        read.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_empty_selection_document_preserves_previous_behavior(self):
        fixture = self.fixture()
        with self.transports(fixture):
            omitted = fixture.inspect().to_document()
            explicit = fixture.inspect(root_extras=[]).to_document()
            self.assertEqual(omitted, explicit)
            refreshed = published_install._refresh(published_install.BootstrapRequest(json.dumps(omitted)))
            self.assertEqual(refreshed, omitted)
        self.assertFalse(self.root.exists())

    def test_extras_changed_after_confirmation_reject_before_download_and_root(self):
        fixture = self.fixture(extras=("mcp", "skills"))
        with self.transports(fixture):
            request = fixture.inspect(root_extras=["mcp", "skills"])
            for row in (fixture.support["combinations"][0], fixture.validation["results"][0]):
                row["materials"]["root_extras"] = ["mcp"]
            with self.assertRaises(ValueError):
                published_install.bootstrap_published(self.root, request)
        self.assertEqual(fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_extras_changed_under_ex_reject_before_stage_or_current(self):
        fixture = self.fixture(extras=("mcp", "skills"))
        count = 0

        def metadata(url):
            nonlocal count
            if url == updates.RELEASES_URL:
                count += 1
                if count == 4:
                    for row in (fixture.support["combinations"][0], fixture.validation["results"][0]):
                        row["materials"]["root_extras"] = ["mcp"]
            return fixture.get_json(url)

        with self.transports(fixture, get_json=metadata), \
                mock.patch("cpn.rpnh.install_executor.create_stage_locked",
                           side_effect=AssertionError("changed selection reached stage")) as stage:
            request = fixture.inspect(root_extras=["mcp", "skills"])
            with self.assertRaises(ValueError):
                published_install.bootstrap_published(self.root, request)
        self.assertEqual(count, 4)
        self.assertTrue(self.root.exists())
        self.assertEqual(list((self.root / "versions").iterdir()), [])
        self.assertFalse((self.root / "current").exists())
        stage.assert_not_called()

    def test_missing_optional_dependency_in_backed_metadata_fails_closure_before_root(self):
        fixture = self.fixture(extras=("mcp", "skills"))
        missing = "fixture-optional-skills"
        for row in (fixture.support["combinations"][0], fixture.validation["results"][0]):
            row["materials"]["python_wheels"] = [wheel for wheel in row["materials"]["python_wheels"]
                                                  if wheel["name"] != missing]
        with self.transports(fixture):
            request = fixture.inspect(root_extras=["mcp", "skills"])
            with self.assertRaisesRegex(ValueError, "closure|missing"):
                published_install.bootstrap_published(self.root, request)
        self.assertEqual(len(fixture.wheel_calls), 3)
        self.assertFalse(self.root.exists())

    def test_cli_inspection_and_confirmation_keep_complete_explicit_choice(self):
        fixture = self.fixture(extras=("mcp", "skills"))
        arguments = self.cli_arguments(fixture) + ["--extra", "skills", "--extra", "mcp"]
        output = StringIO()
        with self.transports(fixture), mock.patch("sys.stdout", output), \
                mock.patch.object(update_cli, "_confirm", side_effect=AssertionError("inspect asked to write")) as confirm:
            status = update_cli.main(arguments + ["--inspect-only"])
        self.assertEqual(status, 0)
        document = json.loads(output.getvalue())
        self.assertEqual(document["materials"]["root_extras"], ["mcp", "skills"])
        confirm.assert_not_called()
        with self.transports(fixture), mock.patch("sys.stdout", StringIO()), \
                mock.patch.object(update_cli, "_confirm", side_effect=ValueError("review cancels before acquisition")) as confirm:
            status = update_cli.main(arguments)
        self.assertEqual(status, 2)
        self.assertIn("Python optional dependencies: mcp, skills", confirm.call_args.args[0])
        self.assertEqual(fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_nonempty_extras_real_reduced_install_preserves_exact_observations(self):
        fixture = self.fixture(runnable=True, extras=("mcp", "skills"))
        with self.transports(fixture):
            request = fixture.inspect(root_extras=["skills", "mcp"])
            result = published_install.bootstrap_published(self.root, request)
        target, directory, installed = install_v2.read_target(self.root)
        self.assertTrue(result.activation.committed)
        self.assertEqual(result.activation.current, target)
        self.assertEqual(installed["source"], fixture.source)
        self.assertEqual(set(installed["components"]), {"basic"})
        materials = json.loads((directory / "materials.json").read_text())
        probe = json.loads((directory / "probe.json").read_text())
        self.assertEqual(materials["root_extras"], ["mcp", "skills"])
        self.assertEqual(materials["published_selection"], request.to_document()["materials"])
        self.assertEqual(probe["python"]["root_extras"], ["mcp", "skills"])
        self.assertEqual(probe["python"]["pip_check"], "passed")
        self.assertEqual(set(probe["python"]["distributions"]),
                         {"rpnh-harness", "packaging", "fixture-optional-mcp", "fixture-optional-skills"})
        self.assertEqual(len(fixture.wheel_calls), 4)
        self.assertEqual(result.to_document()["native_qualification"], "not_awarded")
        self.assertFalse(result.to_document()["tasks_resumed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
