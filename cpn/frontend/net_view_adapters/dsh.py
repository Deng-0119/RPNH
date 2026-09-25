"""DSH-specific session/request selection for the main PN viewer."""
from __future__ import annotations
from pathlib import Path
import re
from cpn.rpnh.registry.schema_catalog import SchemaCatalog
from ._registry import RegistryViewBinding, ViewBindingError, bind_session, host_directory


def bind_dsh(root: Path, session_id: str, *, turn: str | int = 'latest',
             request_id: str | None = None, catalog: SchemaCatalog | None = None) -> RegistryViewBinding:
    if not isinstance(session_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session_id):
        raise ViewBindingError('invalid managed DSH session identity')
    session = host_directory(Path(root), session_id)
    return bind_session(session, host='dsh', catalog=SchemaCatalog() if catalog is None else catalog,
                        turn=turn, request_id=request_id, session_id=session_id)


def main(argv=None) -> int:
    from ._cli import run_cli
    return run_cli('dsh', bind_dsh, argv)


if __name__ == '__main__':
    raise SystemExit(main())
