"""Explicit portable checkout location for independent-review fixture imports."""
import os
from pathlib import Path
value = os.environ.get('RPNH_SOURCE')
if not value:
    raise RuntimeError('Set RPNH_SOURCE to the applied authoritative RPNH checkout')
SOURCE = Path(value).resolve()
if not (SOURCE / 'cpn').is_dir() or not (SOURCE / 'tests/test_static_lease_reads.py').is_file():
    raise RuntimeError('RPNH_SOURCE is not an applied static-lease candidate checkout')
