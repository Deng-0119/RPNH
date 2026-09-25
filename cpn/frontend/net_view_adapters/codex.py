"""Codex-specific thread/child-task selection for the main PN viewer."""
from __future__ import annotations
from pathlib import Path
from cpn.rpnh.registry.schema_catalog import SchemaCatalog
from ._registry import RegistryViewBinding, bind_session, host_directory


def bind_codex(root: Path, thread_id: str, *, turn: str | int = 'latest',
               task_id: str | None = None, catalog: SchemaCatalog | None = None) -> RegistryViewBinding:
    if catalog is None:
        from cpn.rpnh.agent_tasks import agent_task_catalog
        catalog = agent_task_catalog()
    session = host_directory(Path(root) / 'threads', thread_id)
    return bind_session(session, host='codex', catalog=catalog, turn=turn, task_id=task_id)


def main(argv=None) -> int:
    from ._cli import run_cli
    return run_cli('codex', bind_codex, argv)


if __name__ == '__main__':
    raise SystemExit(main())
