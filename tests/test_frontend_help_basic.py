from __future__ import annotations

import io
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from cpn.rpnh.basic_terminal import BasicTerminal, terminal_text
from cpn.rpnh_cli import _run_basic_frontend, _task_command


def test_small_help_keeps_complete_private_task_help(tmp_path, capsys):
    session = SimpleNamespace(root=tmp_path)
    assert _task_command(session, '/help')
    help_text = capsys.readouterr().out
    assert '/clear' in help_text and '/history' in help_text
    assert '/frontend' in help_text
    assert '/task ID' not in help_text and '/workflow PROMPT' not in help_text
    assert _task_command(session, '/help tasks')
    assert '/task ID reopen' in capsys.readouterr().out


@pytest.mark.parametrize('term,no_color,escapes', [
    ('xterm-256color', False, True), ('xterm-256color', True, False),
    ('dumb', False, False),
])
def test_terminal_capabilities(tmp_path, monkeypatch, term, no_color, escapes):
    output = io.StringIO()
    monkeypatch.setattr(output, 'isatty', lambda: True)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(sys, 'stdout', output)
    monkeypatch.setenv('TERM', term)
    if no_color:
        monkeypatch.setenv('NO_COLOR', '')
    else:
        monkeypatch.delenv('NO_COLOR', raising=False)
    BasicTerminal(tmp_path, 'test-model').welcome()
    assert ('\033[' in output.getvalue()) == escapes


def test_non_tty_output_and_trailing_backslash(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'stdin', io.StringIO('question\\\n/quit\n'))
    terminal = BasicTerminal(tmp_path, 'test-model')
    terminal.welcome()
    terminal.clear()
    with terminal.working():
        pass
    output = capsys.readouterr().out
    assert 'session:' in output and '\033' not in output
    assert 'Working' not in output
    assert terminal.read_message(focus='main', turn_state='ready') == 'question\\'
    assert terminal.read_message(focus='main', turn_state='ready') == '/quit'
    with pytest.raises(EOFError):
        terminal.read_message(focus='main', turn_state='ready')


def test_history_is_main_only_bounded_and_control_safe(tmp_path, capsys):
    messages = [('user', f'message-{i:02}') for i in range(14)]
    messages[-1] = ('user', 'malicious\033]52;c;secret\a\nnext')
    original = list(messages)
    BasicTerminal(tmp_path, 'model').history(messages)
    output = capsys.readouterr().out
    assert 'Recent main conversation' in output
    assert 'message-03' not in output and 'message-04' in output
    assert '\033' not in output and '\a' not in output and '\nnext' in output
    assert messages == original
    assert '\n' not in terminal_text('path\nother')
    assert '\u202e' not in terminal_text('model\u202eevil')


def test_readline_completion_and_history_restore(tmp_path, monkeypatch):
    readline = pytest.importorskip('readline')
    terminal = BasicTerminal(tmp_path, 'test-model')
    terminal.interactive = True
    old_completer, old_delimiters = readline.get_completer(), readline.get_completer_delims()
    old_history = [readline.get_history_item(i) for i in range(1, readline.get_current_history_length() + 1)]
    with terminal:
        monkeypatch.setattr(readline, 'get_line_buffer', lambda: '/h')
        monkeypatch.setattr(readline, 'get_begidx', lambda: 0)
        assert terminal._complete('/h', 0) == '/help'
        assert terminal._complete('/h', 1) == '/history'
        assert terminal._complete('/task', 0) is None
        monkeypatch.setattr(readline, 'get_line_buffer', lambda: '/help v')
        assert terminal._complete('v', 0) == 'viewer'
        readline.add_history('private prompt')
    assert readline.get_completer() is old_completer
    assert readline.get_completer_delims() == old_delimiters
    assert [readline.get_history_item(i) for i in range(1, readline.get_current_history_length() + 1)] == old_history


def _basic_fixture(tmp_path, monkeypatch, answers):
    root = tmp_path / 'session'
    lease = SimpleNamespace(closed=False)
    def close():
        lease.closed = True
    lease.close = close
    class Lease:
        @staticmethod
        def reserve_for_creation(_root):
            assert _root == root
            return lease
    control = SimpleNamespace(get=lambda task: SimpleNamespace(task_id=task),
                              status=lambda task: {'task_id': task, 'status': 'RUNNING'})
    session = SimpleNamespace(root=root, history=[], task_control=control,
                              active_turn_snapshot=lambda: None)
    monkeypatch.setattr('cpn.rpnh.session_access.MainSessionOwnerLease', Lease)
    monkeypatch.setattr('cpn.rpnh.session_frontend.remember_session_frontend', lambda *_a, **_k: None)
    monkeypatch.setattr('cpn.rpnh_cli.MainSession', lambda *_a, **_k: session)
    submitted = []
    monkeypatch.setattr('cpn.rpnh_cli._run_turn', lambda _session, message: submitted.append(message) or True)
    iterator = iter(answers)
    def read(_prompt):
        value = next(iterator)
        if isinstance(value, BaseException):
            raise value
        return value
    monkeypatch.setattr('builtins.input', read)
    return root, lease, session, submitted


def test_basic_help_errors_controls_and_literal_input_do_not_call_model(tmp_path, monkeypatch, capsys):
    root, lease, session, submitted = _basic_fixture(tmp_path, monkeypatch, [
        '/status', '/history', '/clear', '/help viewer', '/help tasks',
        '/help viewer --unknown', '/help "unterminated', '/help --help',
        '/unknown', '//tmp/source', '/switch child', '/status', '/quit'])
    assert _run_basic_frontend(SimpleNamespace(resume=None, prompt=None), root, tmp_path/'profile.json') == 0
    output = capsys.readouterr()
    assert 'Turn      ready' in output.out and '"task_id": "child"' in output.out
    assert 'unknown command; use /help' in output.err and 'unmatched quote' in output.err
    assert submitted == ['/tmp/source']
    assert session.history == [] and lease.closed


def test_private_ctrl_c_reminder_and_eof_release(tmp_path, monkeypatch, capsys):
    root, lease, _, submitted = _basic_fixture(tmp_path, monkeypatch, [KeyboardInterrupt(), EOFError()])
    assert _run_basic_frontend(SimpleNamespace(resume=None, prompt=None), root, tmp_path/'profile.json') == 0
    assert 'Use /quit to leave' in capsys.readouterr().out
    assert submitted == [] and lease.closed


def test_terminal_multiline_and_exit_alias(tmp_path, monkeypatch):
    root, lease, _, submitted = _basic_fixture(tmp_path, monkeypatch, ['first\\', '第二行', '/exit'])
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    assert _run_basic_frontend(SimpleNamespace(resume=None, prompt=None), root, tmp_path/'profile.json') == 0
    assert submitted == ['first\n第二行'] and lease.closed


def test_one_shot_preserves_output_and_bypasses_line_editor(tmp_path, monkeypatch, capsys):
    root, lease, _, submitted = _basic_fixture(tmp_path, monkeypatch, [])
    monkeypatch.setattr(BasicTerminal, '__enter__', lambda *_: pytest.fail('line editor'))
    assert _run_basic_frontend(SimpleNamespace(resume=None, prompt='/literal'), root, tmp_path/'profile.json') == 0
    assert capsys.readouterr().out == f'RPNH — AI assistant\nsession: {root}\nType /help for task controls.\n'
    assert submitted == ['/literal'] and lease.closed
