from __future__ import annotations

from pathlib import Path

import pytest

from rpnh_rrsi.formal_cli import _validated_run_root


def test_source_checkout_rejects_run_root_inside_example() -> None:
    checkout = Path(__file__).parents[1].resolve()

    with pytest.raises(ValueError, match="outside"):
        _validated_run_root(str(checkout / "private-run"))


def test_source_checkout_rejects_run_root_elsewhere_in_repository() -> None:
    repository = Path(__file__).parents[3].resolve()

    with pytest.raises(ValueError, match="outside"):
        _validated_run_root(str(repository / "private-run"))


def test_source_checkout_preserves_external_run_root(tmp_path: Path) -> None:
    requested = tmp_path / "caller-selected-run"

    assert _validated_run_root(str(requested)) == requested.resolve()
