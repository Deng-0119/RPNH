"""Prepare canonical cold/pending roots once for both version lanes; no client runs."""
from pathlib import Path
import argparse
import json
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New synthetic parent directory")
    args = parser.parse_args()
    root = args.output.absolute()
    root.mkdir(parents=True, exist_ok=False)
    prepare = Path(__file__).with_name("prepare_fixture.py")
    for name, turns, extra in (("cold", 60, []), ("pending", 6, ["--pending"])):
        subprocess.run([sys.executable, str(prepare), "--repo", str(args.repo.resolve()),
                        "--output", str(root / name), "--turns", str(turns), *extra], check=True)
    (root / "pair.json").write_text(json.dumps({
        "schema": "rpnh/codex_candidate_fixture_pair/v1",
        "canonical_root": str(root.resolve()),
        "fixtures": {"cold": "cold", "pending": "pending"},
        "note": "Do not copy or move roots between lanes: display identity binds canonical path.",
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
