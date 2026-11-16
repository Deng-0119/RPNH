"""Read-only interpreter and POSIX API checks for Linux and macOS.

This module is deliberately standard-library-only and can be executed by file
path before installing RPNH dependencies. API availability is a prerequisite,
not evidence that a platform or frontend has passed native acceptance.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys

try:
    import fcntl
except ImportError:  # Keep unsupported systems diagnosable.
    fcntl = None

try:
    import resource
except ImportError:
    resource = None


def _has_callables(module, *names: str) -> bool:
    return module is not None and all(callable(getattr(module, name, None)) for name in names)


def _has_constants(module, *names: str) -> bool:
    return module is not None and all(isinstance(getattr(module, name, None), int) for name in names)


def _implementation_scope(target: str) -> dict:
    """Describe existing code paths without probing or authorizing execution.

    Mirrors local_process._run_bounded, plugins.worker and the Linux-only
    process_supervision/controlled_script implementations. Darwin's libproc
    birth-identity reader does not supply an invocation-owned supervisor.
    """
    backend = {
        "linux": "invocation_owned",
        "darwin": "legacy_process_group",
    }.get(target, "unsupported_platform")
    return {
        "basis": "source_code_paths",
        "invocation_owned_supervision": "linux_only",
        "local_process_supervision": backend,
        "plugin_worker_supervision": backend,
        "controlled_script": {
            "profile": "linux_isolated_python/v1",
            "platform_scope": "linux_only",
            "implementation_on_current_platform": (
                "present" if target == "linux" else "not_implemented"),
        },
    }


def platform_report() -> dict:
    """Inspect the running interpreter without creating files or processes.

    ``eligible`` means only that the minimum interpreter and API prerequisites
    are present. Runtime operation, installed frontend binaries, architecture
    compatibility and the required native acceptance matrix are separate.
    ``implementation_scope`` describes source paths only; it does not change
    these prerequisite checks or claim that any runtime facility works.
    """
    target = "linux" if sys.platform.startswith("linux") else sys.platform
    minimum = {"linux": (3, 11), "darwin": (3, 13)}.get(target)
    version = tuple(sys.version_info[:3])
    checks = {
        "supported_platform": minimum is not None,
        "python_minimum": minimum is not None and version[:2] >= minimum,
        "unix_socket_api": _has_constants(socket, "AF_UNIX", "SOCK_STREAM"),
        "waitid_api": (_has_callables(os, "waitid") and _has_constants(
            os, "P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "CLD_EXITED")),
        "file_lock_api": (_has_callables(fcntl, "flock", "fcntl") and
                          _has_constants(fcntl, "LOCK_EX", "LOCK_SH", "LOCK_NB", "F_DUPFD_CLOEXEC")),
        "process_group_api": _has_callables(os, "getpgid", "killpg", "setsid"),
        "posix_file_api": (_has_callables(os, "getuid", "fstat", "fsync", "replace") and
                           _has_constants(os, "O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW")),
    }
    errors = []
    if minimum is None:
        errors.append("RPNH requires Linux or macOS; native Windows support is not available.")
    elif not checks["python_minimum"]:
        errors.append(f"RPNH on {target} requires Python {minimum[0]}.{minimum[1]} or newer.")
    descriptions = {
        "unix_socket_api": "AF_UNIX stream sockets",
        "waitid_api": "waitid with P_PID, WEXITED, WNOHANG, WNOWAIT and CLD_EXITED",
        "file_lock_api": "POSIX flock and close-on-exec descriptor duplication",
        "process_group_api": "POSIX process groups",
        "posix_file_api": "POSIX owner, directory and atomic-file operations",
    }
    for name, description in descriptions.items():
        if not checks[name]:
            errors.append(f"The running Python is missing required API support: {description}.")
    libc = platform.libc_ver() if target == "linux" else ("", "")
    return {
        "schema_version": "rpnh/platform_preflight/v1",
        "platform": target,
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python_version": ".".join(map(str, version)),
        "python_implementation": sys.implementation.name,
        "minimum_python": None if minimum is None else ".".join(map(str, minimum)),
        "libc": {"family": libc[0], "version": libc[1]} if libc[0] else None,
        "required_checks": checks,
        "optional_checks": {
            "address_space_limit_api": (_has_callables(resource, "setrlimit") and
                                        _has_constants(resource, "RLIMIT_AS")),
            "process_limit_api": (_has_callables(resource, "setrlimit") and
                                  _has_constants(resource, "RLIMIT_NPROC")),
        },
        "eligible": all(checks.values()),
        "support_level": "candidate" if target == "darwin" else (
            "existing_linux_path" if target == "linux" else "unavailable"),
        "native_validation": "not_performed",
        "implementation_scope": _implementation_scope(target),
        "errors": errors,
    }


def require_platform_support() -> dict:
    """Return prerequisite observations or fail before preparation/launch."""
    report = platform_report()
    if not report["eligible"]:
        raise RuntimeError(" ".join(report["errors"]))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print structured prerequisite observations")
    options = parser.parse_args(argv)
    report = platform_report()
    if options.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"RPNH platform preflight: {report['platform']} / Python {report['python_version']}")
        if report["eligible"]:
            print("Required interpreter and API prerequisites are present.")
        for error in report["errors"]:
            print(error)
        if report["platform"] == "darwin":
            print("macOS remains a candidate pending native installation and runtime acceptance.")
            print("Local-process and plugin-worker supervision use legacy process-group cleanup; "
                  "a Darwin invocation-owned supervisor is not implemented.")
        elif report["platform"] == "linux":
            print("Local-process and plugin-worker supervision use the Linux invocation-owned backend.")
        print("The linux_isolated_python/v1 controlled-script profile has a Linux implementation only.")
        print("This preflight does not perform native runtime or frontend acceptance.")
    return 0 if report["eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("platform_report", "require_platform_support", "main")

