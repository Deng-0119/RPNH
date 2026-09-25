"""Build/install the FULL checkout outside its source tree, without network/API calls.

Run: python tools/check_opencode_wheel.py --work-dir /outside/repository/work
The directory must not exist. Existing dependencies must already be installed;
this tool deliberately uses --no-index/--no-deps and never installs OpenCode.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    if not (source / "cpn/rpnh/main_session.py").is_file():
        parser.error("A complete harness checkout is required; a changes-only bundle is not a wheel input")
    work = args.work_dir.expanduser().resolve()
    if work.is_relative_to(source) or work.exists():
        parser.error("Use an absent work directory outside the source checkout")
    work.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT"}}
    env.update(PIP_NO_INDEX="1", PYTHONNOUSERSITE="1")
    evidence = {"real_provider_calls": 0, "steps": [], "result": "failed"}

    def run(argv, cwd):
        result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=120, check=False)
        evidence["steps"].append({"command": [str(x).replace(str(work), "<WORK>").replace(str(source), "<SOURCE>") for x in argv],
                                  "returncode": result.returncode,
                                  "stdout": result.stdout.replace(str(work), "<WORK>").replace(str(source), "<SOURCE>"),
                                  "stderr": result.stderr.replace(str(work), "<WORK>").replace(str(source), "<SOURCE>")})
        if result.returncode:
            raise RuntimeError("Offline wheel verification command failed")
        return result

    try:
        run([sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation",
             "--wheel-dir", str(work / "dist"), str(source)], work)
        wheels = list((work / "dist").glob("rpnh_harness-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("Expected exactly one harness wheel")
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(work / "venv")
        python = str(work / "venv/bin/python")
        run([python, "-m", "pip", "install", "--no-index", "--no-deps", "--force-reinstall", str(wheels[0])], work)
        probe = ("from pathlib import Path; import cpn, importlib.resources, sys; "
                 "p=Path(cpn.__file__).resolve(); assert p.is_relative_to(Path(sys.prefix).resolve()), p; "
                 "assert importlib.resources.files('cpn.frontend').joinpath('opencode_compatibility.v1.json').is_file(); "
                 "print('cpn origin:', p)")
        run([python, "-c", probe], work)
        result = run([str(work / "venv/bin/rpnh"), "--help"], work)
        if "opencode" not in result.stdout:
            raise RuntimeError("Installed rpnh help does not advertise OpenCode")
        run([str(work / "venv/bin/rpnh"), "config", "--help"], work)
        evidence["result"] = "passed"
    finally:
        (work / "wheel-verification.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
