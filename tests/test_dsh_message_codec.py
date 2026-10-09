"""Pure offline codec checks. No owner, provider, socket, install or runtime."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import pytest
from cpn.dsh.message_codec import (
    LEGACY_DSH_REVISION as OLD, CANDIDATE_DSH_REVISION as NEW,
    decode_tool_result, encode_tool_result, make_tool_result, DshCodecError,
)
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / 'integrations/dsh/fixtures/message-codec.v1.json').read_text())


@pytest.mark.parametrize('entry', FIXTURE['valid'], ids=lambda e: e['name'])
@pytest.mark.parametrize('key,revision,other,other_revision', [('v3', OLD, 'v4', NEW), ('v4', NEW, 'v3', OLD)])
def test_tool_roundtrip_and_cross_layout(entry, key, revision, other, other_revision):
    original = deepcopy(entry[key])
    value = decode_tool_result(entry[key], revision, require_identity=True)
    assert encode_tool_result(value, revision) == original
    assert encode_tool_result(value, other_revision) == entry[other]
    assert entry[key] == original
    value['source']['callId'] = 'mutated'
    assert entry[key] == original


@pytest.mark.parametrize('entry', FIXTURE['invalid'], ids=lambda e: e['name'])
def test_invalid_exact_layout_is_refused(entry):
    with pytest.raises(DshCodecError):
        decode_tool_result(entry['message'], entry['revision'], require_identity=True)


@pytest.mark.parametrize('key', ['id', 'source', 'role', 'toolCallId', 'content', 'isError'])
def test_canonical_extension_cannot_replace_identity(key):
    value = decode_tool_result(FIXTURE['valid'][0]['v3'], OLD)
    for owner in ('messageExtensions', 'resultExtensions'):
        forged = deepcopy(value)
        forged[owner][key] = 'forged'
        with pytest.raises(DshCodecError):
            encode_tool_result(forged, NEW)


def test_managed_error_flag_remains_mandatory():
    message = FIXTURE['valid'][2]['v3']
    assert 'isError' not in decode_tool_result(message, OLD)
    with pytest.raises(DshCodecError):
        decode_tool_result(message, OLD, require_error=True)
    for invalid in [None, 0, 1, 'false']:
        with pytest.raises(DshCodecError):
            make_tool_result(message_id='r', call_id='c', content=[],
                             is_error=invalid, revision=OLD)
    result = make_tool_result(message_id='r', call_id='c', content=[],
                              is_error=True, revision=OLD)
    assert result['content'][0]['isError'] is True


def test_typescript_python_shared_fixture_parity():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node unavailable; cross-language gate not run')
    codec = (ROOT / 'integrations/dsh/src/message-codec.ts').as_uri()
    script = f'''import {{decodeToolResult, encodeToolResult}} from {json.dumps(codec)};
    let input = ''; for await (const chunk of process.stdin) input += chunk;
    const f = JSON.parse(input); const output = [];
    for (const e of f.valid) for (const [key, rev] of [['v3', f.legacy_revision], ['v4', f.candidate_revision]]) {{
      const v = decodeToolResult(e[key], rev, {{requireIdentity:true}});
      output.push({{value:v, encoded:encodeToolResult(v, rev)}});
    }}
    console.log(JSON.stringify(output));'''
    completed = subprocess.run([node, '--input-type=module', '-e', script],
                               input=json.dumps(FIXTURE), text=True,
                               capture_output=True, check=True, timeout=30)
    expected = []
    for entry in FIXTURE['valid']:
        for key, rev in [('v3', OLD), ('v4', NEW)]:
            value = decode_tool_result(entry[key], rev, require_identity=True)
            expected.append({'value': value, 'encoded': encode_tool_result(value, rev)})
    assert json.loads(completed.stdout) == expected
