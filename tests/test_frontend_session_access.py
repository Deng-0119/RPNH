from __future__ import annotations

import json
from pathlib import Path

import pytest

from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.main_thread import MainThreadRegistry
from cpn.rpnh.session_access import (
    MainSessionOwnerLease,
    MainSessionRoot,
    inspect_main_session_root,
    stable_frontend_session_id,
)


def _session_root(tmp_path: Path, name: str = "session") -> Path:
    root = tmp_path / name
    root.mkdir()
    execution = tmp_path / f"{name}-execution.json"
    execution.write_text("{}", encoding="utf-8")
    (root / "execution_profile.json").write_text(json.dumps({
        "schema_version": "rpnh/main_session_profile/v2",
        "execution_config_path": str(execution),
        "adapter_config_path": "/configured/adapter.json",
        "selection_id": "selection",
        "provider": "local",
        "model_condition": "offline",
        "adapter_kind": "local_process",
        "registry_policy": {},
    }), encoding="utf-8")
    core = _RegistryCore(root / "main", create=True)
    MainThreadRegistry(core, session_root=root,
                       initialize_path_base=MainThreadRegistry.REGISTRY_ROOT_PATH_BASE).create_thread(
                           idempotency_key="initial-main-thread")
    return root


def test_inspect_direct_root_is_read_only_and_returns_persisted_config(tmp_path: Path) -> None:
    root = _session_root(tmp_path)
    before = {path.relative_to(root): path.read_bytes()
              for path in root.rglob("*") if path.is_file()}

    found = inspect_main_session_root(root)

    assert found == MainSessionRoot(root.resolve(), (tmp_path / "session-execution.json").resolve())
    after = {path.relative_to(root): path.read_bytes()
             for path in root.rglob("*") if path.is_file()}
    assert after == before


def test_inspect_rejects_containers_invalid_roots_and_symlinks(tmp_path: Path) -> None:
    root = _session_root(tmp_path)
    container = tmp_path / "frontend"
    (container / "threads").mkdir(parents=True)
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    link = tmp_path / "session-link"
    link.symlink_to(root, target_is_directory=True)

    with pytest.raises(ValueError, match="container"):
        inspect_main_session_root(container)
    with pytest.raises(ValueError, match="Registry database"):
        inspect_main_session_root(invalid)
    with pytest.raises(ValueError, match="symlink"):
        inspect_main_session_root(link)


def test_owner_lease_rejects_a_symlink_lock_path(tmp_path: Path) -> None:
    root = _session_root(tmp_path)
    target = tmp_path / "lock-target"
    target.write_text("not a lock", encoding="utf-8")
    (root / ".main-session-owner.lock").symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        MainSessionOwnerLease(root)


@pytest.mark.parametrize("component", ["main", "storage", "database", "profile"])
def test_inspect_rejects_internal_session_symlinks(
        tmp_path: Path, component: str,
) -> None:
    root = _session_root(tmp_path)
    if component == "main":
        target = tmp_path / "main-target"
        (root / "main").rename(target)
        (root / "main").symlink_to(target, target_is_directory=True)
    elif component == "storage":
        target = tmp_path / "storage-target"
        (root / "main" / ".registry_v1").rename(target)
        (root / "main" / ".registry_v1").symlink_to(
            target, target_is_directory=True)
    elif component == "database":
        database = root / "main" / ".registry_v1" / "registry.sqlite3"
        target = tmp_path / "registry-target.sqlite3"
        database.rename(target)
        database.symlink_to(target)
    else:
        profile = root / "execution_profile.json"
        target = tmp_path / "profile-target.json"
        profile.rename(target)
        profile.symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        inspect_main_session_root(root)


def test_owner_lease_contends_then_releases_and_leaves_sidecar(tmp_path: Path) -> None:
    root = _session_root(tmp_path)
    record = inspect_main_session_root(root)

    first = MainSessionOwnerLease(record)
    with pytest.raises(RuntimeError, match="owner lease"):
        MainSessionOwnerLease(root)
    first.close()
    with MainSessionOwnerLease.acquire(root):
        assert (root / ".main-session-owner.lock").is_file()
    assert (root / ".main-session-owner.lock").is_file()


def test_stable_frontend_session_id_has_required_display_shape(tmp_path: Path) -> None:
    root = _session_root(tmp_path)

    first = stable_frontend_session_id(root)
    second = stable_frontend_session_id(inspect_main_session_root(root))

    assert first == second
    assert first.startswith("ses_")
    assert len(first) == 36
    assert all(character in "0123456789abcdef" for character in first[4:])
