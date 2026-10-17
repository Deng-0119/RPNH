"""Offline tests. Optional private input stays outside the public example."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest


spec = importlib.util.spec_from_file_location("local_business_world", Path(__file__).parents[1] / "world.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BusinessWorld = module.BusinessWorld


@pytest.fixture
def world(tmp_path):
    return BusinessWorld(tmp_path / "world", workspace_root=tmp_path)


def test_git_versions_and_exact_bytes(world):
    seed = world.snapshot()["git_refs"]["refs/heads/main"]
    world.create_branch("platform_admin", "mini-seed-repo", "coord/change")
    args = dict(owner="platform_admin", repo="mini-seed-repo", branch_name="coord/change",
                filePath="sql/change.sql", content="SELECT 1;\r\n", message="Record change")
    first = world.create_or_update_file(**args)
    args["content"] = "SELECT 2;\n"
    second = world.create_or_update_file(**args)
    assert first["parent_commit"] == seed
    assert second["parent_commit"] == first["commit"]
    assert world.read_git_file(args["owner"], args["repo"], args["filePath"], commit=first["commit"])["content"] == "SELECT 1;\r\n"
    assert world.read_git_file(args["owner"], args["repo"], args["filePath"], "coord/change")["content"] == args["content"]
    assert world._git("log", "-1", "--format=%B", second["commit"]).rstrip("\n") == args["message"]
    assert world.snapshot()["git_refs"]["refs/heads/main"] == seed
    with pytest.raises(subprocess.CalledProcessError):
        world.create_branch("platform_admin", "mini-seed-repo", "coord/change")


def test_note_identity_history_and_stale_refusal(world):
    note = world.create_file("note.txt", "text/plain", ["folder"], "Shell")
    first = world.read_note(note["id"])
    second = world.update_note(note["id"], "Revision", expected_version=1,
                               validation_evidence={"checked_commit": "example"})
    assert first["body"] == ""
    assert second["id"] == first["id"]
    assert second["version"] == 2 and second["ref"] != first["ref"]
    assert {k: second[k] for k in ("name", "mimeType", "parents", "description")} == {
        k: first[k] for k in ("name", "mimeType", "parents", "description")}
    before = {str(p): p.read_bytes() for p in world.root.rglob("*") if p.is_file()}
    with pytest.raises(module.StaleVersionError):
        world.update_note(note["id"], "Stale", expected_version=1)
    assert before == {str(p): p.read_bytes() for p in world.root.rglob("*") if p.is_file()}
    assert world.read_note(note["id"], 1) == first
    third = world.update_note(note["id"], "Third", expected_version=2)
    assert third["version"] == 3
    third["parents"].append("detached")
    assert world.read_note(note["id"])["parents"] == ["folder"]


def test_incident_and_detached_snapshot(world):
    fields = {"caller_id": "test-user", "status": "new", "worknotes": "Unmodified"}
    incident = world.create_incident(**fields, validation_evidence={"note_version": 2})
    assert incident["fields"] == fields
    snapshot = world.snapshot()
    assert snapshot["incidents"] == [incident]
    snapshot["incidents"][0]["fields"]["status"] = "changed"
    assert world.snapshot()["incidents"][0]["fields"] == fields


def test_original_input_contract(world):
    source = os.environ.get("MUP_UPSTREAM_INPUT")
    if source is None:
        pytest.skip("private upstream input not supplied")
    task = json.loads(Path(source).read_text(encoding="utf-8"))
    receipts = []
    for subtask in task["sub_task_list"]:
        for step in subtask["ground_truth"]:
            name = step["tool_name"]
            if name not in {"create_file", "create_branch", "create_or_update_file", "create_incident"}:
                continue
            args = step["arguments"]
            receipt = getattr(world, name)(**args)
            receipts.append(receipt)
            if name == "create_file":
                assert all(receipt[k] == v for k, v in args.items())
            elif name == "create_or_update_file":
                read = world.read_git_file(args["owner"], args["repo"], args["filePath"], commit=receipt["commit"])
                assert read["content"] == args["content"]
                assert receipt["ref"] == "refs/heads/" + args["branch_name"]
                assert world._git("cat-file", "commit", receipt["commit"]).split("\n\n", 1)[1] == args["message"]
            elif name == "create_incident":
                assert receipt["fields"] == args
    assert len(receipts) == 5
    assert len(world.snapshot()["incidents"]) == 1


@pytest.mark.parametrize("path", ["../outside", "/absolute", ".git/config", "a/../../b", "a/./b"])
def test_repository_path_rejection(world, path):
    before = world.snapshot()
    with pytest.raises(ValueError):
        world.create_or_update_file("platform_admin", "mini-seed-repo", "main", path, "x", "x")
    assert world.snapshot() == before


def test_new_root_and_link_rejection(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        BusinessWorld(existing, workspace_root=tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(existing, target_is_directory=True)
    with pytest.raises(ValueError):
        BusinessWorld(alias / "world", workspace_root=tmp_path)
    with pytest.raises(ValueError):
        BusinessWorld(tmp_path.parent / "outside", workspace_root=tmp_path)
    world = BusinessWorld(tmp_path / "world", workspace_root=tmp_path)
    original = tmp_path / "original"
    original.write_text("untouched")
    (world.root / "drive" / "linked").symlink_to(original)
    with pytest.raises(ValueError):
        world.create_file("note", "text/plain", [], "shell")
    assert original.read_text() == "untouched"
    (world.root / "drive" / "linked").unlink()
    os.link(original, world.root / "drive" / "hardlinked")
    with pytest.raises(ValueError):
        world.create_branch("platform_admin", "mini-seed-repo", "change")
    assert original.read_text() == "untouched"


def test_git_environment_is_local(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "wrong-git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "wrong-tree"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path / "wrong-hooks"))
    world = BusinessWorld(tmp_path / "world", workspace_root=tmp_path)
    assert world._git("rev-parse", "--absolute-git-dir").strip() == str(world.repo / ".git")
    assert not (tmp_path / "wrong-git").exists()
    assert not (tmp_path / "wrong-tree").exists()
