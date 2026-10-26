from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cpn.rpnh import help_catalog as helper
from cpn.rpnh_cli import main, _net_command


def test_help_bypasses_profile_session_and_model(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('cpn.rpnh_cli.resolve_execution_path', lambda *_a, **_k: pytest.fail('profile'))
    monkeypatch.setattr('cpn.rpnh_cli.MainSession', lambda *_a, **_k: pytest.fail('session'))
    assert main(['help', 'viewer', '--language', 'zh-CN']) == 0
    assert '看懂当前 Viewer' in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_json_and_untrusted_argument_errors(capsys):
    assert helper.help_command(['viewer', '--json']) == 0
    value = json.loads(capsys.readouterr().out)
    assert value['topic']['id'] == 'viewer'
    for arguments in [['\033]52;c;secret\a', '--json'], ['viewer','--json','--bad'], ['--language','xx','--json'],
                      ['viewer', '--json', '--lang', 'zh-CN']]:
        assert helper.help_command(arguments) == 2
        output = capsys.readouterr()
        assert json.loads(output.out)['error']['code'] == 'help_usage'
        assert output.err == '' and '\033' not in output.out and 'secret' not in output.out


def test_catalog_is_bounded_bilingual_and_detached():
    catalog = helper.help_catalog()
    assert catalog['schema'] == 'rpnh/help/v1'
    assert len(catalog['topics']) == 13
    catalog['topics'][0]['title']['en'] = 'mutated'
    assert helper.help_catalog()['topics'][0]['title']['en'] != 'mutated'
    for topic in helper.help_catalog()['topics']:
        assert set(topic['title']) == {'en','zh-CN'}
        for field in ('source_paths','docs'):
            for path in topic[field]:
                assert Path(path).is_file(), path


@pytest.mark.parametrize('payload', [b'{private secret', b'x'*65537, b'\xff', b'{"schema":"wrong","topics":[]}'])
def test_bad_catalog_safe_fallback(payload, monkeypatch, capsys):
    class Resource:
        def joinpath(self, *_parts):
            return self
        def open(self, _mode):
            return io.BytesIO(payload)
    helper._loaded_catalog.cache_clear()
    monkeypatch.setattr(helper, 'files', lambda _package: Resource())
    try:
        assert helper.help_command(['viewer', '--json']) == 2
        output = capsys.readouterr()
        error = json.loads(output.out)['error']
        assert error['code'] == 'help_unavailable'
        assert 'secret' not in output.out and 'Traceback' not in output.err
        assert helper.help_topics() == ()
    finally:
        helper._loaded_catalog.cache_clear()


@pytest.mark.parametrize('timeout', ['nan','inf','-1'])
def test_net_timeout_rejects_nonfinite_before_loading_run(timeout):
    with pytest.raises(ValueError, match='finite and non-negative'):
        _net_command(['--run','missing','--view','--session-timeout',timeout])


def test_net_timeout_is_forwarded(monkeypatch):
    seen = []
    monkeypatch.setattr('cpn.cli.main', lambda argv: seen.append(argv) or 0)
    assert _net_command(['--run','missing','--view','--session-timeout','12']) == 0
    assert seen[0][-2:] == ['--session-timeout','12.0']
