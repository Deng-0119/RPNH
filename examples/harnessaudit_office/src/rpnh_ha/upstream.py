"""Lazy imports of original HarnessAudit code. No local business mock fallback."""
from __future__ import annotations
from pathlib import Path
import sqlite3
from .constants import OFFICE_EFFECTS, TASK_ID
from .office_cases import case_path
from .task_view import public_task


def load_case(audit_root: Path, task_id: str = TASK_ID):
    from multi_agent.loader import load_task_with_tools
    import multi_agent.loader as module
    expected = (audit_root / "multi_agent" / "loader.py").resolve()
    if Path(module.__file__).resolve() != expected:
        raise RuntimeError("imported HarnessAudit does not match the selected source checkout")
    task, catalog = load_task_with_tools(case_path(audit_root, task_id),
                                         tools_dir=audit_root / "multi_agent" / "tools")
    if task.task_id != task_id:
        raise ValueError("loaded task identity differs from selected case")
    actual = {t.name for t in catalog.tools}
    if actual != set(OFFICE_EFFECTS):
        raise RuntimeError("office tool inventory changed: " + repr(sorted(actual ^ set(OFFICE_EFFECTS))))
    public = public_task(task, catalog)
    return task, catalog, public


def office_bank_factory(audit_root: Path):
    seed_dir = audit_root / "multi_agent" / "fixtures" / "office"
    # Upstream tolerates missing seed files by producing an empty database.
    # Fail before execution instead of mistaking empty setup for model failure.
    required = ("seed_directory.json", "seed_assets.json", "seed_knowledge.json")
    # Exact files are checked before execution; populated tables are checked too.
    if not seed_dir.is_dir() or any(not (seed_dir / name).is_file() for name in required):
        raise FileNotFoundError("office fixture release is missing")
    def create():
        from multi_agent.banks.office import OfficeEnterpriseManagementBank
        bank = OfficeEnterpriseManagementBank(seed_dir)
        for table in ("employees", "hardware_assets", "knowledge_articles"):
            if bank.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0:
                bank.conn.close()
                raise RuntimeError(f"fixture table {table} is empty")
        return bank
    return create


def official_dispatch(bank, tool: str, args: dict) -> str:
    from multi_agent.frameworks.core.tool_dispatch import dispatch_tool
    if bank is None: raise RuntimeError("bank fallback is prohibited")
    return dispatch_tool(tool, args, bank=bank)


class SnapshotBank:
    """Evaluator-only read-only view; never passed to the agent driver."""
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA query_only=ON")

    def query_sql(self, query: str):
        return [dict(row) for row in self.conn.execute(query).fetchall()]

    def close(self): self.conn.close()
