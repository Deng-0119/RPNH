"""Explicit local Skill files into original OwnerInput and actual Registry/PN.

The parser, filesystem, original publisher, Success and delivery are real. No
directory discovery, script execution, parser substitute or permission fake is
used. The one PN case drives the original Owner directly, as the existing Skill
integration fixture does; it is not a native owner-listener test.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest
import strictyaml  # The production dependency is required, never substituted.

from cpn.rpnh import skill_materials as skill
from cpn.rpnh.agent_workflows import TEXT_SCHEMA
from cpn.rpnh.collaboration.references import SourceQualifiedResourceRef
from cpn.rpnh.material_types import decode_agent_text
import test_skill_native_integration as native


RAW = (
    '---\r\nname: read-guide\r\ndescription: Read this exact guide.\r\n'
    'allowed-tools: Bash(*)\r\n---\r\n# Guide\r\n保留原文。\r\n'
).encode('utf-8')
REFERENCE = '精确参考资料\r\n'.encode('utf-8')
SCRIPT = b'raise AssertionError("selected script must remain text")\n'
PORTS = {'SKILL.md': 'request', 'references/details.md': 'detail', 'scripts/worker.py': 'script'}


@pytest.fixture
def directory(tmp_path):
    root = tmp_path / 'read-guide'
    (root / 'references').mkdir(parents=True)
    (root / 'scripts').mkdir()
    (root / 'SKILL.md').write_bytes(RAW)
    (root / 'references/details.md').write_bytes(REFERENCE)
    script = root / 'scripts/worker.py'
    script.write_bytes(SCRIPT)
    script.chmod(0o755)
    # An invalid unselected member must not be discovered or imported.
    (root / 'unselected.bin').write_bytes(b'\xff')
    return root


def load(root, ports=PORTS, *, max_bytes=4096):
    return skill.prepare_directory_member_inputs(root, ports, max_bytes=max_bytes)


def text(input_):
    return decode_agent_text(input_.payload, content_schema_ref=TEXT_SCHEMA,
                             media_type='application/json')


def test_exact_selected_bytes_and_total_limit_return_original_inputs(directory):
    document, inputs = load(directory, max_bytes=len(RAW) + len(REFERENCE) + len(SCRIPT))
    assert document.text.encode('utf-8') == RAW
    assert document.metadata['allowed-tools'] == 'Bash(*)'
    assert set(inputs) == {'request', 'detail', 'script'}
    assert text(inputs['request']).encode('utf-8') == RAW
    assert text(inputs['detail']).encode('utf-8') == REFERENCE
    assert text(inputs['script']).encode('utf-8') == SCRIPT


@pytest.mark.parametrize('limit', [0, -1, True, len(RAW) + len(REFERENCE) + len(SCRIPT) - 1])
def test_total_limit_is_exact_and_invalid_bounds_fail(directory, limit):
    with pytest.raises(skill.SkillMaterialError, match='byte limit'):
        load(directory, max_bytes=limit)


@pytest.mark.parametrize('where', ['root', 'ancestor', 'member_directory', 'member'])
def test_every_selected_path_component_refuses_symlinks(directory, where):
    outside = directory.parent / 'outside'
    outside.mkdir()
    (outside / 'details.md').write_bytes(REFERENCE)
    selected = directory
    ports = {'SKILL.md': 'request'}
    if where == 'root':
        selected = directory.parent / 'root-link'
        selected.symlink_to(directory, target_is_directory=True)
    elif where == 'ancestor':
        alias = directory.parent / 'ancestor-link'
        alias.symlink_to(directory.parent, target_is_directory=True)
        selected = alias / directory.name
    elif where == 'member_directory':
        (directory / 'linked').symlink_to(outside, target_is_directory=True)
        ports['linked/details.md'] = 'detail'
    else:
        (directory / 'references/link.md').symlink_to(outside / 'details.md')
        ports['references/link.md'] = 'detail'
    with pytest.raises(skill.SkillMaterialError, match='without following links'):
        load(selected, ports)


def test_missing_skill_escape_duplicate_ports_and_relative_root_fail(directory):
    for ports in ({'references/details.md': 'detail'},
                  {'SKILL.md': 'request', '../outside.md': 'detail'},
                  {'SKILL.md': 'request', 'references/details.md': 'request'}):
        with pytest.raises(skill.SkillMaterialError):
            load(directory, ports)
    with pytest.raises(skill.SkillMaterialError, match='absolute path'):
        load(Path('read-guide'))


def test_nonregular_directory_and_non_utf8_members_fail(directory):
    with pytest.raises(skill.SkillMaterialError, match='regular file'):
        load(directory, {'SKILL.md': 'request', 'references': 'detail'})
    with pytest.raises(skill.SkillMaterialError, match='binary member'):
        load(directory, {'SKILL.md': 'request', 'unselected.bin': 'binary'})


def test_fifo_fails_without_waiting_for_a_writer(directory):
    os.mkfifo(directory / 'fifo')
    script = (
        'import sys\nfrom cpn.rpnh import skill_materials as s\n'
        'try:\n s.prepare_directory_member_inputs(sys.argv[1], '
        '{"SKILL.md":"request","fifo":"fifo"},max_bytes=4096)\n'
        'except s.SkillMaterialError as error:\n'
        ' assert "regular file" in str(error)\n'
        'else:\n raise AssertionError("FIFO was accepted")\n'
    )
    result = subprocess.run([sys.executable, '-c', script, str(directory)],
                            capture_output=True, text=True, timeout=5, check=False)
    assert result.returncode == 0, result.stderr


def test_success_and_failure_close_all_opened_descriptors(directory):
    descriptors = Path('/proc/self/fd')
    if not descriptors.is_dir():
        pytest.skip('Linux descriptor inventory is unavailable on this platform')
    (directory / 'bad-link').symlink_to(directory / 'SKILL.md')
    before = set(descriptors.iterdir())
    for _ in range(3):
        load(directory)
        with pytest.raises(skill.SkillMaterialError):
            load(directory, {'SKILL.md': 'request', 'bad-link': 'bad'})
    assert set(descriptors.iterdir()) == before


def test_directory_input_reaches_original_success_and_active_skill_read(directory):
    document, inputs = load(directory, {'SKILL.md': 'request'}, max_bytes=len(RAW))
    owner, publisher, member_ref, _literal_index = native.world(
        directory.parent, request=inputs['request'])
    index = skill.make_skill_index(document, {'SKILL.md': member_ref})
    outputs = native.publish(owner, publisher, index)
    active = native.consumer(owner, outputs)
    _logical, grant = native.grant_member(owner, active, member_ref)
    read = skill.read_firing_skill_member(owner, active,
        SourceQualifiedResourceRef(native.SOURCE, outputs.outputs[0].resource_ref),
        'SKILL.md', member_authorization_ref=grant, command_id='skill:directory:read')
    assert read['text'].encode('utf-8') == RAW
    assert read['index_receipt'] == active.operation.inputs[0].artifact.receipt
    assert read['receipt'].outcome == 'acknowledged'
    assert read['receipt'].observed_read_event_id is not None
    assert active.operation.spec.allowed_tool_ids == ()

