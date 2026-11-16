"""Read-only MainSession/task admission before opening a Registry writer.

This composes existing MainThread and task-manifest contracts for one selected
root.  It is not a cross-release write grant: child module/plugin/route and old
tool-environment compatibility retain their own existing checks and remaining
qualification requirements. No last-writer file or second Registry is created.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import stat

from .registry._registry import _RegistryCore
from .registry.main_thread import MainThreadAuthorityError, MainThreadRegistry
from .registry.main_thread_history import MainThreadReadCut
from .task_control import TaskInventory, inspect_task_inventory


class BusinessAdmissionError(ValueError):
    """A selected MainSession's existing facts cannot be safely interpreted."""


def _source_identity(path: Path, *, directory: bool = False) -> tuple[int, int]:
    value = path.lstat()
    if (stat.S_ISLNK(value.st_mode)
            or not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))):
        raise BusinessAdmissionError(f"main-session admission source changed: {path}")
    return value.st_dev, value.st_ino


@dataclass(frozen=True, slots=True)
class MainSessionAdmission:
    reader: MainThreadRegistry
    cut: MainThreadReadCut
    tasks: TaskInventory
    sources: tuple[tuple[Path, bool, tuple[int, int]], ...]

    def assert_unchanged(self) -> None:
        """Recheck the inspected source/cut immediately before writer entry."""
        for path, directory, identity in self.sources:
            if _source_identity(path, directory=directory) != identity:
                raise BusinessAdmissionError(f"main-session admission source changed: {path}")
        core = self.reader.core
        _first, boundary = core.event_store.first_and_last_events()
        if (boundary is None or boundary.event_id != self.cut.boundary_event_id
                or boundary.ordinal != self.cut.view.through_ordinal
                or core.event_store.get_meta("task_id") != str(self.cut.task_id)
                or core.event_store.get_meta("branch_id") != self.cut.branch_id):
            raise BusinessAdmissionError("main-session admission Registry cut changed")
        self.tasks.assert_unchanged()


def inspect_main_session_write_admission(root: Path) -> MainSessionAdmission:
    """Inspect one root without a writer, pending recovery, or process spawn.

    Formal frontend callers already hold their MainSession Owner lease. The
    inspection does not acquire or replace that lease and does not make the
    direct Python API an independently owned session.
    """
    if not isinstance(root, Path):
        raise TypeError("main-session admission requires pathlib.Path")
    paths = ((root, True), (root / "main", True),
             (root / "main" / ".registry_v1", True),
             (root / "main" / ".registry_v1" / "registry.sqlite3", False))
    try:
        sources = tuple((path, directory, _source_identity(path, directory=directory))
                        for path, directory in paths)
        core = _RegistryCore(root / "main", create=False, read_only=True)
        reader = MainThreadRegistry(core, session_root=root)
        cut = reader.capture_read_cut()
        # The active main turn is the one child this session will continue.
        # Reuse its existing exact Registry observation before acquiring the
        # parent writer. Accepted turns and an attached path without a child DB
        # remain legitimate; an existing malformed DB is never pending_start.
        active_turn = reader.project_thread_at(cut).active_turn_ref
        if active_turn is not None:
            try:
                reader.observe_turn_execution(turn_ref=active_turn)
            except MainThreadAuthorityError as exc:
                raise BusinessAdmissionError(
                    f"active main-turn {active_turn.version_id} in {root / 'main'}: {exc}"
                ) from exc
        # Check the selected main Registry's actual type inventory, including
        # records outside the main-thread projection. A current version string
        # cannot make an unknown persisted contract writable.
        object_types = {row["object_type"] for row in core.event_store.canonical_object_rows(
            through_ordinal=cut.view.through_ordinal)}
        for object_type in object_types:
            core.catalog.require(object_type, category="object")
        event_types = {event.event_type for event in core.event_store.canonical_events(
            through_ordinal=cut.view.through_ordinal)}
        for event_type in event_types:
            core.catalog.require(event_type, category="event")
        relation_types = {row["relation_type"] for row in core.event_store.canonical_relation_rows(
            through_ordinal=cut.view.through_ordinal)}
        for relation_type in relation_types:
            core.catalog.require(relation_type, category="relation")
        tasks = inspect_task_inventory(reader.child_path_root / "tasks")
        tasks.require_write_compatible()
        admission = MainSessionAdmission(reader, cut, tasks, sources)
        admission.assert_unchanged()
        return admission
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        raise BusinessAdmissionError(f"main-session write admission rejected: {exc}") from exc


__all__ = ("BusinessAdmissionError", "MainSessionAdmission", "inspect_main_session_write_admission")
