"""Native descriptor and cleanup boundaries for shared supervision."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from cpn.process_supervision import ProcessStartError, start_process, stop_process

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux supervision contract")


def test_exact_passed_descriptor_and_environment_reach_target(tmp_path):
    read, write = os.pipe()
    os.write(write, b"original-capability")
    os.close(write)
    try:
        child = start_process(
            [sys.executable, "-B", "-c",
             "import os,sys; assert os.environ[\x27VALUE\x27] == \x27fixture\x27; print(os.read(int(sys.argv[1]), 64).decode())",
             str(read)], env={"VALUE": "fixture"}, cwd=tmp_path,
            pass_fds=(read,), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            deadline=time.monotonic() + 5)
    finally:
        os.close(read)
    stdout, stderr = child.communicate(timeout=5)
    assert (child.returncode, stdout, stderr) == (0, b"original-capability\n", b"")
    assert child.cleanup_complete and not child.had_live_descendants


def test_normal_exit_reaps_leftover_descendant(tmp_path):
    marker = tmp_path / "descendant"
    script = """
import os,sys,time
from pathlib import Path
pid=os.fork()
if pid==0:
    Path(sys.argv[1]).write_text(str(os.getpid()))
    while True: time.sleep(0.05)
while not Path(sys.argv[1]).exists(): time.sleep(0.01)
print("complete",flush=True)
"""
    child = start_process([sys.executable, "-B", "-c", script, str(marker)],
                          env={}, cwd=tmp_path, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, deadline=time.monotonic()+5)
    stdout, stderr = child.communicate(timeout=5)
    assert (child.returncode, stdout, stderr) == (0, b"complete\n", b"")
    assert child.cleanup_complete and child.had_live_descendants
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)


def test_stopping_tree_confirms_cleanup(tmp_path):
    marker = tmp_path / "ready"
    child = start_process([sys.executable, "-B", "-c",
                           "import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpgrp())); time.sleep(30)",
                           str(marker)], env={}, cwd=tmp_path,
                           deadline=time.monotonic()+5)
    try:
        until = time.monotonic()+5
        while not marker.exists():
            assert time.monotonic()<until
            time.sleep(0.01)
        assert int(marker.read_text()) == os.getpgrp()
        stop_process(child)
        assert child.returncode == -signal.SIGKILL
        assert child.cleanup_complete and not child.had_live_descendants
    finally:
        if child.poll() is None:
            stop_process(child)


def test_invalid_passed_descriptor_cannot_be_reused_for_internal_channel(tmp_path):
    read, write = os.pipe()
    os.close(read); os.close(write)
    with pytest.raises(OSError):
        start_process([sys.executable, "-B", "-c", "raise AssertionError(\x27must not start\x27)"],
                      env={}, cwd=tmp_path, pass_fds=(write,), deadline=time.monotonic()+5)
