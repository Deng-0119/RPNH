"""Host-side checkpoint lineage contracts; this module never solves or grades.

The benchmark runner remains the only checkpoint scheduler. These helpers accept
only the prompt it has already revealed and digest files in its current Session.
They do not load the problem repository, future prompts, tests or reference code.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import stat
from typing import Any, Mapping


CHECKPOINTS = tuple(f"checkpoint_{n}" for n in range(1, 6))


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


@dataclass(frozen=True)
class CheckpointScope:
    names: tuple[str, ...] = CHECKPOINTS[:3]

    def __post_init__(self) -> None:
        if (not isinstance(self.names, tuple) or not self.names
                or self.names != CHECKPOINTS[:len(self.names)]):
            raise ValueError("scope must be a nonempty original checkpoint prefix")

    @property
    def coverage(self) -> str:
        return "full_task" if self.names == CHECKPOINTS else "partial_prefix"


def workspace_manifest(root: Path, *, max_files: int = 1000,
                       max_bytes: int = 16 * 1024 * 1024) -> dict[str, Any]:
    """Digest an already agent-safe workspace without following links.

    This is evidence, not a sandbox, snapshot materializer or input sanitizer.
    The caller must supply the upstream Session workspace, never a task checkout.
    No paths are silently omitted. Unsupported filesystem shapes fail closed.
    """
    if root.is_symlink() or not root.is_dir():
        raise ValueError("workspace must be a real directory")
    rows = []
    total = 0
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode) or path.stat().st_nlink != 1:
            raise ValueError("workspace links and special files are unsupported")
        size = path.stat().st_size
        total += size
        if len(rows) >= max_files or total > max_bytes:
            raise ValueError("workspace exceeds declared manifest limits")
        content = path.read_bytes()
        if len(content) != size:
            raise ValueError("workspace changed during observation")
        rows.append({"path": path.relative_to(root).as_posix(),
                     "sha256": hashlib.sha256(content).hexdigest(),
                     "size_bytes": size, "executable": bool(mode & 0o111)})
    body = {"schema_version": "rpnh/slopcodebench-workspace/v1", "files": rows}
    return {**body, "sha256": digest(body)}


def current_request(*, checkpoint: str, prompt: str,
                    workspace: Mapping[str, Any], definition_sha256: str,
                    predecessor_sha256: str | None = None) -> dict[str, Any]:
    """An input identity, not a claim that RPNH admitted this checkpoint."""
    if checkpoint not in CHECKPOINTS or not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("a currently revealed checkpoint and nonempty prompt are required")
    for value in (definition_sha256, workspace.get("sha256"), predecessor_sha256):
        if value is not None and (not isinstance(value, str) or len(value) != 64
                                  or any(c not in "0123456789abcdef" for c in value)):
            raise ValueError("exact identities require lowercase SHA-256 digests")
    if workspace.get("sha256") is None:
        raise ValueError("workspace digest is required")
    return {"schema_version": "rpnh/slopcodebench-request/v1",
            "checkpoint": checkpoint,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "workspace_sha256": workspace["sha256"],
            "definition_sha256": definition_sha256,
            "predecessor_sha256": predecessor_sha256}


class CheckpointLedger:
    """Append-only evidence of runner callbacks, never permission to advance.

    The original runner's evaluator/pass policy decides whether a next callback
    occurs. This ledger cannot reveal checkpoints or infer an official score.
    """

    def __init__(self, root: Path, scope: CheckpointScope = CheckpointScope()):
        self.root, self.scope = root, scope
        root.mkdir(parents=True, exist_ok=True)

    def records(self) -> list[dict[str, Any]]:
        records = []
        previous = None
        for index, path in enumerate(sorted(self.root.glob("record-*.json"))):
            if index >= len(self.scope.names) or path.name != f"record-{index + 1:02}.json":
                raise ValueError("checkpoint ledger has a gap or unexpected entry")
            body = json.loads(path.read_text(encoding="utf-8"))
            if (body["request"]["checkpoint"] != self.scope.names[index]
                    or body["request"]["predecessor_sha256"] != previous):
                raise ValueError("checkpoint ledger has invalid identity or predecessor")
            if records and records[-1]["handoff_status"] != "submitted":
                raise ValueError("checkpoint ledger advanced after an unfinished handoff")
            previous = digest(body)
            records.append(body)
        return records

    def append(self, request: dict[str, Any], *, handoff_status: str,
               rpnh_result: Mapping[str, Any] | None, reason: str | None = None,
               settled_workspace_sha256: str | None = None) -> dict[str, Any]:
        records = self.records()
        if (len(records) >= len(self.scope.names)
                or request["checkpoint"] != self.scope.names[len(records)]):
            raise ValueError("runner callback is outside the next original checkpoint")
        previous = digest(records[-1]) if records else None
        if request["predecessor_sha256"] != previous:
            raise ValueError("request predecessor differs from immutable history")
        if records and records[-1]["handoff_status"] != "submitted":
            raise ValueError("cannot advance after an unfinished handoff")
        if handoff_status not in {"submitted", "failed", "blocked", "not_run"}:
            raise ValueError("unknown handoff status")
        if handoff_status == "submitted" and not rpnh_result:
            raise ValueError("submission requires actual RPNH result identity")
        if handoff_status == "submitted" and (
                not isinstance(settled_workspace_sha256, str)
                or len(settled_workspace_sha256) != 64
                or any(c not in "0123456789abcdef" for c in settled_workspace_sha256)):
            raise ValueError("submission requires the exact settled workspace digest")
        if handoff_status != "submitted" and not reason:
            raise ValueError("unfinished handoff requires a reason")
        body = {"schema_version": "rpnh/slopcodebench-lineage/v1",
                "coverage": self.scope.coverage, "request": request,
                "handoff_status": handoff_status,
                "rpnh_result": None if rpnh_result is None else dict(rpnh_result),
                "settled_workspace_sha256": settled_workspace_sha256,
                "original_evaluation": {"status": "not_collected", "score": None},
                "reason": reason}
        destination = self.root / f"record-{len(records) + 1:02}.json"
        with destination.open("xb") as stream:
            stream.write(canonical(body) + b"\n")
        return body
