"""Read-only compatibility probe, outside the five-file R2 implementation.

Reads completed test Registries created by the recorded author run. No new
owner, writer, registry, step, provider call, socket or replay is created.
"""
import hashlib
import json
from pathlib import Path
import sys

import cpn.rpnh.registry.run_authority as readers
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.task_control import TaskControl


def files(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


root = Path(sys.argv[1])
rows = []
for directory in sorted(root.iterdir()):
    if directory.is_symlink():
        continue
    run_dir = directory / "run"
    if not (run_dir / ".registry_v1/registry.sqlite3").is_file():
        continue
    before_files = files(run_dir)
    core = _RegistryCore(run_dir, create=False, read_only=True)
    before_head = core.event_store.max_ordinal()
    before_epoch = core.writer_epoch
    read = readers.read_run_execution(core, _ResourceServiceKernel(core))
    value = (None if read.terminal is None else
             json.loads(readers.read_run_terminal_bytes(core, read)))
    status = TaskControl._read_terminal_status(run_dir)
    read.cut.assert_unchanged(core)
    assert before_head == core.event_store.max_ordinal()
    assert before_epoch == core.writer_epoch
    assert before_files == files(run_dir)
    assert status["execution_status"] == read.status
    assert status["actual_model_call_counts"] == [0, 0]
    rows.append({"fixture": directory.name, "head": before_head,
        "writer_epoch": before_epoch, "status": read.status,
        "run_outcome": None if read.terminal is None else read.terminal.run_outcome,
        "terminal_version": None if read.terminal is None else str(read.terminal.evidence_ref.version_id),
        "result": value, "task_control_status": status, "unchanged": True})
assert rows, "completed author fixtures are no longer available"
output = {"reader_file": readers.__file__, "python": sys.executable,
          "registry_count": len(rows), "rows": rows}
Path(sys.argv[2]).write_text(json.dumps(output, indent=2) + "\n")
print(json.dumps({"registry_count": len(rows), "terminal_count": sum(row["status"] == "terminal" for row in rows),
                 "readonly_unchanged": True, "reader_file": readers.__file__}))
