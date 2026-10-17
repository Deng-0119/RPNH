"""Offline business state only; callers own authorization and write arbitration.

Construct a fresh world with BusinessWorld(root, workspace_root=existing_boundary).
Receipts are detached JSON values. Version refs and Git object IDs address retained
immutable state; branch refs and note IDs address the mutable business resource.
This adapter does not provide concurrent-writer transactions or crash recovery.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from uuid import uuid4


class StaleVersionError(ValueError):
    """The expected note version is no longer current; nothing was written."""


class BusinessWorld:
    def __init__(self, root: str | Path, *, workspace_root: str | Path):
        boundary = Path(workspace_root).absolute()
        self.root = Path(root).absolute()
        for path in (boundary, self.root):
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError("symlink in world boundary")
        if not boundary.is_dir() or not self.root.resolve().is_relative_to(boundary.resolve()):
            raise ValueError("world must be below the existing workspace boundary")
        if self.root == boundary or self.root.exists():
            raise FileExistsError("world root must be new")
        self.root.mkdir(parents=True)
        self.repo = self.root / "platform_admin" / "mini-seed-repo"
        self.repo.mkdir(parents=True)
        for name in ("drive", "incidents", "git-env", "git-env/tmp", "git-env/template"):
            (self.root / name).mkdir()
        self._git("init", "--initial-branch=main", "--template=" + str(self.root / "git-env/template"))
        tree = self._git("mktree", input="").strip()
        commit = self._git("commit-tree", tree, input="Initialize local business world\n").strip()
        self._git("update-ref", "refs/heads/main", commit)

    @classmethod
    def open_existing(cls, root, *, workspace_root):
        """Bind existing business objects without initialization or mutation."""
        self = cls.__new__(cls)
        self.root = Path(root).absolute()
        boundary = Path(workspace_root).resolve()
        if not self.root.resolve().is_relative_to(boundary) or self.root.resolve() == boundary:
            raise ValueError("world must remain inside the workspace")
        self.repo = self.root / "platform_admin" / "mini-seed-repo"
        self._check_tree()
        for path in (self.repo / '.git', self.root / 'drive', self.root / 'incidents',
                     self.root / 'git-env/tmp', self.root / 'git-env/template'):
            if not path.is_dir():
                raise ValueError("existing world is incomplete: " + str(path))
        return self

    def _check_tree(self):
        # Local world files are not tools for reading/writing arbitrary host paths.
        for path in (self.root, *self.root.parents):
            if path.is_symlink():
                raise ValueError("symlink in world boundary")
        for path in self.root.rglob("*"):
            if path.is_symlink() or (path.is_file() and path.stat().st_nlink != 1):
                raise ValueError("linked world file is not supported")

    def _git(self, *args: str, input: str | None = None) -> str:
        self._check_tree()
        # Discard inherited GIT_* overrides, global config, hooks and host temp paths.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        git_config_root = str(self.root / "git-env")
        env.update(XDG_CONFIG_HOME=git_config_root, TMPDIR=git_config_root + "/tmp",
                   GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
                   GIT_ATTR_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
                   GIT_INDEX_FILE=git_config_root + "/index", GIT_AUTHOR_NAME="Local business world",
                   GIT_AUTHOR_EMAIL="world@example.invalid", GIT_COMMITTER_NAME="Local business world",
                   GIT_COMMITTER_EMAIL="world@example.invalid", LC_ALL="C")
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "gc.auto=0", *args],
                                cwd=self.repo, env=env,
                                input=None if input is None else input.encode("utf-8"),
                                capture_output=True, check=True)
        return result.stdout.decode("utf-8")

    @staticmethod
    def _repository(owner, repo):
        if (owner, repo) != ("platform_admin", "mini-seed-repo"):
            raise ValueError("unknown local repository")

    def _branch(self, branch):
        ref = "refs/heads/" + branch
        self._git("check-ref-format", ref)
        return ref

    @staticmethod
    def _file_path(filePath):
        path = PurePosixPath(filePath)
        if (not filePath or path.is_absolute() or ".." in path.parts
                or any(p.lower() == ".git" for p in path.parts)
                or "\x00" in filePath or str(path) != filePath or filePath == "."):
            raise ValueError("invalid repository file path")
        return filePath

    def create_branch(self, owner, repo, branch, old_branch="main"):
        self._repository(owner, repo)
        ref = self._branch(branch)
        commit = self._git("rev-parse", "--verify", self._branch(old_branch)).strip()
        self._git("update-ref", ref, commit, "0" * 40)
        return {"owner": owner, "repo": repo, "ref": ref, "commit": commit}

    def create_or_update_file(self, owner, repo, branch_name, filePath, content, message):
        self._repository(owner, repo)
        path = self._file_path(filePath)
        ref = self._branch(branch_name)
        parent = self._git("rev-parse", "--verify", ref).strip()
        self._git("read-tree", parent)
        blob = self._git("hash-object", "-w", "--stdin", input=content).strip()
        self._git("update-index", "--add", "--cacheinfo", "100644", blob, path)
        tree = self._git("write-tree").strip()
        commit = self._git("commit-tree", tree, "-p", parent, input=message).strip()
        self._git("update-ref", ref, commit, parent)
        return {"owner": owner, "repo": repo, "ref": ref, "commit": commit,
                "parent_commit": parent, "filePath": path, "blob": blob,
                "message": message, "content": content}

    def read_git_file(self, owner, repo, filePath, branch_name="main", *, commit=None):
        self._repository(owner, repo)
        path = self._file_path(filePath)
        if commit is not None and not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("commit must be a full object ID")
        resolved = self._git("rev-parse", "--verify", (commit or self._branch(branch_name)) + "^{commit}").strip()
        blob = self._git("rev-parse", "--verify", resolved + ":" + path).strip()
        content = self._git("cat-file", "blob", blob)
        return {"owner": owner, "repo": repo, "commit": resolved,
                "filePath": path, "blob": blob, "content": content}

    def _write_json(self, path, value):
        self._check_tree()
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")

    def create_file(self, name, mimeType, parents, description, body=""):
        self._check_tree()
        note_id = uuid4().hex
        directory = self.root / "drive" / note_id
        directory.mkdir()
        value = {"id": note_id, "name": name, "mimeType": mimeType,
                 "parents": parents, "description": description, "body": body,
                 "version": 1, "ref": f"drive:{note_id}:v1"}
        self._write_json(directory / "1.json", value)
        return self.read_note(note_id)

    def _note_dir(self, note_id):
        if not re.fullmatch(r"[0-9a-f]{32}", note_id):
            raise ValueError("invalid note ID")
        self._check_tree()
        directory = self.root / "drive" / note_id
        if not directory.is_dir():
            raise FileNotFoundError(note_id)
        return directory

    def read_note(self, note_id, version=None):
        directory = self._note_dir(note_id)
        current = max(int(p.stem) for p in directory.glob("*.json"))
        if version is None:
            version = current
        if type(version) is not int or version < 1:
            raise ValueError("invalid note version")
        return json.loads((directory / f"{version}.json").read_text(encoding="utf-8"))

    def update_note(self, note_id, body, expected_version, *, validation_evidence=None):
        value = self.read_note(note_id)
        if type(expected_version) is not int or value["version"] != expected_version:
            raise StaleVersionError(f"expected {expected_version}; current {value['version']}")
        version = value["version"] + 1
        value.update(body=body, version=version, ref=f"drive:{note_id}:v{version}")
        if validation_evidence is not None:
            value["validation_evidence"] = validation_evidence
        self._write_json(self._note_dir(note_id) / f"{version}.json", value)
        return self.read_note(note_id, version)

    def create_incident(self, *, validation_evidence=None, **fields):
        incident_id = uuid4().hex
        value = {"fields": fields, "id": incident_id, "version": 1,
                 "ref": f"incident:{incident_id}:v1"}
        if validation_evidence is not None:
            value["validation_evidence"] = validation_evidence
        path = self.root / "incidents" / f"{incident_id}.json"
        self._write_json(path, value)
        return json.loads(path.read_text(encoding="utf-8"))

    def snapshot(self):
        self._check_tree()
        refs = self._git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/")
        return {"git_refs": dict(line.split() for line in refs.splitlines()),
                "notes": [json.loads(p.read_text(encoding="utf-8"))
                          for p in sorted((self.root / "drive").glob("*/*.json"))],
                "incidents": [json.loads(p.read_text(encoding="utf-8"))
                              for p in sorted((self.root / "incidents").glob("*.json"))]}
