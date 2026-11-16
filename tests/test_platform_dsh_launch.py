"""Offline DSH preparation/launch boundary with a real local helper process.

The helper emulates Node command results and pipe lifetime only. These tests do
not install DSH, execute upstream JavaScript, or certify Linux/macOS native UI.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib.resources
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from cpn.frontend import dsh_assets, dsh_launcher
from cpn.rpnh import frontend_setup


class DshPreparedLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rpnh-dsh-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.log = self.root / "node-calls.jsonl"
        self.failure = self.root / "fail-native-check"
        self.binary = self.root / "node-runtime-fixture"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.binary.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys
args = sys.argv[1:]
keys = ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME',
        'XDG_STATE_HOME', 'TMPDIR', 'TSX_DISABLE_CACHE', 'TSX_TSCONFIG_PATH',
        'RPNH_DSH_CALLER_CWD', 'OPENAI_API_KEY', 'RPNH_EXECUTION_CONFIG',
        'NODE_OPTIONS', 'DSH_HOME', 'PYTHONPATH', 'HTTPS_PROXY')
record = {'argv': sys.argv, 'environment': {k: os.environ[k] for k in keys if k in os.environ},
          'cwd': os.getcwd()}
with pathlib.Path(LOG).open('a', encoding='utf-8') as out:
    out.write(json.dumps(record) + '\\n')
if args == ['--version']:
    print('v24.22.1')
elif args[-1] == '--check':
    assert os.environ['TSX_DISABLE_CACHE'] == '1'
    assert pathlib.Path(os.environ['TSX_TSCONFIG_PATH']).is_file()
    if pathlib.Path(FAILURE).exists():
        print('fixture: a prepared native asset is missing', file=sys.stderr)
        sys.exit(7)
elif args[-1].endswith('/setup.ts'):
    target = pathlib.Path(args[-1]).parent
    bridge = target / 'generated/bound-client'
    bridge.mkdir(parents=True, exist_ok=True)
    (bridge / 'host.ts').write_text('export function apply() {}')
    (target / 'official.json').write_text(json.dumps([
        {'id': 'bound-session', 'name': str(bridge / 'host.ts')}]))
    (target / 'tsconfig.json').write_text('{"extends":"../tsconfig.base.json"}')
else:
    assert '--read-fd' in args and '--write-fd' in args
    os.close(int(args[args.index('--read-fd') + 1]))
    os.close(int(args[args.index('--write-fd') + 1]))
'''.replace("LOG", repr(str(self.log))).replace("FAILURE", repr(str(self.failure))), encoding="utf-8")
        self.binary.chmod(0o700)
        (self.bin / "node").symlink_to(self.binary)
        self.environment = mock.patch.dict(os.environ, {
            "PATH": str(self.bin), "RPNH_DSH_FRONTEND_CANDIDATE": "",
            "OPENAI_API_KEY": "fixture-secret", "NODE_OPTIONS": "fixture-option",
            "DSH_HOME": "fixture-home", "PYTHONPATH": "fixture-pythonpath",
            "RPNH_EXECUTION_CONFIG": "fixture-profile", "HTTPS_PROXY": "fixture-proxy",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def _source(self, name="source", *, prepared=True):
        source = self.root / name
        source.mkdir()
        upstream, manifest = dsh_assets.official_web_profile()
        (source / "package.json").write_text(json.dumps({
            "name": "@deepseek-ai/dsh-root", "version": upstream["version"]}), encoding="utf-8")
        (source / "tsconfig.base.json").write_text("{}", encoding="utf-8")
        tsx = source / "node_modules/.bin/tsx"
        tsx.parent.mkdir(parents=True)
        tsx.touch()
        if prepared:
            target = source / "rpnh-frontend"
            target.mkdir()
            resource = importlib.resources.files("integrations.dsh").joinpath("frontend")
            for module in resource.iterdir():
                if module.name.endswith(".ts") and not module.name.endswith(".test.ts"):
                    (target / module.name).write_bytes(module.read_bytes())
            (target / "tsconfig.json").write_text('{"extends":"../tsconfig.base.json"}', encoding="utf-8")
            bridge = target / "generated/bound-client"
            bridge.mkdir(parents=True)
            (bridge / "host.ts").write_text("export function apply() {}", encoding="utf-8")
            (target / "official.json").write_text(json.dumps([
                {"id": "bound-session", "name": str(bridge / "host.ts")}]), encoding="utf-8")
            (target / "web-packages.json").write_text(json.dumps(manifest), encoding="utf-8")
        return source

    def _calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    @staticmethod
    def _source_state(source):
        return {str(path.relative_to(source)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in source.rglob("*") if path.is_file()}

    def test_missing_preparation_is_rejected_before_owner_or_download(self):
        source = self._source(prepared=False)
        session = self.root / "session"
        with mock.patch.object(dsh_launcher, "FrontendGateway") as owner, \
                mock.patch.object(dsh_launcher, "prepare_dsh_frontend") as prepare, \
                mock.patch.object(dsh_assets, "urlopen") as download:
            with self.assertRaisesRegex(RuntimeError, "frontend setup dsh --install"):
                dsh_launcher.run_dsh_frontend(session, self.root / "execution.json", source=source)
        owner.assert_not_called()
        prepare.assert_not_called()
        download.assert_not_called()
        self.assertEqual(self._calls(), [])
        self.assertFalse((source / "rpnh-frontend").exists())
        self.assertFalse(session.exists())

    def test_missing_required_files_are_rejected_before_native_probe(self):
        for index, relative in enumerate(("node_modules/.bin/tsx", "tsconfig.base.json",
                                          "rpnh-frontend/frontend.ts", "rpnh-frontend/tsconfig.json",
                                          "rpnh-frontend/official.json")):
            with self.subTest(relative=relative):
                source = self._source(f"missing-{index}")
                (source / relative).unlink()
                with mock.patch.object(dsh_launcher, "FrontendGateway") as owner, \
                        self.assertRaisesRegex(RuntimeError, "frontend setup dsh --install"):
                    dsh_launcher.run_dsh_frontend(self.root / "session", self.root / "execution.json", source=source)
                owner.assert_not_called()
        self.assertEqual(self._calls(), [])

    def test_same_version_with_different_package_set_is_not_reused(self):
        source = self._source()
        manifest = source / "rpnh-frontend/web-packages.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["packages"].pop()
        manifest.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            dsh_launcher.probe_dsh_frontend(source, {"PATH": str(self.bin)})
        self.assertEqual(self._calls(), [])

    def test_stale_rpnh_adapter_is_rejected_before_native_probe_or_owner(self):
        source = self._source()
        adapter = source / "rpnh-frontend/official_bff.ts"
        adapter.write_text(adapter.read_text(encoding="utf-8") + "\n// prior RPNH adapter\n", encoding="utf-8")
        with mock.patch.object(dsh_launcher, "FrontendGateway") as owner, \
                self.assertRaisesRegex(RuntimeError, "adapter differs.*frontend setup dsh --install"):
            dsh_launcher.run_dsh_frontend(self.root / "session", self.root / "execution.json", source=source)
        owner.assert_not_called()
        self.assertEqual(self._calls(), [])

    def test_copied_preparation_cannot_reuse_the_original_host_paths(self):
        original = self._source()
        copied = self.root / "copied"
        shutil.copytree(original, copied)
        for original_present in (True, False):
            with self.subTest(original_present=original_present):
                if not original_present:
                    shutil.rmtree(original)
                with mock.patch.object(dsh_launcher, "FrontendGateway") as owner, \
                        self.assertRaisesRegex(RuntimeError, "prepared host paths.*frontend setup dsh --install"):
                    dsh_launcher.run_dsh_frontend(self.root / "session", self.root / "execution.json", source=copied)
                owner.assert_not_called()
        self.assertEqual(self._calls(), [])

    def test_missing_generated_host_is_rejected_before_native_probe(self):
        source = self._source()
        (source / "rpnh-frontend/generated/bound-client/host.ts").unlink()
        with self.assertRaisesRegex(RuntimeError, "prepared host paths"):
            dsh_launcher.probe_dsh_frontend(source, frontend_setup._probe_environment())
        self.assertEqual(self._calls(), [])

    def test_readonly_probe_returns_absolute_node_and_preserves_source(self):
        source = self._source()
        before = self._source_state(source)
        with mock.patch.object(dsh_assets, "urlopen", side_effect=AssertionError("download")), \
                mock.patch.object(dsh_launcher, "prepare_dsh_frontend", side_effect=AssertionError("prepare")):
            node, entry = dsh_launcher.probe_dsh_frontend(source, frontend_setup._probe_environment())
        self.assertEqual(node, str(self.binary.resolve()))
        self.assertEqual(entry, source / "rpnh-frontend/frontend.ts")
        self.assertEqual(self._source_state(source), before)
        calls = self._calls()
        self.assertEqual([call["argv"][-1] for call in calls], ["--version", "--check"])
        self.assertEqual(calls[-1]["environment"]["TSX_DISABLE_CACHE"], "1")
        self.assertEqual(calls[-1]["environment"]["HOME"], os.devnull)

    def test_native_check_failure_does_not_construct_owner(self):
        source = self._source()
        self.failure.touch()
        with mock.patch.object(dsh_launcher, "FrontendGateway") as owner, \
                mock.patch.object(dsh_launcher, "prepare_dsh_frontend") as prepare, \
                self.assertRaisesRegex(RuntimeError, "frontend setup dsh --install"):
            dsh_launcher.run_dsh_frontend(self.root / "session", self.root / "execution.json", source=source)
        owner.assert_not_called()
        prepare.assert_not_called()
        self.assertEqual(self._calls()[-1]["argv"][-1], "--check")

    def test_ordinary_launch_uses_probed_node_and_temporary_ui_environment(self):
        source = self._source()
        before = self._source_state(source)
        gateway = mock.Mock()
        gateway.call.return_value = [{"id": "ses_fixture"}]
        def owner(_factory):
            # Resolve/launch must not consult a changed parent PATH again.
            os.environ["PATH"] = str(self.root / "changed-path")
            return gateway
        original_which = shutil.which
        with mock.patch.object(dsh_launcher, "FrontendGateway", side_effect=owner), \
                mock.patch.object(dsh_launcher.shutil, "which", wraps=original_which) as which, \
                mock.patch.object(dsh_launcher, "prepare_dsh_frontend", side_effect=AssertionError("prepare")), \
                mock.patch.object(dsh_assets, "prepare_official_web_assets", side_effect=AssertionError("asset preparation")), \
                mock.patch.object(dsh_assets, "urlopen", side_effect=AssertionError("download")), \
                mock.patch.object(dsh_launcher.shutil, "copyfile", side_effect=AssertionError("copy")), \
                redirect_stdout(io.StringIO()):
            result = dsh_launcher.run_dsh_frontend(
                self.root / "session", self.root / "execution.json", source=source, open_browser=False)
        self.assertEqual(result, 0)
        which.assert_called_once_with("node", path=str(self.bin))
        gateway.close.assert_called_once_with()
        calls = self._calls()
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call["argv"][0] == str(self.binary.resolve()) for call in calls))
        self.assertIn("--no-open-browser", calls[-1]["argv"])
        native_env = calls[-1]["environment"]
        self.assertFalse(set(native_env) & {
            "OPENAI_API_KEY", "NODE_OPTIONS", "DSH_HOME", "RPNH_EXECUTION_CONFIG", "PYTHONPATH", "HTTPS_PROXY"})
        self.assertEqual(native_env["TSX_DISABLE_CACHE"], "1")
        self.assertEqual(native_env["RPNH_DSH_CALLER_CWD"], str(Path.cwd().resolve()))
        scratch = Path(native_env["HOME"]).parent
        self.assertTrue(all(Path(native_env[key]).parent == scratch for key in (
            "HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")))
        self.assertFalse(scratch.exists())
        self.assertEqual(self._source_state(source), before)

    def test_explicit_preparation_keeps_default_and_candidate_pins(self):
        for selected, version in (("", "0.2.2-alpha.2"), ("0.2.2-rc.1", "0.2.2-rc.1")):
            with self.subTest(selected=selected), \
                    mock.patch.dict(os.environ, {dsh_assets.DSH_CANDIDATE_ENV: selected}):
                source = self._source(version, prepared=False)
                with mock.patch.object(dsh_assets, "prepare_official_web_assets") as prepare_assets:
                    node, entry = dsh_launcher.prepare_dsh_frontend(source, {"PATH": str(self.bin)})
                prepare_assets.assert_called_once_with(source / "rpnh-frontend/node_modules")
                self.assertEqual(node, str(self.binary.resolve()))
                self.assertEqual(entry, source / "rpnh-frontend/frontend.ts")
                manifest = json.loads((entry.parent / "web-packages.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest, dsh_assets.official_web_profile()[1])
                self.assertEqual(manifest["version"], version)
        self.assertEqual(dsh_assets.official_web_profile()[0]["version"], "0.2.2-alpha.2")

    def test_discovery_delegates_to_same_readonly_probe(self):
        source = self._source()
        with mock.patch.object(dsh_launcher, "probe_dsh_frontend", return_value=(str(self.binary), source / "entry")) as probe:
            self.assertEqual(frontend_setup._probe_dsh(source), source.resolve())
        self.assertEqual(probe.call_args.args[0], source.resolve())
        self.assertEqual(probe.call_args.args[1]["TSX_DISABLE_CACHE"], "1")

    def test_platform_failure_precedes_owner_node_or_preparation(self):
        source = self._source()
        with mock.patch.object(dsh_launcher, "require_platform_support", side_effect=RuntimeError("waitid is missing")), \
                mock.patch.object(dsh_launcher, "FrontendGateway") as owner, \
                mock.patch.object(dsh_launcher, "prepare_dsh_frontend") as prepare, \
                self.assertRaisesRegex(RuntimeError, "waitid"):
            dsh_launcher.run_dsh_frontend(self.root / "session", self.root / "execution.json", source=source)
        owner.assert_not_called()
        prepare.assert_not_called()
        self.assertEqual(self._calls(), [])


if __name__ == "__main__":
    unittest.main()
