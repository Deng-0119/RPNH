"""Exercise the installed first-run CLI in a real PTY, without a model call."""
from __future__ import annotations

import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import tempfile
import time


def main() -> None:
    binary = shutil.which("rpnh")
    if binary is None:
        raise RuntimeError("install RPNH before running this check")
    with tempfile.TemporaryDirectory(prefix="rpnh-onboarding-smoke-") as directory:
        root = Path(directory)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("RPNH_")}
        environment.update(RPNH_CONFIG=str(root / "user/config.json"),
                           PYTHONUNBUFFERED="1", TERM="xterm")
        master, slave = pty.openpty()
        process = subprocess.Popen([binary, "init"], stdin=slave, stdout=slave,
                                   stderr=slave, env=environment, cwd=root)
        os.close(slave)
        transcript = bytearray()
        try:
            # No authentication: the reserved .invalid endpoint cannot be used
            # accidentally as a real provider. Setup is configuration-only.
            entries = ["1", "Offline demo", "offline-demo-model", "demo",
                       "https://api.example.invalid/v1/chat/completions", "3", "n", "y"]
            os.write(master, ("\n".join(entries) + "\n").encode())
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                readable, _, _ = select.select([master], [], [], 0.1)
                if readable:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    transcript.extend(chunk)
                elif process.poll() is not None:
                    break
            if process.wait(timeout=5) != 0:
                raise AssertionError(transcript.decode(errors="replace"))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            os.close(master)
        def run(*arguments):
            result = subprocess.run([binary, *arguments], cwd=root, env=environment,
                                    capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                raise AssertionError(result.stderr + result.stdout)
            return json.loads(result.stdout)
        profile = run("config", "show")
        report = run("doctor", "--json")
        assert profile["profile"] == "demo"
        assert profile["model_condition"] == "offline-demo-model"
        assert report["ready"] is True and report["network_checked"] is False
        assert not (root / ".rpnh").exists()
        print(json.dumps({"installed_cli": True, "pty_setup": "passed",
                          "config_show": "passed", "doctor": "passed",
                          "model_requests": 0, "task_created": False}))


if __name__ == "__main__":
    main()
