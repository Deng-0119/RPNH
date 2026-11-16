"""Explicit managed-v1 side-by-side updates; no in-place/package-manager takeover.

The lifecycle lock protects environments, not Registry facts or permissions.
Never unlock a descriptor shared with a detached worker: close our copy only.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
from uuid import uuid4

from cpn.update_context import (
    MANAGED_TARGET_RE, managed_context_hint, reject_candidate_runtime,
)
from .updates import Offer, discover

PROTOCOL = "managed-side-by-side-v1"
TARGET_RE = MANAGED_TARGET_RE
ENTRY_FRONTEND = {"basic": "basic", "opencode": "opencode", "dsh-web": "dsh"}


def _check_root(root: Path) -> Path:
    root = root.expanduser().absolute()
    if root != root.resolve() or not root.is_dir():
        raise ValueError("managed root must be a real directory, not a symlink")
    info = root.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("managed root must be private and owned by this user")
    marker = root / "managed.json"
    if marker.is_symlink() or marker.stat().st_size > 1024:
        raise ValueError("invalid managed installation marker")
    if json.loads(marker.read_text(encoding="utf-8")) != {"protocol": PROTOCOL}:
        raise ValueError("unsupported managed installation protocol")
    for name in ("versions", "bin"):
        child = root / name
        info = child.stat()
        if child.is_symlink() or not child.is_dir() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("managed layout contains unsafe directories")
    return root


def _target(root: Path, name: str = "current") -> tuple[str, Path]:
    target = os.readlink(root / name)
    if TARGET_RE.fullmatch(target) is None:
        raise ValueError("invalid managed environment pointer")
    directory = root / target
    if directory != directory.resolve() or not directory.is_dir():
        raise ValueError("managed environment escaped its installation")
    record_path = directory / "installed.json"
    if record_path.is_symlink() or record_path.stat().st_size > 4096:
        raise ValueError("invalid installed environment record")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if (record.get("protocol") != PROTOCOL or record.get("complete") is not True
            or record.get("entry") not in ENTRY_FRONTEND
            or not target.startswith("versions/" + str(record.get("version")) + "-")):
        raise ValueError("environment has not passed installation verification")
    for name in ("python", "rpnh"):
        executable = directory / "venv/bin" / name
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("managed environment is missing an executable")
    return target, directory


def _marker_document(root: Path) -> object:
    """Read a bounded dispatch hint; protocol-specific validators authorize it."""
    path = root.expanduser().absolute() / "managed.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise ValueError("invalid managed installation marker")
    return json.loads(path.read_text(encoding="utf-8"))


def _v2_root(root: Path) -> bool:
    value = _marker_document(root)
    return isinstance(value, dict) and value.get("protocol") == "managed-side-by-side-v2"


def _runtime_check_root(root: Path) -> Path:
    if _v2_root(root):
        from .install_v2 import check_root
        return check_root(root)
    return _check_root(root)


def _recover_runtime_pending() -> None:
    """Finish committed metadata before SH, never upgrade a runtime lease."""
    reject_candidate_runtime()
    candidate = managed_context_hint()
    if candidate is not None and _v2_root(candidate):
        from .install_contract import read_software_record, validate_managed
        from .install_v2 import check_root, recover
        root = check_root(candidate)
        marker = validate_managed(read_software_record(root / "managed.json", limit=16384))
        if marker["pending_activation"] is not None:
            recover(root)


def managed_root() -> Path | None:
    reject_candidate_runtime()
    candidate = managed_context_hint()
    if candidate is None:
        return None
    root = _runtime_check_root(candidate)
    if not os.environ.get("RPNH_MANAGED_ROOT"):
        raise ValueError("managed interpreter requires its fixed launcher context")
    if _v2_root(root):
        import cpn
        from .install_contract import read_frontend_view, read_software_record, validate_managed
        from .install_v2 import read_target
        marker = validate_managed(read_software_record(root / "managed.json", limit=16384))
        if marker["pending_activation"] is not None:
            raise ValueError("managed installation has a pending activation; recover it before runtime entry")
        target = read_target(root)
        if target is None:
            raise ValueError("managed installation has no current environment")
        _, directory, record = target
        venv = directory / "venv"
        if not venv.is_dir() or venv != venv.resolve():
            raise ValueError("managed environment must contain its own real venv directory")
        config = directory / "frontend.json"
        if config.is_symlink() or not config.is_file() or config.stat().st_size > 16384:
            raise ValueError("managed frontend binding is unavailable")
        read_frontend_view(directory, expected_id=record["installation_id"],
                           selected_entries=set(record["components"]))
        if (record["rpnh_version"] != cpn.__version__
                or not Path(cpn.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())):
            raise ValueError("this package does not match the managed environment version")
    else:
        _, directory = _target(root)
    if (not Path(__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
            or Path(sys.prefix).resolve() != (directory / "venv").resolve()
            or Path(os.environ.get("RPNH_FRONTEND_CONFIG", "")).absolute()
            != directory / "frontend.json"):
        raise ValueError("this interpreter was not launched from the managed environment")
    return root


def _open_lease(root: Path, exclusive: bool):
    _runtime_check_root(root)
    fd = os.open(root / "lifecycle.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        # Descriptors 0..2 may be closed in daemon parents; pass_fds must not
        # lose this lease when Popen installs the child's standard streams.
        safe = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 3)
    except BlockingIOError:
        raise RuntimeError("Managed RPNH is active or another update is running. "
                           "Checkpoint-stop tasks and exit all managed sessions before updating.") from None
    finally:
        os.close(fd)
    return os.fdopen(safe, "a+b", closefd=True)


@contextmanager
def lifecycle_lease(root: Path | None = None, *, exclusive: bool = False):
    reject_candidate_runtime()
    if root is None:
        _recover_runtime_pending()
    selected = managed_root() if root is None else root
    lease = None if selected is None else _open_lease(selected, exclusive)
    try:
        # The launcher may have read current just before an updater took EX.
        # Recheck the interpreter and fixed config while SH prevents another
        # switch. Explicit-root management deliberately doesn't bind itself to
        # current: it may be preparing or recovering an installation.
        if root is None and selected is not None and managed_root() != selected:
            raise ValueError("managed installation changed before the runtime lease was acquired")
        yield lease
    finally:
        if lease is not None:
            lease.close()  # NOT LOCK_UN: a child may hold the same description.


def worker_lease():
    """A distinct shared open-file-description, acquired before worker spawn."""
    _recover_runtime_pending()
    root = managed_root()
    if root is None:
        return None
    lease = _open_lease(root, False)
    try:
        if managed_root() != root:
            raise ValueError("managed installation changed before the worker lease was acquired")
        return lease
    except BaseException:
        lease.close()
        raise


def _run(argv: list[str], *, env: dict[str, str], capture: bool = False,
         timeout: float = 1800) -> str:
    try:
        process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    except OSError as exc:
        raise RuntimeError("Managed update command could not start; old environment retained") from exc
    try:
        output, _ = process.communicate(timeout=timeout)
    except BaseException as exc:
        cleanup_error = None
        if process.returncode is None:
            try:
                from .install_executor import _stop_owned_process
                _stop_owned_process(process, own_group=True)
            except BaseException as failure:
                cleanup_error = failure
        # communicate may already have reaped its child before raising (in
        # particular on KeyboardInterrupt). Never signal after that boundary
        # or wait indefinitely for pipes possibly held by other processes.
        output_stream = getattr(process, "stdout", None)
        close_error = None
        if output_stream is not None:
            try:
                output_stream.close()
            except BaseException as failure:
                close_error = failure
        if isinstance(exc, subprocess.TimeoutExpired):
            message = "Managed update command timed out; old environment retained"
            if cleanup_error is not None:
                message += ("; interrupted child cleanup is unconfirmed "
                            f"({type(cleanup_error).__name__}: {cleanup_error})")
            if close_error is not None:
                message += ("; output close is unconfirmed "
                            f"({type(close_error).__name__}: {close_error})")
            error = RuntimeError(message)
            if cleanup_error is not None:
                error.cleanup_error = cleanup_error
            if close_error is not None:
                error.stream_close_error = close_error
            raise error from exc
        if cleanup_error is not None:
            exc.add_note("Interrupted update cleanup is unconfirmed: "
                         f"{type(cleanup_error).__name__}: {cleanup_error}")
            exc.cleanup_error = cleanup_error
        if close_error is not None:
            exc.add_note("Interrupted update output close is unconfirmed: "
                         f"{type(close_error).__name__}: {close_error}")
            exc.stream_close_error = close_error
        raise
    if process.returncode:
        raise RuntimeError("Managed update command failed; old environment retained")
    output = output or ""
    if len(output) > 16384:
        raise RuntimeError("Unexpected managed verification output")
    return output


def _environment(directory: Path) -> dict[str, str]:
    # Provider/model secrets, Python/node injection options and package-manager
    # settings are not inherited. Download proxy routing is preserved, not logged.
    env = {key: os.environ[key] for key in (
        "PATH", "LANG", "LC_ALL", "LC_CTYPE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "no_proxy") if key in os.environ}
    env.update({"HOME": str(directory / "home"),
                "XDG_CONFIG_HOME": str(directory / "home/.config"),
                "XDG_CACHE_HOME": str(directory / "home/.cache"),
                "RPNH_FRONTEND_CONFIG": str(directory / "frontend.json"),
                "RPNH_UPDATES": "off", "PIP_CONFIG_FILE": os.devnull})
    (directory / "home").mkdir(mode=0o700)
    return env


def _launcher(root: Path) -> str:
    # This intentionally tiny v1 dispatcher does not contain updater logic.
    # The manager lives in the selected Python package and upgrades with it.
    return ("#!/bin/sh\nset -eu\nROOT=" + shlex.quote(str(root)) + "\n"
            "TARGET=$(/usr/bin/readlink \"$ROOT/current\")\n"
            "case \"$TARGET\" in versions/*) NAME=${TARGET#versions/};; *) exit 78;; esac\n"
            "case \"$NAME\" in ''|*[!0-9a-z.-]*|*..*|.*) exit 78;; esac\n"
            "[ ! -L \"$ROOT/versions\" ] || exit 78\n"
            "[ ! -L \"$ROOT/$TARGET\" ] || exit 78\n"
            "export RPNH_MANAGED_ROOT=\"$ROOT\"\n"
            "export RPNH_FRONTEND_CONFIG=\"$ROOT/$TARGET/frontend.json\"\n"
            "exec \"$ROOT/$TARGET/venv/bin/rpnh\" \"$@\"\n")


def _point(root: Path, name: str, target: str) -> None:
    if name not in ("current", "previous") or TARGET_RE.fullmatch(target) is None:
        raise ValueError("invalid managed pointer")
    temporary = root / ("." + name + "-" + uuid4().hex)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, root / name)
    finally:
        temporary.unlink(missing_ok=True)


def install_managed(offer: Offer, *, root: Path, bootstrap: bool = False,
                    fetch=None, runner=None) -> Path:
    if offer.entry not in ENTRY_FRONTEND:
        raise ValueError("this entry requires its own manual package/source procedure")
    # Never install from stale cached support or from a caller-fabricated Offer.
    kwargs = {} if fetch is None else {"fetch": fetch}
    fresh = discover(channel=offer.channel, installed=None, **kwargs)
    if offer not in fresh:
        raise ValueError("release is not currently published, supported and verified")
    root = root.expanduser().absolute()
    if root != root.resolve():
        raise ValueError("managed installation cannot use a symlink path")
    if bootstrap:
        if root.exists():
            raise ValueError("choose a new nonexistent directory; existing installations are not adopted")
        root.mkdir(parents=True, mode=0o700)
        (root / "managed.json").write_text(json.dumps({"protocol": PROTOCOL}) + "\n")
        (root / "versions").mkdir(mode=0o700)
        (root / "bin").mkdir(mode=0o700)
        launcher = root / "bin/rpnh"
        launcher.write_text(_launcher(root), encoding="utf-8")
        launcher.chmod(0o700)
    else:
        _check_root(root)
        _target(root)
    run = _run if runner is None else runner
    with lifecycle_lease(root, exclusive=True):
        previous = None if bootstrap else _target(root)[0]
        target = f"versions/{offer.rpnh_version}-{uuid4().hex[:12]}"
        directory = root / target
        directory.mkdir(mode=0o700)
        try:
            env = _environment(directory)
            python = directory / "venv/bin/python"
            run([sys.executable, "-I", "-m", "venv", str(directory / "venv")], env=env)
            run([str(python), "-I", "-m", "pip", "install", "--isolated",
                 "--disable-pip-version-check", "--no-input", "--index-url",
                 "https://pypi.org/simple", offer.wheel_url], env=env)
            frontend = ENTRY_FRONTEND[offer.entry]
            run([str(python), "-I", "-m", "cpn.rpnh_cli", "frontend", "setup", frontend], env=env)
            run([str(python), "-I", "-m", "cpn.rpnh_cli", "--help"], env=env)
            # Fixed code + locally allowlisted entry; not a remote command field.
            pin_script = "upstream={'version':'builtin','revision':None};"
            if offer.entry == "opencode":
                pin_script = ("from cpn.frontend.opencode_protocol import OPENCODE_VERSION,OPENCODE_COMMIT;"
                              "upstream={'version':OPENCODE_VERSION,'revision':OPENCODE_COMMIT};")
            elif offer.entry == "dsh-web":
                pin_script = ("import importlib.resources;"
                              "pin=json.loads(importlib.resources.files('integrations.dsh').joinpath('UPSTREAM.json').read_text());"
                              "upstream={'version':pin['version'],'revision':pin['revision']};")
            script = (
                "import json,sys,os,importlib.metadata as m;from pathlib import Path;import cpn;"
                "from cpn.rpnh.frontend_config import preferences_path;"
                "from cpn.rpnh.frontend_setup import frontend_status;"
                "assert Path(cpn.__file__).resolve().is_relative_to(Path(sys.prefix).resolve());"
                "assert os.access(Path(sys.prefix)/'bin/rpnh',os.X_OK);"
                + pin_script +
                "print(json.dumps({'version':m.version('rpnh-harness'),"
                "'preferences':str(preferences_path()),'upstream':upstream,"
                f"'available':frontend_status({frontend!r})['available']}}))")
            result = json.loads(run([str(python), "-I", "-c", script], env=env, capture=True))
            if result != {"version": offer.rpnh_version,
                          "preferences": str(directory / "frontend.json"), "available": True,
                          "upstream": {"version": offer.upstream_version,
                                       "revision": offer.upstream_revision}}:
                raise RuntimeError("new environment verification failed")
            (directory / "installed.json").write_text(json.dumps({
                "protocol": PROTOCOL, "complete": True, "entry": offer.entry,
                "version": offer.rpnh_version, "release": offer.tag,
                "channel": offer.channel, "data_policy": "no-migration"}) + "\n")
            # Check the record and containment through the same parser before
            # publishing it; no Registry/database is opened by this procedure.
            if previous is not None:
                _point(root, "previous", previous)
            _point(root, "current", target)
        except BaseException:
            # Retain failed isolated bytes without turning them into a launch
            # target. No recursive cleanup of source checkouts or user data.
            (directory / "FAILED").write_text("Installation did not activate. Old environment retained.\n")
            raise
    return root / "bin/rpnh"


def rollback(root: Path) -> Path:
    root = _check_root(root)
    with lifecycle_lease(root, exclusive=True):
        _target(root)
        previous, _ = _target(root, "previous")
        # One commit point; repeated rollback is deliberately idempotent.
        _point(root, "current", previous)
    return root / "bin/rpnh"
