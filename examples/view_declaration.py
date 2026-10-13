"""Open a bundled initial declaration in the installed read-only RPNH Viewer.

Run from the repository root in an environment with RPNH already installed:
    python examples/view_declaration.py rsi-four-round

This reads a public static frame. It does not run the example or open a Registry.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


_EXAMPLES = Path(__file__).resolve().parent
_DECLARATIONS = {
    "rsi-four-round": "rsi_workflows/figures/four-round/declaration-frame.json",
    "rrsi-policy": "rrsi_v06/figures/policy/declaration-frame.json",
    "rrsi-analyst": "rrsi_v06/figures/analyst/declaration-frame.json",
    "rrsi-proposer": "rrsi_v06/figures/proposer/declaration-frame.json",
    "rrsi-critic": "rrsi_v06/figures/critic/declaration-frame.json",
    "rrsi-digester": "rrsi_v06/figures/digester/declaration-frame.json",
    "collaboration-baseline": "user_collaboration/figures/baseline/declaration-frame.json",
    "collaboration-adopted": "user_collaboration/figures/adopted-M2/declaration-frame.json",
    "tool-pipeline": "tool_pipeline/figures/declaration-frame.json",
    "parallel": "workflow_patterns/figures/parallel/declaration-frame.json",
    "automationbench": "automationbench/figures/declaration-frame.json",
    "erp-bench": "erp_bench/figures/declaration-frame.json",
    "slopcodebench": "slopcodebench/figures/declaration-frame.json",
    "harnessaudit-office": "harnessaudit_office/figures/declaration-frame.json",
    "iteration-profile": "../cpn/examples/iteration_profile/figures/iteration-profile/declaration-frame.json",
    "compose-serial": "net_operations/figures/compose-serial/declaration-frame.json",
}


class DeclarationFixture:
    """Read-only provider for one of the bundled initial dashboard frames."""

    def __init__(self, example: str) -> None:
        if example not in _DECLARATIONS:
            raise ValueError("unknown bundled declaration")
        self._frame = json.loads(
            (_EXAMPLES / _DECLARATIONS[example]).read_text(encoding="utf-8"))
        if (self._frame["schema_version"] != "rpnh/dashboard/v1"
                or self._frame["source"]["mode"] != "initial_configured"
                or self._frame["net"]["source"]["mode"] != "initial_configured"):
            raise ValueError("expected a bundled initial_configured dashboard frame")

    def __call__(self) -> dict[str, Any]:
        return self._frame["net"]

    def dashboard(self, cursor: int | None = None) -> dict[str, Any]:
        if cursor is not None:
            raise ValueError("Initial declaration fixtures have no execution history")
        return self._frame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("example", choices=tuple(_DECLARATIONS))
    parser.add_argument(
        "--no-open", action="store_true", help="print the local URL without opening a browser")
    args = parser.parse_args(argv)
    try:
        provider = DeclarationFixture(args.example)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(f"could not read the bundled declaration: {exc}")

    from cpn.frontend.server import serve_projection

    # The existing server prints the actual URL after binding an available port.
    serve_projection(provider, host="127.0.0.1", port=0, open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
