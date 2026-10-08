"""Developer-only integrity checks for two frozen, unchanged upstream tasks.

Only ``instruction_bytes`` may be passed to an actor. This module does not stage
the checkout, checker, solution or developer metadata into a solver workspace.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tomllib

RPNH_BASE = "ae09445fe1d9b973502bc5d2c961976c1d2c0163"
UPSTREAM_COMMIT = "ceba3880af555129b5278e056a0c20f2fb5a0ba9"
UPSTREAM_REPOSITORY = "https://github.com/agentic-labs/erp-bench"
ERP_PREFIX = "examples/erp_bench/"
TASK_IDS = ("2000_easy_01_buy_only_baseline", "2299_hard_repair_plan_hard")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def load_json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError(f"nonfinite JSON number: {value}")

    result = json.loads(data, object_pairs_hook=pairs, parse_constant=nonfinite)
    # Also catches overflow such as 1e999, which parse_constant does not see.
    json_bytes(result)
    return result


def safe_relative(name: str) -> PurePosixPath:
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or any(ord(c) < 32 for c in name)
            or any(p in ("", ".", "..") for p in name.split("/"))):
        raise ValueError("unsafe relative path")
    path = PurePosixPath(name)
    if path.is_absolute():
        raise ValueError("absolute paths are not portable")
    return path


def safe_file(root, name: str) -> Path:
    root = Path(root).resolve(strict=True)
    path = root
    for part in safe_relative(name).parts:
        path /= part
        if path.is_symlink():
            raise ValueError(f"symlink not allowed: {name}")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError(f"missing/conflicting file: {name}")
    return path


def git(root, *args) -> bytes:
    result = subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args],
                            capture_output=True, check=False)
    if result.returncode:
        raise ValueError(f"git {args[0]} failed (exit {result.returncode})")
    return result.stdout


def _metadata(name):
    candidates = (Path(__file__).resolve().parent / "data" / name,
                  Path(sys.prefix) / "share/rpnh-erp-bench/data" / name)
    for path in candidates:
        if path.is_file():
            return load_json(path.read_bytes())
    raise ValueError("ERP metadata missing; install the example's data files")


@dataclass(frozen=True)
class VerifiedTask:
    task_id: str
    task_path: Path
    instruction_bytes: bytes
    task_toml: dict
    file_hashes: dict
    source_pin: str = UPSTREAM_COMMIT

    @property
    def instruction_path(self) -> Path:
        return self.task_path / "instruction.md"

    @property
    def instruction_sha256(self) -> str:
        return sha256(self.instruction_bytes)

    @property
    def seed(self) -> int:
        return self.task_toml["metadata"]["seed"]

    @property
    def official_limits(self) -> dict:
        return {"agent_timeout_sec": self.task_toml["agent"]["timeout_sec"],
                "verifier_timeout_sec": self.task_toml["verifier"]["timeout_sec"],
                **self.task_toml["environment"]}

    @property
    def input_sha256(self) -> str:
        """Digest of canonical public task/source identity, not a world snapshot."""
        return sha256(json_bytes(self.public_identity()))

    def public_identity(self) -> dict:
        return {"task_id": self.task_id, "repository": UPSTREAM_REPOSITORY,
                "revision": self.source_pin, "seed": self.seed,
                "instruction_sha256": self.instruction_sha256,
                "task_toml": self.task_toml, "official_limits": self.official_limits,
                "files": self.file_hashes,
                "scope": "Pinned source inputs only; seeded world identity is separate."}


def validate_task(upstream_root, task_id: str) -> VerifiedTask:
    if task_id not in TASK_IDS:
        raise ValueError("unsupported task ID")
    root = Path(upstream_root).resolve(strict=True)
    if git(root, "rev-parse", "--show-toplevel").decode().strip() != str(root):
        raise ValueError("upstream root must be the checkout root")
    if git(root, "rev-parse", "HEAD").decode().strip() != UPSTREAM_COMMIT:
        raise ValueError("upstream commit mismatch")
    origin = git(root, "remote", "get-url", "origin").decode().strip()
    if origin not in (UPSTREAM_REPOSITORY, UPSTREAM_REPOSITORY + ".git",
                       "git@github.com:agentic-labs/erp-bench.git"):
        raise ValueError("upstream origin mismatch")
    metadata = _metadata("task-identities.json")
    if metadata["revision"] != UPSTREAM_COMMIT:
        raise ValueError("task metadata pin mismatch")
    expected = metadata["tasks"][task_id]
    files = expected["files"]
    task_prefix = f"tasks/{task_id}/"
    names = set(git(root, "ls-files", "-z", "--", task_prefix).decode().split("\0")) - {""}
    if names != {name for name in files if name.startswith(task_prefix)}:
        raise ValueError("selected task tracked file set mismatch")
    # Check index changes too, even when working bytes were restored manually.
    if git(root, "diff", "HEAD", "--name-only", "--", *files):
        raise ValueError("selected tracked source is dirty")
    if git(root, "ls-files", "--others", "--exclude-standard", "--", task_prefix):
        raise ValueError("untracked selected task inputs")
    for name, identity in files.items():
        data = safe_file(root, name).read_bytes()
        if sha256(data) != identity["sha256"] or len(data) != identity["bytes"]:
            raise ValueError(f"source digest mismatch: {name}")
        if git(root, "rev-parse", f"HEAD:{name}").decode().strip() != identity["git_blob_sha1"]:
            raise ValueError(f"source Git blob mismatch: {name}")
    instruction = safe_file(root, task_prefix + "instruction.md").read_bytes()
    task_toml = tomllib.loads(safe_file(root, task_prefix + "task.toml").read_text())
    if task_toml != expected["task_toml"]:
        raise ValueError("task TOML identity mismatch")
    return VerifiedTask(task_id, root / "tasks" / task_id, instruction, task_toml, files)
