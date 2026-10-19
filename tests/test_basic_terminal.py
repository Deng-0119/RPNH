from __future__ import annotations

import io
from pathlib import Path
import sys

import pytest

from cpn.rpnh.basic_terminal import BasicTerminal
from cpn.rpnh.main_session import MainSession
from cpn.rpnh.session_access import MainSessionOwnerLease
from cpn.rpnh_cli import _task_command, main
from test_main_session_registry import _write_execution_profile


def test_default_help_is_small_and_task_help_remains_available(tmp_path, capsys):
    session = type("Session", (), {"root": tmp_path})()
    assert _task_command(session, "/help")
    help_text = capsys.readouterr().out
    assert "/clear" in help_text and "/history" in help_text
    assert "/frontend" in help_text and "/resume" in help_text
    assert "/task ID" not in help_text and "/workflow PROMPT" not in help_text
    assert _task_command(session, "/help tasks")
    assert "/task ID reopen" in capsys.readouterr().out


@pytest.mark.parametrize("term,no_color,escapes", [
    ("xterm-256color", False, True), ("xterm-256color", True, False),
    ("dumb", False, False),
])
def test_terminal_capabilities_control_welcome_rendering(
        tmp_path, monkeypatch, term, no_color, escapes):
    output = io.StringIO()
    monkeypatch.setattr(output, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setenv("TERM", term)
    if no_color:
        monkeypatch.setenv("NO_COLOR", "")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    terminal = BasicTerminal(tmp_path, "test-model")
    terminal.welcome()
    assert ("\033[" in output.getvalue()) == escapes
    if term != "dumb":
        assert "test-model" in output.getvalue()


def test_redirected_output_has_no_terminal_controls(tmp_path, capsys):
    terminal = BasicTerminal(tmp_path, "test-model")
    terminal.welcome()
    terminal.clear()
    with terminal.working():
        pass
    output = capsys.readouterr().out
    assert "session:" in output and "\033" not in output
    assert "Working" not in output


def test_multiline_input_is_submitted_once(tmp_path, monkeypatch, capsys):
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "session"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(("first line\\", "第二行", "/unknown-command", "/exit"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    submitted = []
    monkeypatch.setattr(
        "cpn.rpnh_cli._run_turn", lambda session, text: submitted.append(text))
    assert main(["--frontend", "basic", "--execution", str(execution),
                 "--session-dir", str(root)]) == 0
    assert submitted == ["first line\n第二行"]
    assert "unknown command; use /help" in capsys.readouterr().err
    with MainSessionOwnerLease(root):
        pass


def test_piped_trailing_backslash_preserves_line_and_next_command(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("question\\\n/quit\n"))
    terminal = BasicTerminal(tmp_path, "test-model")
    assert terminal.read_message(focus="main", turn_state="ready") == "question\\"
    assert terminal.read_message(focus="main", turn_state="ready") == "/quit"
    with pytest.raises(EOFError):
        terminal.read_message(focus="main", turn_state="ready")


def test_display_commands_do_not_modify_session_or_call_model(
        tmp_path, monkeypatch, capsys):
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "session"
    answers = iter(("/status", "/history", "/clear", "/help", "/quit"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(MainSession, "turn", lambda *_a, **_kw: pytest.fail("model call"))
    assert main(["--frontend", "basic", "--execution", str(execution),
                 "--session-dir", str(root)]) == 0
    output = capsys.readouterr().out
    assert "offline-main-session-test" in output
    assert "Turn      ready" in output and "No conversation messages yet." in output
    session = MainSession.resume(root, execution)
    assert session.history == [] and session.active_turn_snapshot() is None
    with MainSessionOwnerLease(root):
        pass


def test_recent_history_display_is_bounded_without_mutating_messages(tmp_path, capsys):
    messages = [("user", f"message-{i:02}") for i in range(14)]
    original = list(messages)
    BasicTerminal(tmp_path, "test-model").history(messages)
    output = capsys.readouterr().out
    assert "message-03" not in output and "message-04" in output
    assert "message-13" in output and messages == original


def test_readline_completion_and_in_memory_history_are_scoped(tmp_path, monkeypatch):
    readline = pytest.importorskip("readline")
    terminal = BasicTerminal(tmp_path, "test-model")
    terminal.interactive = True
    old_completer, old_delimiters = readline.get_completer(), readline.get_completer_delims()
    old_history = [readline.get_history_item(i) for i in range(1, readline.get_current_history_length() + 1)]
    with terminal:
        assert readline.get_completer_delims() == " \t\n"
        monkeypatch.setattr(readline, "get_line_buffer", lambda: "/h")
        monkeypatch.setattr(readline, "get_begidx", lambda: 0)
        assert terminal._complete("/h", 0) == "/help"
        assert terminal._complete("/h", 1) == "/history"
        assert terminal._complete("/task", 0) is None
        monkeypatch.setattr(readline, "get_line_buffer", lambda: "/frontend d")
        assert terminal._complete("d", 0) == "dsh"
        readline.add_history("private prompt")
    assert readline.get_completer() is old_completer
    assert readline.get_completer_delims() == old_delimiters
    assert [readline.get_history_item(i) for i in range(1, readline.get_current_history_length() + 1)] == old_history


def test_one_shot_preserves_plain_output_and_does_not_open_line_editor(
        tmp_path, monkeypatch, capsys):
    execution = _write_execution_profile(tmp_path)
    monkeypatch.setattr(BasicTerminal, "__enter__", lambda *_: pytest.fail("line editor"))
    monkeypatch.setattr("cpn.rpnh_cli._run_turn", lambda *_: True)
    assert main(["--prompt", "hello", "--execution", str(execution),
                 "--session-dir", str(tmp_path / "session")]) == 0
    output = capsys.readouterr().out
    assert "RPNH — AI assistant" in output
    assert "\033" not in output and "Basic commands" not in output
