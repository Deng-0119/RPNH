"""Offline adapter tests; no installation or provider/model calls."""
import ast
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from cpn.rpnh import bootstrap_entry, update_cli

SOURCE = Path(__file__).resolve().parents[1] / 'cpn/rpnh/update_cli.py'
def product_parser():
    parsed = ast.parse(SOURCE.read_text())
    nodes = [n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name in ('_entry','_parser')]
    # Exact constants read from dce70b2 cpn/rpnh/updates.py; no product imports or I/O.
    ns = dict(argparse=argparse, Path=Path, updates=SimpleNamespace(
        ENTRIES=('basic','codex','opencode','dsh-web','dsh-legacy','dsh-operator'),
        CHANNELS=('stable','prerelease')))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),ns)
    return ns['_parser']()

class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.common=['--root','目录/new root','--release','v0.1.0','--platform','observed platform.json']
    def test_complete_recipes_unchanged_into_actual_product_parser(self):
        seen=[]
        def update(args):
            seen.append(product_parser().parse_args(args));return 19
        code=bootstrap_entry.main(self.common+['--runtime-bindings','all runtimes.json','--components','four frontends.json',
             '--select','opencode','--extra','mcp','--extra','skills','--combination','precise-combination','--inspect-only','--json'],
             update_main=update,observe=lambda *_:self.fail('must not replace complete observations'))
        self.assertEqual(code,19);o=seen[0]
        self.assertEqual(o.command,'bootstrap');self.assertEqual(str(o.runtime_bindings),'all runtimes.json')
        self.assertEqual(str(o.components),'four frontends.json');self.assertEqual(o.select,'opencode')
        self.assertEqual(o.extra,['mcp','skills']);self.assertTrue(o.inspect_only and o.json)
        self.assertEqual(o.combination,'precise-combination')
    def test_basic_convenience_only_generates_python_observation(self):
        seen={}
        def update(args):
            o=product_parser().parse_args(args);seen['path']=o.runtime_bindings
            seen['data']=json.loads(o.runtime_bindings.read_text());self.assertIsNone(o.components);return 0
        self.assertEqual(bootstrap_entry.main(self.common+['--python','/persistent/python','--extra','mcp'],
            update_main=update,base_python=lambda x:Path(x),observe=lambda *_:SimpleNamespace(path=Path('/persistent/python'),version='3.12.0',implementation='cpython',architecture='x86_64')),0)
        self.assertEqual(list(seen['data']),['python']);self.assertFalse(seen['path'].exists())
    def test_complete_components_never_silently_use_python_only(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            bootstrap_entry.main(self.common+['--python','/p','--components','four.json'],update_main=lambda *_:self.fail('must not install'))
        self.assertEqual(e.exception.code,2)
    def test_both_runtime_sources_rejected_before_product(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            bootstrap_entry.main(self.common+['--python','/p','--runtime-bindings','all.json'],update_main=lambda *_:self.fail('must not install'))
    def test_original_failure_not_hidden_or_root_cleaned(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'managed';root.mkdir();marker=root/'pending';marker.write_text('original installer state')
            def update(args):raise RuntimeError('original transaction incomplete')
            with self.assertRaisesRegex(RuntimeError,'original transaction'):
                bootstrap_entry.main(['--root',str(root),'--release','v0.1.0','--platform','p','--runtime-bindings','r'],update_main=update)
            self.assertEqual(marker.read_text(),'original installer state')

class RegistrationWiringTest(unittest.TestCase):
    def options(self):return ['--root','a-root','--release','v0.1.0','--platform','p.json','--runtime-bindings','r.json','--bin-dir','user-bin']
    def test_registration_follows_success_once(self):
        events=[]
        def install(args):events.append('install');return 0
        def register(root,directory):events.append(('register',str(root),str(directory)));return {'status':'registered','command':'user-bin/rpnh'}
        with contextlib.redirect_stderr(io.StringIO()):self.assertEqual(bootstrap_entry.main(self.options(),update_main=install,register_command=register),0)
        self.assertEqual(events,['install',('register','a-root','user-bin')])
    def test_failed_install_never_registers(self):
        self.assertEqual(bootstrap_entry.main(self.options(),update_main=lambda args:2,register_command=lambda *_:self.fail('must not register')),2)
    def test_registration_failure_is_partial_success_not_rollback(self):
        def conflict(*_):raise FileExistsError('existing command retained')
        output=io.StringIO()
        with contextlib.redirect_stderr(output):self.assertEqual(bootstrap_entry.main(self.options(),update_main=lambda args:0,register_command=conflict),3)
        self.assertIn('installation succeeded',output.getvalue());self.assertIn('retained',output.getvalue())
    def test_registration_lease_failure_retains_success(self):
        def busy(*_):raise RuntimeError('another lifecycle operation holds the lock')
        output=io.StringIO()
        with contextlib.redirect_stderr(output):self.assertEqual(bootstrap_entry.main(self.options(),update_main=lambda args:0,register_command=busy),3)
        self.assertIn('installation succeeded',output.getvalue());self.assertIn('retained',output.getvalue())
    def test_inspection_cannot_register(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):bootstrap_entry.main(self.options()+['--inspect-only'],update_main=lambda *_:self.fail('no call'))


class LocalCandidateTest(unittest.TestCase):
    def setUp(self):
        self.common = ['--root', '目录/new root', '--candidate-wheel', '候选 wheel.whl',
                       '--materials', 'exact materials.json']

    def test_local_mapping_into_actual_product_parser(self):
        for inspect in (False, True):
            with self.subTest(inspect=inspect):
                seen = []
                def update(args):
                    seen.append(update_cli._parser().parse_args(args))
                    return 19
                options = self.common + ['--components', 'complete components.json', '--json']
                options += ['--inspect-only'] if inspect else ['--select', 'dsh']
                self.assertEqual(bootstrap_entry.main(
                    options, update_main=update,
                    observe=lambda *_: self.fail('materials own runtime'),
                    base_python=lambda *_: self.fail('materials own Python')), 19)
                o, = seen
                self.assertEqual(o.command, 'plan' if inspect else 'apply')
                self.assertEqual(str(o.root), '目录/new root')
                self.assertEqual(str(o.candidate_wheel), '候选 wheel.whl')
                self.assertEqual(str(o.materials), 'exact materials.json')
                self.assertEqual(str(o.components), 'complete components.json')
                self.assertTrue(o.json)
                self.assertFalse(hasattr(o, 'channel'))
                if inspect:
                    self.assertIsNone(o.runtime_bindings)
                else:
                    self.assertEqual(o.select, 'dsh')

    def test_local_and_published_options_never_mix(self):
        for option in (['--release', 'v0.1.0'], ['--python', '/p'],
                       ['--runtime-bindings', 'r.json'], ['--platform', 'p.json'],
                       ['--channel', 'stable'], ['--channel', 'prerelease'],
                       ['--extra', 'mcp'], ['--extra', 'skills'], ['--combination', 'c'],
                       ['--inspect-only', '--select', 'basic'],
                       ['--inspect-only', '--bin-dir', 'user-bin']):
            with self.subTest(option=option), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                bootstrap_entry.main(self.common + option,
                    update_main=lambda *_: self.fail('must reject before product'))
            self.assertEqual(error.exception.code, 2)

    def test_required_inputs_and_published_materials_rejected(self):
        cases = [
            ['--root', 'r', '--candidate-wheel', 'c.whl'],
            ['--root', 'r', '--materials', 'm.json'],
            ['--root', 'r', '--release', 'v0.1.0', '--platform', 'p', '--python', '/p',
             '--materials', 'm.json'],
            ['--root', 'r', '--release', 'v0.1.0', '--python', '/p', '--extra', 'mcp'],
            ['--root', 'r', '--release', 'v0.1.0', '--platform', 'p'],
        ]
        for options in cases:
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                bootstrap_entry.main(options, update_main=lambda *_: self.fail('invalid input'))
            self.assertEqual(error.exception.code, 2)

    def test_published_default_channel_still_stable(self):
        seen = []
        def update(args):
            seen.append(update_cli._parser().parse_args(args))
            return 0
        self.assertEqual(bootstrap_entry.main(
            ['--root', 'r', '--release', 'v0.1.0', '--platform', 'p', '--runtime-bindings', 'r'],
            update_main=update), 0)
        self.assertEqual(seen[0].channel, 'stable')

    def test_registration_only_after_successful_local_apply(self):
        for code in (0, 2, 130):
            with self.subTest(code=code):
                events = []
                def update(args):
                    self.assertEqual(update_cli._parser().parse_args(args).command, 'apply')
                    events.append('apply')
                    return code
                def register(root, directory):
                    events.append(('register', str(root), str(directory)))
                    return {'status': 'registered', 'command': 'user-bin/rpnh'}
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(bootstrap_entry.main(
                        self.common + ['--bin-dir', 'user-bin'], update_main=update,
                        register_command=register), code)
                self.assertEqual(events, ['apply', ('register', '目录/new root', 'user-bin')]
                                 if code == 0 else ['apply'])

    def test_successful_local_plan_does_not_register(self):
        def update(args):
            self.assertEqual(update_cli._parser().parse_args(args).command, 'plan')
            return 0
        self.assertEqual(bootstrap_entry.main(self.common + ['--inspect-only'],
            update_main=update, register_command=lambda *_: self.fail('plan cannot register')), 0)

    def test_local_registration_error_retains_installed_application(self):
        def register(*_):
            raise FileExistsError('existing command retained')
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(bootstrap_entry.main(self.common + ['--bin-dir', 'user-bin'],
                update_main=lambda _: 0, register_command=register), 3)
        self.assertIn('installation succeeded', output.getvalue())
        self.assertIn('retained', output.getvalue())

    def test_original_candidate_identity_gate_remains(self):
        for inspect in (False, True):
            output = io.StringIO()
            with self.subTest(inspect=inspect), patch.dict(os.environ, {'RPNH_CANDIDATE_ID': 'test-only'}), contextlib.redirect_stdout(output):
                self.assertEqual(bootstrap_entry.main(
                    self.common + ['--json'] + (['--inspect-only'] if inspect else []),
                    register_command=lambda *_: self.fail('identity gate failed')), 2)
            result = json.loads(output.getvalue())
            self.assertEqual(result['operation'], 'plan' if inspect else 'apply')
            self.assertIn('candidate installation context', result['error'])

    def test_original_missing_materials_and_payload_fail_without_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            root = directory / 'uncreated-root'
            wheel = directory / 'missing.whl'
            for inspect in (False, True):
                for material_exists in (False, True):
                    materials = directory / f'materials-{inspect}-{material_exists}.json'
                    if material_exists:
                        materials.write_text('{}')
                    output = io.StringIO()
                    with self.subTest(inspect=inspect, materials=material_exists), contextlib.redirect_stdout(output):
                        self.assertEqual(bootstrap_entry.main(
                            ['--root', str(root), '--candidate-wheel', str(wheel),
                             '--materials', str(materials), '--json']
                            + (['--inspect-only'] if inspect else []),
                            register_command=lambda *_: self.fail('must not register')), 2)
                    result = json.loads(output.getvalue())
                    self.assertEqual(result['operation'], 'plan' if inspect else 'apply')
                    self.assertIn('No such file', result['error'])
                    self.assertFalse(root.exists())


    def test_original_invalid_candidate_payload_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            root = directory / 'uncreated-root'
            materials = directory / 'materials.json'
            materials.write_text('{}')
            wheel = directory / 'invalid.whl'
            wheel.write_text('deliberately invalid wheel fixture')
            for inspect in (False, True):
                output = io.StringIO()
                with self.subTest(inspect=inspect), contextlib.redirect_stdout(output):
                    self.assertEqual(bootstrap_entry.main(
                        ['--root', str(root), '--candidate-wheel', str(wheel),
                         '--materials', str(materials), '--json']
                        + (['--inspect-only'] if inspect else []),
                        register_command=lambda *_: self.fail('must not register')), 2)
                self.assertIn('zip', json.loads(output.getvalue())['error'].lower())
                self.assertFalse(root.exists())

    def test_original_tty_and_update_confirmation_still_required(self):
        # Planning is stubbed only to reach the real CLI confirmation. Execution
        # must never run for non-TTY input or a response other than UPDATE.
        from cpn.rpnh import install_executor
        plan = SimpleNamespace(conflicts=(), rpnh_version='0.1.0rc3',
                               components={'basic': {'profile': 'basic-test'}})
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            root = directory / 'uncreated-root'
            materials = directory / 'materials.json'
            materials.write_text('{"root_extras": []}')
            options = ['--root', str(root), '--candidate-wheel', str(directory / 'c.whl'),
                       '--materials', str(materials), '--bin-dir', str(directory), '--json']
            for tty, answer in ((False, 'UPDATE'), (True, 'y'), (True, '')):
                output = io.StringIO()
                with (
                    self.subTest(tty=tty, answer=answer),
                    patch.object(install_executor, 'plan_local_install', return_value=plan),
                    patch.object(install_executor, 'execute_install') as execute,
                    patch('sys.stdin.isatty', return_value=tty),
                    contextlib.redirect_stdout(output),
                    patch.object(output, 'isatty', return_value=tty),
                    patch('builtins.input', return_value=answer),
                ):
                    self.assertEqual(bootstrap_entry.main(options,
                        register_command=lambda *_: self.fail('must not register')), 2)
                    execute.assert_not_called()
                self.assertIn('confirmation in a terminal' if not tty else 'update cancelled',
                              output.getvalue())
                self.assertFalse(root.exists())

if __name__ == '__main__':unittest.main()
