"""Choose and connect a presentation before opening its main session."""
from __future__ import annotations

from typing import Callable

from cpn.rpnh.frontend_config import read_frontend_preferences
from cpn.rpnh.onboarding import SetupCancelled


FRONTEND_LABELS = {
    "codex": "Codex terminal",
    "opencode": "OpenCode terminal",
    "dsh": "DSH official Web",
    "basic": "Basic built-in terminal",
}


class FrontendSwitch(Exception):
    """Leave the current owner before opening another presentation."""

    def __init__(self, frontend: str) -> None:
        super().__init__(frontend)
        self.frontend = frontend


def choose_frontend(
        frontend: str | None = None, *,
        input_fn: Callable[[str], str] | None = None,
        output_fn: Callable[[str], None] | None = None,
        **bindings,
) -> str:
    from cpn.rpnh.frontend_setup import configure_frontend, frontend_status

    read, write = input_fn or input, output_fn or print
    if frontend is not None:
        if frontend not in FRONTEND_LABELS:
            raise ValueError("Choose a frontend: " + ", ".join(FRONTEND_LABELS))
        configure_frontend(frontend, output_fn=write, **bindings)
        return frontend

    preference = read_frontend_preferences().frontend
    choices = tuple(FRONTEND_LABELS)
    statuses = {name: frontend_status(name) for name in choices}
    default = preference if preference in choices else (
        "codex" if statuses["codex"]["available"] else "basic")
    write("Choose an RPNH frontend. Missing clients will be installed and connected.")
    for i, name in enumerate(choices, 1):
        status = "ready" if statuses[name]["available"] else "setup needed"
        write(f"  {i}. {FRONTEND_LABELS[name]} ({name}; {status})")
    while True:
        try:
            answer = read(f"Frontend [{default}]: ").strip().lower() or default
        except (EOFError, KeyboardInterrupt) as exc:
            raise SetupCancelled("Frontend selection cancelled; no session opened.") from exc
        if answer.isascii() and answer.isdecimal() and 1 <= int(answer) <= len(choices):
            answer = choices[int(answer) - 1]
        if answer not in choices:
            write("Enter a frontend name or its number.")
            continue
        try:
            configure_frontend(answer, output_fn=write, **bindings)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            write(f"Could not configure {answer}: {exc}")
            write("Choose another frontend or press Ctrl+C to cancel.")
            continue
        return answer
