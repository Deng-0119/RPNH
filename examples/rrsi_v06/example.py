#!/usr/bin/env python3
"""Source-checkout entrypoint for the RRSI v0.6 application example."""
from __future__ import annotations

from pathlib import Path
import sys


SOURCE = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SOURCE))

from rpnh_rrsi.formal_cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
