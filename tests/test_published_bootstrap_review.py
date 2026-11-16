"""Independent regression checks for published-bootstrap review findings.

Official-looking metadata and wheel streams are the existing synthetic transport
fixture. Race injection changes real temporary files/directories; it does not
replace source, material, acquisition, target or lease validation. Stage spies
only make an unexpected write boundary fail visibly. CLI committed-state tests
are error-presentation checks with an explicitly supplied ActivationError, not
an assertion that a real activation ran in that case.
"""
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cpn.rpnh import install_v2, published_install, update_cli, updates
from tests.test_published_bootstrap import BootstrapFixture


class _Terminal(StringIO):
    def isatty(self):
        return True


class PublishedBootstrapReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rpnh-bootstrap-review-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.fixture = BootstrapFixture(self.base)
        self.root = self.base / "new-managed-root"

    def test_root_appearing_during_precreation_refresh_is_never_adopted(self):
        observations = 0
        raced_marker = None

        def metadata(url):
            nonlocal observations, raced_marker
            if url == updates.RELEASES_URL:
                observations += 1
                if observations == 3:
                    # Initial inspect, bootstrap refresh, then executor's
                    # pre-root verification. Another owner arrives here.
                    install_v2.create_root(self.root)
                    raced_marker = (self.root / "managed.json").read_bytes()
            return self.fixture.get_json(url)

        with mock.patch.object(published_install, "get_json", side_effect=metadata), \
                mock.patch.object(published_install, "_open_wheel", side_effect=self.fixture.open_wheel), \
                mock.patch("cpn.rpnh.install_executor.create_stage_locked",
                           side_effect=AssertionError("raced-in root must not receive a stage")) as stage:
            request = self.fixture.inspect()
            with self.assertRaisesRegex(ValueError, "new dedicated"):
                published_install.bootstrap_published(self.root, request)
        self.assertEqual(observations, 3)
        self.assertIsNotNone(raced_marker)
        self.assertEqual((self.root / "managed.json").read_bytes(), raced_marker)
        self.assertEqual(list((self.root / "versions").iterdir()), [])
        self.assertFalse((self.root / "current").exists())
        stage.assert_not_called()

    def test_acquired_file_changed_during_ex_refresh_fails_before_stage(self):
        observations = 0
        acquired_paths = []
        original_observation = published_install._file_observation

        def observe(path):
            value = original_observation(path)
            if Path(path) not in acquired_paths:
                acquired_paths.append(Path(path))
            return value

        def metadata(url):
            nonlocal observations
            if url == updates.RELEASES_URL:
                observations += 1
                if observations == 4:
                    # The material reader has already loaded this selection
                    # under EX. Change its actual acquired input during the
                    # following network refresh, before the final stat check.
                    self.assertTrue(acquired_paths)
                    path = acquired_paths[0]
                    path.write_bytes(path.read_bytes() + b"changed-during-refresh")
            return self.fixture.get_json(url)

        with mock.patch.object(published_install, "get_json", side_effect=metadata), \
                mock.patch.object(published_install, "_open_wheel", side_effect=self.fixture.open_wheel), \
                mock.patch.object(published_install, "_file_observation", side_effect=observe), \
                mock.patch("cpn.rpnh.install_executor.create_stage_locked",
                           side_effect=AssertionError("changed acquisition must not reach a stage")) as stage:
            request = self.fixture.inspect()
            with self.assertRaisesRegex(ValueError, "material changed"):
                published_install.bootstrap_published(self.root, request)
        self.assertEqual(observations, 4)
        self.assertEqual(list((self.root / "versions").iterdir()), [])
        self.assertFalse((self.root / "current").exists())
        self.assertFalse(any(path.exists() for path in acquired_paths))
        stage.assert_not_called()

    def _cli_arguments(self):
        platform = self.base / "platform.json"
        runtimes = self.base / "runtime-bindings.json"
        platform.write_text(json.dumps(self.fixture.platform), encoding="utf-8")
        runtimes.write_text(json.dumps(self.fixture.runtimes), encoding="utf-8")
        return ["bootstrap", "--root", str(self.root), "--release", self.fixture.tag,
                "--platform", str(platform), "--runtime-bindings", str(runtimes), "--json"]

    def test_cli_inspect_only_never_confirms_or_downloads(self):
        output = StringIO()
        with mock.patch.object(published_install, "get_json", side_effect=self.fixture.get_json), \
                mock.patch.object(published_install, "_open_wheel", side_effect=AssertionError("inspect must not acquire")) as download, \
                mock.patch.object(update_cli, "_confirm", side_effect=AssertionError("inspect must not ask to install")) as confirm, \
                mock.patch("sys.stdout", output):
            status = update_cli.main(self._cli_arguments() + ["--inspect-only"])
        self.assertEqual(status, 0)
        document = json.loads(output.getvalue())
        self.assertEqual(document["operation"], "bootstrap")
        self.assertEqual(document["materials"]["root_extras"], [])
        self.assertEqual(document["source"]["kind"], "published_release")
        self.assertFalse(self.root.exists())
        download.assert_not_called()
        confirm.assert_not_called()

    def test_cli_nonterminal_cannot_acquire_or_install(self):
        output = StringIO()
        with mock.patch.object(published_install, "get_json", side_effect=self.fixture.get_json), \
                mock.patch.object(published_install, "_open_wheel", side_effect=AssertionError("no terminal confirmation")) as download, \
                mock.patch("sys.stdin", StringIO("UPDATE\n")), mock.patch("sys.stdout", output):
            status = update_cli.main(self._cli_arguments())
        self.assertEqual(status, 2)
        document = json.loads(output.getvalue())
        self.assertIn("confirmation in a terminal", document["error"])
        self.assertFalse(self.root.exists())
        download.assert_not_called()

    def test_cli_keeps_explicit_committed_error_state(self):
        # The original transaction's error is supplied, rather than pretending
        # this presentation-only test committed a software installation.
        error = install_v2.ActivationError(current="versions/i-" + "a" * 32, committed=True)
        output = _Terminal()
        with mock.patch.object(published_install, "get_json", side_effect=self.fixture.get_json), \
                mock.patch.object(published_install, "bootstrap_published", side_effect=error) as bootstrap, \
                mock.patch("sys.stdin", _Terminal("UPDATE\n")), mock.patch("sys.stdout", output):
            status = update_cli.main(self._cli_arguments())
        self.assertEqual(status, 2)
        raw = output.getvalue()
        document = json.loads(raw[raw.index('{"schema_version": "rpnh/update_error/v2"'):])
        self.assertEqual(document["committed"], True)
        self.assertEqual(document["current"], error.current)
        self.assertEqual(document["activation_status"], "committed_recovery_required")
        bootstrap.assert_called_once()
        self.assertFalse(self.root.exists())

    def test_actual_postcommit_failure_survives_acquisition_cleanup_and_recovers(self):
        directory = self.base / "runnable"
        directory.mkdir()
        fixture = BootstrapFixture(directory, runnable=True)
        actual_pointer = install_v2._set_pointer

        def interrupt_after_current(root, name, target):
            if name == "previous":
                raise OSError("review interruption after the actual current commit")
            return actual_pointer(root, name, target)

        with mock.patch.object(published_install, "get_json", side_effect=fixture.get_json), \
                mock.patch.object(published_install, "_open_wheel", side_effect=fixture.open_wheel), \
                mock.patch.object(install_v2, "_set_pointer", side_effect=interrupt_after_current):
            request = fixture.inspect()
            with self.assertRaises(install_v2.ActivationError) as caught:
                published_install.bootstrap_published(self.root, request)
        failure = caught.exception
        self.assertIs(failure.committed, True)
        selected = install_v2.read_target(self.root)
        self.assertEqual(failure.current, selected[0])
        self.assertEqual(selected[2]["source"]["kind"], "published_release")
        self.assertTrue(selected[2]["complete"])
        material = json.loads((selected[1] / "materials.json").read_text(encoding="utf-8"))
        self.assertTrue(all(not Path(row["path"]).exists() for row in material["python_wheels"]))
        self.assertIsNotNone(install_v2.marker(self.root)["pending_activation"])
        recovered = install_v2.recover(self.root)
        self.assertIs(recovered.committed, True)
        self.assertEqual(recovered.current, selected[0])
        self.assertIsNone(install_v2.marker(self.root)["pending_activation"])
        self.assertIsNone(install_v2.read_target(self.root, "previous", allow_missing=True))


if __name__ == "__main__":
    unittest.main()
