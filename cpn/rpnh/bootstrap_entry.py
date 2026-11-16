"""Thin bootstrap entry delegating to the original managed installer."""
import argparse
import json
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

def main(argv=None, *, observe=None, update_main=None, base_python=None, register_command=None):
    p = argparse.ArgumentParser(description=__doc__)
    runtime_choice = p.add_mutually_exclusive_group()
    runtime_choice.add_argument("--python", help="explicit persistent base Python; Basic convenience path")
    runtime_choice.add_argument("--runtime-bindings", type=Path, help="complete observed runtime bindings for the original installer")
    p.add_argument("--components", type=Path)
    p.add_argument("--select", choices=("auto", "basic", "codex", "opencode", "dsh", "dsh-web"))
    p.add_argument("--extra", action="append", default=[], choices=("mcp", "skills"))
    p.add_argument("--platform", type=Path, help="actual observed platform JSON; native status is not guessed")
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--bin-dir", type=Path, help="explicit existing user command directory; never changes PATH or replaces a command")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--release", help="exact published release")
    source.add_argument("--candidate-wheel", type=Path, help="explicit local candidate wheel")
    p.add_argument("--materials", type=Path, help="fixed local candidate materials")
    p.add_argument("--channel", choices=("stable", "prerelease"))
    p.add_argument("--combination")
    p.add_argument("--inspect-only", action="store_true")
    p.add_argument("--json", action="store_true")
    o = p.parse_args(argv)
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
    else:
        if o.materials is not None:
            p.error("--materials requires --candidate-wheel")
        if o.platform is None or (o.python is None and o.runtime_bindings is None):
            p.error("--release requires --platform and either --python or --runtime-bindings")
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
        if code != 0 or o.bin_dir is None:
            return code
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
        return forward(bindings)

if __name__ == "__main__":
    raise SystemExit(main())
