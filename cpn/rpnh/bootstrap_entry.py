"""Thin bootstrap entry delegating to the original managed installer."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

def require_base_python(executable, run=subprocess.run):
    path = Path(executable).expanduser().absolute()
    probe = run([str(path), "-I", "-c",
        "import json,sys;print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix}))"],
        check=True, capture_output=True, text=True, timeout=15)
    value = json.loads(probe.stdout)
    if value["prefix"] != value["base_prefix"]:
        raise ValueError("Select a persistent base Python, not a controller/virtual environment Python")
    return path.resolve(strict=True)

def default_root():
    if not sys.platform.startswith("linux"):
        raise ValueError("Automatic installation defaults currently support Linux only")
    data = os.environ.get("XDG_DATA_HOME")
    base = Path(data).expanduser() if data and Path(data).is_absolute() else Path.home() / ".local/share"
    return base / "rpnh"


def first_run_setup(root, *, run=subprocess.run):
    """After activation, hand off to the installed CLI's existing setup."""
    from .command_registration import original_installation

    root = root.expanduser().absolute()
    target = root / "bin" / "rpnh"
    if root != root.resolve() or target.parent.is_symlink():
        raise ValueError("Select the real managed root and bin directory")
    with original_installation(root):
        if target.is_symlink() or not target.is_file() or not os.access(target, os.X_OK):
            raise ValueError("Original managed dispatcher is not an executable regular file")
    # Do not retain the short validation lease during interactive setup.
    # As for other CLI invocations, the dispatcher resolves current at launch.
    code = run([str(target), "init"], check=False).returncode
    return code if code >= 0 else 128 - code


def main(argv=None, *, observe=None, update_main=None, base_python=None, register_command=None,
         platform_observe=None, setup=None):
    p = argparse.ArgumentParser(description=__doc__)
    runtime_choice = p.add_mutually_exclusive_group()
    runtime_choice.add_argument("--python", help="explicit persistent base Python; Basic convenience path")
    runtime_choice.add_argument("--runtime-bindings", type=Path, help="complete observed runtime bindings for the original installer")
    p.add_argument("--components", type=Path)
    p.add_argument("--select", choices=("auto", "basic", "codex", "opencode", "dsh", "dsh-web"))
    p.add_argument("--extra", action="append", default=[], choices=("mcp", "skills"))
    p.add_argument("--platform", type=Path, help="actual observed platform JSON; native status is not guessed")
    p.add_argument("--root", type=Path, help="new application root; defaults to Linux user data directory")
    p.add_argument("--bin-dir", type=Path, help="explicit existing user command directory; never changes PATH or replaces a command")
    source = p.add_mutually_exclusive_group()
    source.add_argument("--release", help="exact published release")
    source.add_argument("--candidate-wheel", type=Path, help="explicit local candidate wheel")
    p.add_argument("--materials", type=Path, help="fixed local candidate materials")
    p.add_argument("--channel", choices=("stable", "prerelease"))
    p.add_argument("--combination")
    p.add_argument("--inspect-only", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--first-run", action="store_true",
                   help="after a fresh interactive Basic installation, start the built-in setup")
    o = p.parse_args(argv)
    if o.root is None:
        o.root = default_root()
    if o.first_run:
        if (o.inspect_only or o.json or o.candidate_wheel is not None or o.bin_dir is not None
                or o.runtime_bindings is not None or o.components is not None or o.extra
                or o.combination is not None or o.select not in (None, "basic")):
            p.error("--first-run is only for a fresh interactive Basic installation")
        if not sys.platform.startswith("linux") or not sys.stdin.isatty() or not sys.stdout.isatty():
            p.error("--first-run requires a Linux terminal; no installation was attempted")
        try:
            o.root = o.root.expanduser().absolute()
            if o.root != o.root.resolve():
                p.error("--first-run requires a real installation root without symbolic links")
            o.root.lstat()
        except FileNotFoundError:
            pass
        except (OSError, RuntimeError) as error:
            p.error(f"could not check installation root: {error}")
        else:
            p.error("installation root already exists; use the existing managed command")
        o.select = "basic"
    if o.release is None and o.candidate_wheel is None:
        from cpn import __version__
        o.release = "v" + __version__
        if o.channel is None:
            from packaging.version import Version
            o.channel = "prerelease" if Version(__version__).is_prerelease else "stable"
    # Complete recipes use the existing exact observations without a second
    # schema reader or guessed platform/runtime facts. Original installer owns
    # eligibility and consistency checks. Optional extras remain explicit.
    if o.candidate_wheel is not None:
        if o.materials is None:
            p.error("--candidate-wheel requires --materials")
        if (o.python is not None or o.runtime_bindings is not None or o.platform is not None
                or o.channel is not None or o.extra or o.combination is not None):
            p.error("--candidate-wheel cannot use published --python, --runtime-bindings, "
                    "--platform, --channel, --extra or --combination; materials own those facts")
        if o.inspect_only and o.select is not None:
            p.error("--select is not supported for local --inspect-only")
        if not o.inspect_only and os.path.lexists(o.root):
            p.error(f"installation root already exists: {o.root}; "
                    "use explicit update commands or a new root")
    else:
        if o.materials is not None:
            p.error("--materials requires --candidate-wheel")
        if o.python is None and o.runtime_bindings is None:
            p.error("Persistent Python is required; npm transport discovers one or use --python")
        if o.platform is None and (o.components is not None or o.extra or o.runtime_bindings is not None):
            p.error("Complete/non-Basic recipes require explicit --platform")
        if o.components is not None and o.runtime_bindings is None:
            p.error("--components requires --runtime-bindings for the complete selected recipes")
    if o.bin_dir is not None and o.inspect_only:
        p.error("--bin-dir is only available after installation, not --inspect-only")
    def forward(bindings):
        if o.candidate_wheel is not None:
            args = ["plan" if o.inspect_only else "apply", "--root", str(o.root),
                    "--candidate-wheel", str(o.candidate_wheel), "--materials", str(o.materials)]
        else:
            args = ["bootstrap", "--root", str(o.root), "--release", o.release,
                    "--channel", o.channel or "stable", "--platform", str(o.platform),
                    "--runtime-bindings", str(bindings)]
        if o.components is not None:
            args += ["--components", str(o.components)]
        if o.select is not None:
            args += ["--select", o.select]
        for extra in o.extra:
            args += ["--extra", extra]
        if o.combination is not None:
            args += ["--combination", o.combination]
        if o.inspect_only and o.candidate_wheel is None:
            args.append("--inspect-only")
        if o.json:
            args.append("--json")
        code = update_main(args)
        if code != 0:
            return code
        if o.first_run:
            try:
                result = (setup or first_run_setup)(o.root)
            except KeyboardInterrupt:
                result = 130
            except (OSError, ValueError, RuntimeError) as error:
                print("RPNH installation remains in place; could not start setup: " + str(error),
                      file=sys.stderr)
                return 4
            if result:
                print("RPNH installation remains in place; rerun init to finish setup.",
                      file=sys.stderr)
            return result
        if o.bin_dir is None:
            return 0
        registration = register_command
        if registration is None:
            from .command_registration import register as registration
        try:
            result = registration(o.root, o.bin_dir)
        except (OSError, ValueError, RuntimeError) as error:
            print("RPNH installation succeeded; command registration failed. "
                  "The installed application is retained: " + str(error), file=sys.stderr)
            return 3
        print("RPNH command " + result["status"] + ": " + result["command"] +
              "; PATH was not modified.", file=sys.stderr)
        return 0
    if update_main is None:
        from cpn.rpnh.update_cli import main as update_main
    if o.candidate_wheel is not None:
        return forward(None)
    if o.runtime_bindings is not None:
        return forward(o.runtime_bindings)
    if observe is None:
        from cpn.rpnh.frontend_launch import observe_runtime
        observe = observe_runtime
    python = (base_python or require_base_python)(o.python)
    runtime = observe("python", python)
    binding = {"python": {"path": str(runtime.path), "version": runtime.version,
        "implementation": runtime.implementation, "architecture": runtime.architecture}}
    # Temporary transport only. The original installer records the persistent
    # Python binding; neither this directory nor this process owns current.
    with tempfile.TemporaryDirectory(prefix="rpnh-entry-") as directory:
        bindings = Path(directory) / "runtime.json"
        bindings.write_text(json.dumps(binding), encoding="utf-8")
        if o.platform is None:
            if platform_observe is None:
                from .platform_support import require_platform_support
                platform_observe = require_platform_support
            host = platform_observe()
            if host["platform"] != "linux":
                raise ValueError("Automatic platform selection currently supports Linux only")
            o.platform = Path(directory) / "platform.json"
            o.platform.write_text(json.dumps({"os": host["platform"], "architecture": host["machine"],
                "os_release": host["os_release"], "libc": host["libc"], "emulated": None}), encoding="utf-8")
        return forward(bindings)

if __name__ == "__main__":
    raise SystemExit(main())
