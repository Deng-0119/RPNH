"""Independent G1: no sockets, PTYs, native subprocesses, installs or providers."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import ast
import json
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "source"), str(ROOT / "source/tests")]
from cpn.frontend import opencode_protocol as p
from cpn.frontend import opencode_launcher as launcher
from test_opencode_frontend import ApplicationDouble, LocalGateway


def test_manifest_existing_contract_byte_values_preserved():
    baseline = json.loads((ROOT / 'baseline/cpn/frontend/opencode_compatibility.v1.json').read_text())
    candidate = json.loads((ROOT / 'source/cpn/frontend/opencode_compatibility.v1.json').read_text())
    new_records = candidate.pop('certification_candidates')
    assert candidate == baseline
    assert len(new_records) == 1
    assert new_records[0]['package_version'] == '1.18.35'
    assert new_records[0]['native_g2'] == new_records[0]['native_g3'] == 'not-run'
    assert new_records[0]['production_enabled'] is False


def test_no_unrelated_protocol_method_changes():
    def methods(tree):
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'OpenCodeProtocol')
        return {node.name: ast.dump(node, include_attributes=False) for node in cls.body if isinstance(node, ast.FunctionDef)}
    old = methods(ast.parse((ROOT / 'baseline/cpn/frontend/opencode_protocol.py').read_text()))
    new = methods(ast.parse((ROOT / 'source/cpn/frontend/opencode_protocol.py').read_text()))
    assert new.keys() - old.keys() == {'profile'}
    assert {name for name in old if old[name] != new[name]} == {'__init__', 'session_document', '_get_route'}


@pytest.mark.parametrize('invalid', ['latest', '1.18', '>=1.18.35', '1.18.35-beta', '1.18.35+build', '1.18.35\n', '', True, 1.1835, {}, '1.18.32'])
def test_factory_fails_closed(invalid):
    with pytest.raises(ValueError):
        p.get_opencode_profile(certification_version=invalid)


def test_default_and_candidate_immutable_and_isolated():
    default = p.get_opencode_profile()
    candidate = p.get_opencode_profile(certification_version='1.18.35')
    assert default is p.DEFAULT_PROFILE
    assert (default.version, default.commit, default.certification_only) == ('1.18.32', '545f51d26cc39a907d2867492d498d9607ea5fa4', False)
    assert (candidate.version, candidate.commit, candidate.certification_only) == ('1.18.35', '53d1eabb61e21162157817bf677da0a4ad3332e3', True)
    instances = []
    for profile in (default, candidate):
        with pytest.raises(FrozenInstanceError):
            profile.version = '1.18.99'
        app = ApplicationDouble()
        app.create_session()
        protocol = p.OpenCodeProtocol(LocalGateway(app), '/isolated/test', profile=profile)
        instances.append(protocol)
        assert protocol.profile is profile
        for route in ('/health', '/global/health'):
            assert protocol.route('GET', route).body == {'healthy': True, 'version': profile.version}
        assert protocol.route('GET', '/session').body[0]['version'] == profile.version
        with pytest.raises(AttributeError):
            protocol.profile = default
    instances[1].close()
    assert instances[0].route('GET', '/global/health').body['version'] == '1.18.32'
    assert p.OPENCODE_VERSION == '1.18.32'


@pytest.mark.parametrize('field,value', [('commit', '0'*40), ('version', '1.18.32'), ('certification_only', False), ('certification_only', 1), ('version', True)])
def test_mixed_profile_rejected_before_probe_or_gateway(field, value, tmp_path):
    candidate = p.get_opencode_profile(certification_version='1.18.35')
    bad = replace(candidate, **{field: value})
    with patch.object(launcher.subprocess, 'run', side_effect=AssertionError('native process forbidden')):
        with pytest.raises(ValueError):
            launcher.check_version('/not-executed', {}, tmp_path, profile=bad)
    gateway = NS(call=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('application touched')))
    with pytest.raises(ValueError):
        p.OpenCodeProtocol(gateway, '/not-used', profile=bad)


@pytest.mark.parametrize('version', ['1.18.32','1.18.35'])
@pytest.mark.parametrize('suffix,code', [('-dev',0), ('+local',0), (' extra',0), ('\n1.18.32',0), ('',1)])
def test_probe_rejects_extra_or_nonzero(version,suffix,code,tmp_path):
    profile = p.DEFAULT_PROFILE if version == '1.18.32' else p.get_opencode_profile(certification_version=version)
    with patch.object(launcher.subprocess, 'run', return_value=NS(stdout=version+suffix, returncode=code)):
        with pytest.raises(RuntimeError):
            launcher.check_version('/not-executed', {}, tmp_path, profile=profile)


def test_probe_timeout_and_default_rejects_candidate(tmp_path):
    with patch.object(launcher.subprocess, 'run', side_effect=subprocess.TimeoutExpired('not-executed',10)):
        with pytest.raises(RuntimeError, match='timed out'):
            launcher.check_version('/not-executed', {}, tmp_path)
    with patch.object(launcher.subprocess, 'run', return_value=NS(stdout='1.18.35\n',returncode=0)):
        with pytest.raises(RuntimeError, match='1.18.32'):
            launcher.check_version('/not-executed', {}, tmp_path)


@pytest.mark.parametrize('platform', ['linux', 'win32', 'darwin'])
def test_production_rejects_candidate_before_owner_and_ignores_env(platform,tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError('owner or listener must not be created')
    with patch.object(launcher.sys,'platform',platform), patch.object(launcher.shutil,'which',return_value='/not-executed'), patch.object(launcher.subprocess,'run',return_value=NS(stdout='1.18.35',returncode=0)) as run, patch.object(launcher,'FrontendGateway',side_effect=forbidden), patch.object(launcher,'OpenCodeHTTPServer',side_effect=forbidden), patch.dict(launcher.os.environ,{'OPENCODE_CERTIFY_VERSION':'1.18.35','RPNH_OPENCODE_VERSION':'1.18.35'}):
        with pytest.raises(RuntimeError):
            launcher.run_opencode_frontend(tmp_path/'root',tmp_path/'execution')
        assert run.call_count == (1 if platform == 'linux' else 0)
