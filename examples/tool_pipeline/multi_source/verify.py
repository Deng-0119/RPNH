"""Run the unchanged upstream assertions with two local artifact path mappings.

This offline launch is not the upstream container/test.sh or a dataset benchmark.
Only the collected upstream module's file access names are adapted; real pandas,
filesystem reads, and the original assertions execute unchanged.
"""
from __future__ import annotations

import argparse
import builtins
import hashlib
import json
import os
from pathlib import Path
import platform
from types import SimpleNamespace

UPSTREAM = Path(__file__).parent / "upstream"
CHECKER = "multi-source-data-merger/tests/test_outputs.py"
ARTIFACTS = ("merged_users.parquet", "conflicts.json")


class ArtifactPaths:
    def __init__(self, output_dir):
        self.mapping = {"/app/" + name: str(Path(output_dir).resolve() / name)
                        for name in ARTIFACTS}
        self.results = []
        self.adapted = False

    def translate(self, path):
        return self.mapping.get(os.fspath(path), path)

    def pytest_collection_modifyitems(self, items):
        import pandas as pd

        for item in items:
            if Path(item.path).resolve() != (UPSTREAM / CHECKER).resolve():
                raise ValueError("offline verifier only supports the pinned checker")
            module = item.module
            module.os = SimpleNamespace(path=SimpleNamespace(
                exists=lambda path: os.path.exists(self.translate(path))))
            module.pd = SimpleNamespace(read_parquet=lambda path, *a, **kw:
                                       pd.read_parquet(self.translate(path), *a, **kw))
            module.open = lambda path, *a, **kw: builtins.open(self.translate(path), *a, **kw)
            self.adapted = True

    def pytest_runtest_logreport(self, report):
        self.results.append({"nodeid": report.nodeid, "phase": report.when,
                             "outcome": report.outcome})


def main(argv=None):
    import pandas as pd
    import pyarrow
    import pytest

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.report.exists():
        parser.error("report must be a fresh path")
    manifest = json.loads((UPSTREAM / "SOURCES.json").read_text())
    identity = next(record for record in manifest["files"] if record["path"] == CHECKER)
    checker = UPSTREAM / CHECKER
    before = checker.read_bytes()
    if hashlib.sha256(before).hexdigest() != identity["sha256"]:
        raise ValueError("original verifier differs from pinned source")
    plugin = ArtifactPaths(args.output_dir)
    exit_code = int(pytest.main([str(checker), "-rA", "-p", "no:cacheprovider"], plugins=[plugin]))
    if checker.read_bytes() != before:
        raise AssertionError("original verifier changed during execution")
    artifact_facts = {name: {"bytes": (args.output_dir / name).stat().st_size,
        "sha256": hashlib.sha256((args.output_dir / name).read_bytes()).hexdigest()}
        for name in ARTIFACTS if (args.output_dir / name).is_file()}
    report = {"status": "PASS" if exit_code == 0 else "FAIL", "pytest_exit_code": exit_code,
        "checker": identity, "checker_unchanged_after": True,
        "upstream_commit": manifest["commit"], "assertions_modified": False,
        "adaptation": {"kind": "offline_module_file_access_path_mapping",
            "mapping": plugin.mapping, "applied": plugin.adapted,
            "scope": "checker module names os.path.exists, pd.read_parquet, open; delegate to real I/O",
            "test_sh": "NOT_RUN: installs packages; existing environment reused",
            "container": "NOT_RUN", "official_dataset_benchmark": False,
            "official_reward": None, "ctrf": "NOT_RUN"},
        "environment": {"python": platform.python_version(), "pandas": pd.__version__,
            "pyarrow": pyarrow.__version__, "pytest": pytest.__version__},
        "tests": plugin.results, "artifacts": artifact_facts}
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
