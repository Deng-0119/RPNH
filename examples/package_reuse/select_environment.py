"""Fill a complete receiver selection from the shipped package's exact identity.

Only local JSON and inert ZIP declarations are read. This does not import a
plugin, install dependencies, approve a plan, or execute the package.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from cpn.rpnh.collaboration.environment_local_contracts import EnvironmentSelection
from cpn.rpnh.collaboration.environment_requirements import read_package_environment
from cpn.rpnh.collaboration.share_packages import PackageResolutionLock

ROOT = Path(__file__).resolve().parent


def make_selection(*, executable, prefix=None, archive=ROOT / "native-add-v2.zip",
                   lock=ROOT / "native-add-v2.lock.json", configuration=ROOT / "plugins.json"):
    requirements = read_package_environment(archive, package_lock=PackageResolutionLock(Path(lock).read_bytes()))
    root = next(item for item in requirements.requirements if item.manifest_digest == requirements.target.to_dict()["root_manifest_digest"])
    return EnvironmentSelection.from_dict({
        "schema_version": "rpnh/environment_selection/v1", "selection_id": "native-add-" + ("existing" if prefix is None else "new-venv"),
        "target": requirements.target.to_dict(), "mode": "existing" if prefix is None else "new_venv",
        "python_selection": {"executable": str(Path(executable).absolute())} if prefix is None else
            {"base_executable": str(Path(executable).absolute()), "prefix": str(Path(prefix).absolute())},
        "host_profile_id": "rpnh-native/v1", "tools": [], "services": [],
        "plugins": [{"scoped_requirement_id": root.scoped_id("demo-plugin"), "plugin_id": "demo",
                     "configuration_ref": str(Path(configuration).resolve())}],
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="existing interpreter or base interpreter for --prefix")
    parser.add_argument("--prefix", type=Path, help="absent absolute destination for a new venv; omit for existing")
    parser.add_argument("--archive", type=Path, default=ROOT / "native-add-v2.zip")
    parser.add_argument("--lock", type=Path, default=ROOT / "native-add-v2.lock.json")
    parser.add_argument("--config", type=Path, default=ROOT / "plugins.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.prefix is not None and args.prefix.exists():
        parser.error("--prefix must be absent; use existing mode for an installed environment")
    result = make_selection(executable=args.python, prefix=args.prefix, archive=args.archive,
                            lock=args.lock, configuration=args.config)
    with args.output.open("xb") as stream:
        args.output.chmod(0o600)
        stream.write(result.to_bytes())
    print(str(args.output.absolute()))


if __name__ == "__main__":
    main()
