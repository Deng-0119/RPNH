"""Read-only Registry binding shared by the optional host selectors.

This module intentionally uses the main-thread authority's existing readers
rather than interpreting UI transcripts or instantiating execution hosts.
Private reader dependencies are confined here and covered by main tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping

from cpn.rpnh.inspection import project_registry_net
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.main_thread import MainThreadRegistry, _parse_ref, _ref_payload
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.schema_catalog import SchemaCatalog


class ViewBindingError(ValueError):
    """The requested host/run cannot be bound without guessing or writing."""


def host_directory(root: Path, identity: str) -> Path:
    """Restrict a host identifier to one existing, non-symlink directory."""
    if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}', identity):
        raise ViewBindingError('host identity must be one plain identifier')
    return registered_path(Path(root).resolve(strict=True), identity)


def registered_path(root: Path, relative: str) -> Path:
    """Do not let a registered relative link escape its declared session."""
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise ViewBindingError('registered path must be a relative POSIX path')
    pure = PurePosixPath(relative)
    if pure.is_absolute() or '..' in pure.parts or str(pure) != relative or relative == '.':
        raise ViewBindingError('registered path is not a canonical relative path')
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ViewBindingError('registered path contains a symlink')
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_dir():
        raise ViewBindingError('registered path is outside the session or not a directory')
    return resolved


def _open(path: Path, catalog: SchemaCatalog) -> _RegistryCore:
    return _RegistryCore(path, create=False, read_only=True, catalog=catalog)


def _head(core: _RegistryCore) -> tuple[int, int]:
    return core.event_store.max_ordinal(), core.event_store.writer_epoch


def _identity(path: Path, catalog: SchemaCatalog) -> tuple[VersionRef, VersionRef]:
    core = _open(path, catalog)
    reader = MainThreadRegistry(core, session_root=path)
    raw = core.event_store.get_meta('native_run_ref')
    if raw is None:
        raise ViewBindingError('registered directory has no native run identity yet')
    run_ref = _parse_ref(json.loads(raw))
    run = reader._read_exact(core, run_ref, expected_type='native_run_identity/v1')
    task_ref = _parse_ref(run['task_ref'])
    reader._read_exact(core, task_ref, expected_type='task/v1')
    if task_ref.entity_id != core.task_id:
        raise ViewBindingError('run and task identities differ')
    return task_ref, run_ref


def _select_turn(reader: MainThreadRegistry, state: Mapping[str, Any], turn: str | int,
                 request_id: str | None, session_id: str | None):
    thread_ref = _parse_ref(state['thread_ref'])
    latest = [lineage[-1] for lineage in reader._turn_lineages(thread_ref.entity_id)]
    if request_id is not None:
        if not isinstance(request_id, str) or not request_id:
            raise ViewBindingError('request_id must be nonempty')
        matches = [(ref, doc) for ref, doc in latest
                   if isinstance(doc.get('user_input'), Mapping)
                   and doc['user_input'].get('request_id') == request_id]
    elif turn == 'active':
        matches = [(ref, doc) for ref, doc in latest if _ref_payload(ref) == state['active_turn_ref']]
    elif turn == 'latest':
        matches = latest[-1:]
    elif type(turn) is int and turn > 0:
        matches = [(ref, doc) for ref, doc in latest if doc['ordinal'] == turn]
    elif isinstance(turn, str) and re.fullmatch(r'[1-9][0-9]*', turn):
        matches = [(ref, doc) for ref, doc in latest if doc['ordinal'] == int(turn)]
    else:
        raise ViewBindingError('turn must be active, latest or a positive ordinal')
    if len(matches) != 1:
        raise ViewBindingError('turn/request selection is missing or ambiguous')
    ref, doc = matches[0]
    if session_id is not None:
        user_input = doc.get('user_input')
        if not isinstance(user_input, Mapping) or user_input.get('session_id') != session_id:
            raise ViewBindingError('request belongs to a different DSH session')
    if not isinstance(doc.get('attempt_relative_path'), str):
        raise ViewBindingError('selected turn has no attempt yet; no fallback to an older turn')
    return ref, doc


@dataclass(frozen=True, slots=True)
class RegistryViewBinding:
    """One pinned attempt; callable directly as the existing ProjectionProvider."""
    host: str
    session_root: Path
    run_dir: Path
    relative_path: str
    selection_ref: VersionRef
    selection_kind: str
    main_head: tuple[int, int]
    task_ref: VersionRef
    run_ref: VersionRef
    catalog: SchemaCatalog = field(repr=False, compare=False)

    def describe(self) -> dict[str, Any]:
        return {'schema_version': 'rpnh/net_view_binding/v1', 'host': self.host,
                'session_root': str(self.session_root), 'run_dir': str(self.run_dir),
                'registered_relative_path': self.relative_path,
                'selection_kind': self.selection_kind, 'selection_ref': _ref_payload(self.selection_ref),
                'main_observed_head_ordinal': self.main_head[0],
                'main_observed_writer_epoch': self.main_head[1],
                'task_ref': _ref_payload(self.task_ref), 'run_ref': _ref_payload(self.run_ref)}

    def __call__(self) -> dict[str, Any]:
        # A new turn or resume must not silently replace this page's target.
        path = registered_path(self.session_root, self.relative_path)
        if path != self.run_dir:
            raise ViewBindingError('bound Registry location changed')
        for _ in range(3):
            core = _open(path, self.catalog)
            before = _head(core)
            if _identity(path, self.catalog) != (self.task_ref, self.run_ref):
                raise ViewBindingError('bound native run identity changed')
            snapshot = project_registry_net(path, catalog=self.catalog)
            if _identity(path, self.catalog) != (self.task_ref, self.run_ref):
                raise ViewBindingError('bound native run identity changed while reading')
            if _head(core) != before:
                continue
            observed = snapshot['source'].get('verified_head_ordinal')
            if observed is not None and observed != before[0]:
                continue
            return snapshot
        raise ViewBindingError('Registry changed during projection; retry the same binding')


def bind_session(session_root: Path, *, host: str, catalog: SchemaCatalog,
                 turn: str | int = 'latest', task_id: str | None = None,
                 request_id: str | None = None, session_id: str | None = None) -> RegistryViewBinding:
    if not isinstance(catalog, SchemaCatalog):
        raise TypeError('catalog must be an explicit SchemaCatalog')
    if task_id is not None and request_id is not None:
        raise ViewBindingError('task and request selectors are mutually exclusive')
    if (task_id is not None or request_id is not None) and turn != 'latest':
        raise ViewBindingError('choose either a turn or a task/request selector')
    session_root = session_root.resolve(strict=True)
    for _ in range(3):
        core = _open(registered_path(session_root, 'main'), catalog)
        reader = MainThreadRegistry(core, session_root=session_root)
        before = _head(core)
        state = reader.project_current_thread()
        expected_task = expected_run = None
        if task_id is not None:
            matches = [link for link in state['child_registry_links'] if link['task_control_id'] == task_id]
            if len(matches) != 1:
                raise ViewBindingError('task is not uniquely registered in the selected thread')
            link = matches[0]
            selection_ref = _parse_ref(link['main_child_registry_link_ref'])
            relative = link['registry_relative_path']
            expected_task, expected_run = link.get('child_task_ref'), link.get('child_run_ref')
            kind = 'child_task'
        else:
            selection_ref, doc = _select_turn(reader, state, turn, request_id, session_id)
            relative, kind = doc['attempt_relative_path'], 'main_turn'
        path = registered_path(session_root, relative)
        task_ref, run_ref = _identity(path, catalog)
        if expected_task is not None and expected_task != _ref_payload(task_ref):
            raise ViewBindingError('child task differs from its registered identity')
        if expected_run is not None and expected_run != _ref_payload(run_ref):
            raise ViewBindingError('child run differs from its registered identity')
        if _head(core) == before:
            return RegistryViewBinding(host, session_root, path, relative, selection_ref, kind,
                                       before, task_ref, run_ref, catalog)
    raise ViewBindingError('main Registry changed during selection; retry explicitly')
