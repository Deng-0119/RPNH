from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch
from cpn.rpnh import bootstrap_entry as entry

class BootstrapHandoffTests(TestCase):
    def invoke(self, root, args=("--first-run",), *, tty=True, update_code=0, configure=0):
        self.calls=[]
        def original(values):
            self.calls.append(("original", values))
            return update_code
        def setup(root_arg):
            self.calls.append(("init", root_arg))
            if isinstance(configure, BaseException):
                raise configure
            return configure
        with patch.object(entry.sys, "stdin", SimpleNamespace(isatty=lambda: tty)), \
             patch.object(entry.sys, "stdout", SimpleNamespace(isatty=lambda: tty)):
            return entry.main(["--root", str(root), "--release", "v0.1.0rc8",
                "--channel", "prerelease", "--python", "/usr/bin/python3", *args],
                update_main=original, base_python=Path,
                observe=lambda *_: SimpleNamespace(path=Path("/usr/bin/python3"),
                    version="3.13.12", implementation="cpython", architecture="x86_64"),
                platform_observe=lambda: dict(platform="linux", machine="x86_64",
                    os_release="WSL2", libc="glibc"),
                setup=setup)

    def test_delegates_activate_then_init(self):
        with TemporaryDirectory() as d:
            root=Path(d)/"new"
            self.assertEqual(self.invoke(root),0)
            self.assertEqual([k for k,_ in self.calls],["original","init"])
            self.assertEqual(self.calls[0][1][0],"bootstrap")
            self.assertIn("basic",self.calls[0][1])
            self.assertEqual(self.calls[1][1],root)

    def test_failed_or_declined_install_never_calls_init(self):
        with TemporaryDirectory() as d:
            self.assertEqual(self.invoke(Path(d)/"new",update_code=2),2)
            self.assertEqual([k for k,_ in self.calls],["original"])

    def test_setup_interrupt_leaves_result_distinct(self):
        with TemporaryDirectory() as d:
            self.assertEqual(self.invoke(Path(d)/"new",configure=KeyboardInterrupt()),130)
            self.assertEqual([k for k,_ in self.calls],["original","init"])

    def test_bad_dispatcher_does_not_repeat_install(self):
        with TemporaryDirectory() as d:
            self.assertEqual(self.invoke(Path(d)/"new",configure=ValueError("bad dispatcher")),4)
            self.assertEqual([k for k,_ in self.calls],["original","init"])

    def test_existing_directory_and_dangling_symlink_never_bootstrap(self):
        with TemporaryDirectory() as d:
            root=Path(d)/"new"
            root.mkdir()
            with self.assertRaisesRegex(SystemExit,"2"):
                self.invoke(root)
            self.assertEqual(self.calls,[])
            root.rmdir()
            root.symlink_to(Path(d)/"absent")
            with self.assertRaisesRegex(SystemExit,"2"):
                self.invoke(root)
            self.assertEqual(self.calls,[])

    def test_symlink_parent_with_absent_leaf_never_bootstraps(self):
        with TemporaryDirectory() as d:
            base=Path(d).resolve()
            real=base/"real"
            real.mkdir()
            link=base/"linked"
            link.symlink_to(real,target_is_directory=True)
            with self.assertRaisesRegex(SystemExit,"2"):
                self.invoke(link/"offered-root")
            self.assertEqual(self.calls,[])

    def test_non_tty_or_inspect_only_never_bootstrap(self):
        with TemporaryDirectory() as d:
            root=Path(d)/"new"
            with self.assertRaisesRegex(SystemExit,"2"):
                self.invoke(root,tty=False)
            self.assertEqual(self.calls,[])
            with self.assertRaisesRegex(SystemExit,"2"):
                self.invoke(root,args=("--first-run","--inspect-only"))
            self.assertEqual(self.calls,[])

    def test_existing_legacy_call_unchanged(self):
        with TemporaryDirectory() as d:
            self.assertEqual(self.invoke(Path(d)/"new",args=()),0)
            self.assertEqual([k for k,_ in self.calls],["original"])
            self.assertNotIn("--select",self.calls[0][1])

class DispatcherHandoffTests(TestCase):
    def test_exact_dispatcher_invokes_init_after_releasing_lease(self):
        from contextlib import contextmanager
        from cpn.rpnh import command_registration
        events=[]
        @contextmanager
        def validate(root):
            events.append(("enter",root))
            try:
                yield
            finally:
                events.append(("exited",root))
        with TemporaryDirectory() as d:
            root=Path(d).resolve()
            target=root/"bin"/"rpnh"
            target.parent.mkdir()
            target.write_text("fixture executable only")
            target.chmod(0o755)
            def local_run(argv, *, check):
                self.assertEqual(events,[("enter",root),("exited",root)])
                self.assertEqual(argv,[str(target),"init"])
                self.assertFalse(check)
                return SimpleNamespace(returncode=-2)
            with patch.object(command_registration,"original_installation",validate):
                self.assertEqual(entry.first_run_setup(root,run=local_run),130)

    def test_untrusted_symlink_dispatcher_never_spawns(self):
        from contextlib import nullcontext
        from cpn.rpnh import command_registration
        with TemporaryDirectory() as d:
            root=Path(d).resolve()
            target=root/"bin"/"rpnh"
            target.parent.mkdir()
            file=root/"user-script"
            file.write_text("not the managed dispatcher")
            file.chmod(0o755)
            target.symlink_to(file)
            with patch.object(command_registration,"original_installation",lambda _:nullcontext()):
                with self.assertRaisesRegex(ValueError,"not an executable regular file"):
                    entry.first_run_setup(root,run=lambda *_a,**_k:self.fail("spawned an unsafe target"))


if __name__=="__main__":
    main()
