"""Expose the original managed dispatcher, without another version selector."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path

@contextmanager
def original_installation(root):
    from cpn.rpnh.install_v2 import read_target
    from cpn.rpnh.update_install import lifecycle_lease
    with lifecycle_lease(root, exclusive=False):
        read_target(root)  # Original metadata authority, not a second parser.
        yield

def register(root, bin_directory, *, installation=original_installation):
    root = Path(root).expanduser().absolute()
    directory = Path(bin_directory).expanduser().absolute()
    if directory != directory.resolve() or not directory.is_dir():
        raise ValueError("Select an existing real user command directory")
    if root != root.resolve():
        raise ValueError("Select the real managed root")
    target = root / "bin" / "rpnh"
    link = directory / "rpnh"
    with installation(root):
        if target.is_symlink() or not target.is_file() or not os.access(target, os.X_OK):
            raise ValueError("Original managed dispatcher is not an executable regular file")
        # symlink creation fails atomically on any existing entry. Never replace
        # a command owned by another installation/package manager.
        try:
            link.symlink_to(target)
            state = "registered"
        except FileExistsError:
            if not link.is_symlink() or os.readlink(link) != str(target):
                raise FileExistsError("Command already belongs to another entry: " + str(link))
            state = "already_registered"
    return {"status": state, "command": str(link), "dispatcher": str(target),
            "path_configuration": "not_modified", "application_activation": "not_requested"}

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--bin-directory", required=True, type=Path)
    o = p.parse_args(argv)
    result = register(o.root, o.bin_directory)
    print(result["status"] + ": " + result["command"])
    print("Shell settings unchanged. Use the full command path if its directory is not on PATH.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
