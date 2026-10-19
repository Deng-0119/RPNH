"""Small, dependency-free presentation layer for the built-in terminal."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import sys
from typing import Iterator


BASIC_COMMANDS = (
    ("/help", "show basic commands"),
    ("/clear", "clear the screen, keeping this conversation"),
    ("/status", "show session, model and current focus"),
    ("/history", "show the last 10 conversation messages"),
    ("/frontend [NAME]", "choose or switch the interface for this session"),
    ("/resume", "continue a paused turn or settle its result"),
    ("/rollback", "return a paused main turn to the prior completed turn"),
    ("/quit", "leave the interface; /exit also works"),
)


class BasicTerminal:
    """Render ordinary scrollback and use the platform line editor when present.

    History stays in memory: no second conversation store or shell history file.
    Non-terminal input continues to use the same plain input protocol.
    """

    def __init__(self, root: Path, model: str) -> None:
        self.root, self.model = root, model
        self.input_terminal = sys.stdin.isatty()
        self.interactive = (
            self.input_terminal and sys.stdout.isatty()
            and os.environ.get("TERM", "") != "dumb")
        self.color = self.interactive and "NO_COLOR" not in os.environ
        self._readline = None
        self._saved_completer = None
        self._saved_delimiters = ""
        self._saved_history: list[str] = []

    def _style(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    @property
    def width(self) -> int:
        return max(12, min(shutil.get_terminal_size((80, 24)).columns, 88))

    def welcome(self) -> None:
        if not self.interactive:
            print("RPNH — AI assistant")
            print(f"session: {self.root}")
            print("Type /help for basic commands.")
            return
        print()
        print(self._style("╭─ RPNH · Basic " + "─" * max(0, self.width - 17) + "╮", "36"))
        print("  " + self._style("AI assistant", "1"))
        print(f"  Model    {self.model}")
        print(f"  Folder   {Path.cwd()}")
        print(f"  Session  {self.root}")
        print(self._style("╰" + "─" * (self.width - 2) + "╯", "36"))
        print("  Send a message to begin. /help for commands · /frontend to switch")

    def help(self) -> None:
        print(self._style("Basic commands", "1;36"))
        for command, description in BASIC_COMMANDS:
            print(f"  {command:<20} {description}")
        print("\nEnter sends · trailing \\ continues on another line")
        print("Up/Down recalls input · Tab completes commands (with readline)")
        print("Ctrl+C interrupts · Ctrl+D leaves at an empty prompt")
        print("Task controls: /help tasks")

    def clear(self) -> None:
        if self.interactive:
            print("\033[2J\033[H", end="", flush=True)
            self.welcome()
        else:
            print("Screen clearing is available in an interactive terminal.")

    def status(self, *, focus: str, turn_state: str) -> None:
        print(f"Frontend  basic\nModel     {self.model}\nSession   {self.root}")
        print(f"Focus     {focus}\nTurn      {turn_state}")

    def history(self, messages: list[tuple[str, str]]) -> None:
        recent = messages[-10:]
        if not recent:
            print("No conversation messages yet.")
            return
        print(self._style("Recent conversation", "1;36"))
        for role, body in recent:
            print(self._style(f"\n{role.capitalize()}", "1"))
            print(body)

    def _complete(self, text: str, state: int) -> str | None:
        line = self._readline.get_line_buffer().lstrip()
        if line.startswith("/frontend "):
            candidates = ("basic", "codex", "opencode", "dsh")
        elif self._readline.get_begidx() == 0:
            candidates = tuple(command.split()[0] for command, _ in BASIC_COMMANDS)
        else:
            candidates = ()
        matches = [candidate for candidate in candidates if candidate.startswith(text)]
        return matches[state] if state < len(matches) else None

    def __enter__(self) -> BasicTerminal:
        if not self.interactive:
            return self
        try:
            import readline
        except ImportError:
            return self
        self._readline = readline
        self._saved_completer = readline.get_completer()
        self._saved_delimiters = readline.get_completer_delims()
        self._saved_history = [
            readline.get_history_item(index) or ""
            for index in range(1, readline.get_current_history_length() + 1)]
        readline.clear_history()
        readline.set_completer_delims(" \t\n")
        readline.set_completer(self._complete)
        if "libedit" in (readline.__doc__ or ""):
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
        return self

    def __exit__(self, *_args) -> None:
        if self._readline is not None:
            self._readline.set_completer(self._saved_completer)
            self._readline.set_completer_delims(self._saved_delimiters)
            self._readline.clear_history()
            for line in self._saved_history:
                self._readline.add_history(line)

    def read_message(self, *, focus: str, turn_state: str) -> str:
        if self.interactive:
            print()
            print(self._style("─" * self.width, "2"))
            print(self._style(f"  basic · {focus} · {turn_state}", "2"))
            # Readline needs nonprinting delimiters around ANSI prompt bytes.
            prompt = ("\001\033[36m\002›\001\033[0m\002 "
                      if self.color else "› ")
        else:
            prompt = f"RPNH[{focus}] › "
        lines: list[str] = []
        while True:
            line = input(prompt if not lines else "… ")
            continued = self.input_terminal and line.endswith("\\")
            lines.append(line[:-1] if continued else line)
            if not continued:
                return "\n".join(lines).strip()

    @contextmanager
    def working(self) -> Iterator[None]:
        if self.interactive:
            print(self._style("\n● Working…  Ctrl+C to interrupt", "2"), flush=True)
        yield
