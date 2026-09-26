"""Write the deterministic local-process profile used by the task tutorial."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from _support.profile import write_scripted_profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print(write_scripted_profile(args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
