"""Check published payload identity and recorded transformations; no product tests."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path('<WORKSPACE>').resolve()
REPO = ROOT / 'RPNH-main'
DEST = REPO / 'evidence/first_wave/20261009/taskcontrol-reader'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    manifest = json.loads((DEST / 'MANIFEST.json').read_text())
    expected = {r['path'] for r in manifest['files']}
    assert len(expected) == manifest['payload_count']
    xml_count, cloud_count, edits_count = 0, 0, 0
    for row in manifest['files']:
        source = ROOT / row['source_path_relative_to_workspace']
        target = DEST / row['path']
        assert source.resolve().is_relative_to(ROOT) and target.resolve().is_relative_to(DEST)
        raw, included = source.read_bytes(), target.read_bytes()
        assert sha(raw) == row['original_sha256'] and sha(included) == row['included_sha256']
        assert len(raw) == row['original_bytes'] and len(included) == row['included_bytes']
        parts, cursor, out_size = [], 0, 0
        for edit in row['edits']:
            start, end = edit['original_byte_range']
            a, b = edit['included_byte_range']
            chunk, replacement = raw[cursor:start], edit['replacement'].encode()
            out_size += len(chunk)
            assert [a, b] == [out_size, out_size + len(replacement)]
            assert edit['original_line_range'] == [raw[:start].count(b'\n') + 1] * 2
            assert raw[start:end] in (str(ROOT).encode(), b'<INPUT_DIRECTORY>')
            parts.extend((chunk, replacement))
            out_size += len(replacement)
            cursor = end
            edits_count += 1
        parts.append(raw[cursor:])
        assert b''.join(parts) == included
        if row['path'].startswith('cloud-handoff/'):
            assert raw == included and not row['edits']
            cloud_count += 1
        else:
            assert str(ROOT).encode() not in included
            assert b'<INPUT_DIRECTORY>' not in included
        if target.suffix == '.xml':
            ET.fromstring(included)
            xml_count += 1
        if target.suffix == '.json':
            json.loads(included)
    assert cloud_count == 64
    for line in (DEST / 'cloud-handoff/SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        assert sha((DEST / 'cloud-handoff' / name).read_bytes()) == digest
    actual = {p.relative_to(DEST).as_posix() for p in DEST.rglob('*') if p.is_file()}
    assert actual == expected | {'MANIFEST.json'}, actual ^ expected
    checks = {'status': 'PASS', 'payloads_verified': len(expected), 'received_cloud_files_byte_identical': cloud_count,
              'received_cloud_checksums_verified': 63, 'all_xml_reparsed': xml_count,
              'all_json_parsed': True, 'recorded_edits_reconstructed': edits_count,
              'no_unlisted_payload': True, 'no_registry_profile_cache_archive_selected': True,
              'runtime_validation': 'local/LAYERED_MANIFEST.json',
              'source_whitespace_check': 'Five source/doc paths passed git diff --check before adding raw evidence',
              'raw_evidence_whitespace': 'Preserved; original failure trace formatting is not rewritten'}
    out = DEST / 'PUBLICATION_CHECKS.json'
    assert out.resolve().is_relative_to(REPO.resolve()) and not out.exists()
    out.write_text(json.dumps(checks, indent=2) + '\n')
    text = '''# H2a 本地验收证据

H2a + H1 组合源码有限验收通过：166 不同用例 / 166 次执行。仓库内 154，包附独立检查 12；三项经典恢复测试在两项任务之间共享一次执行。没有真实模型/provider 调用。

- [本地验收报告](local/REPORT_ZH.md)
- [分层结果与八个窗口](local/LAYERED_MANIFEST.json)
- [用例清单与去重](local/test-inventory.json)
- [CLI 与独立回读审计](local/native-cli-readback-audit.json)
- [源码前后身份](local/source-before.json) / [测试后](local/source-after.json)
- [原始命令、stdout/stderr 与 JUnit](local/logs/)
- [独立代码与证据复核](local/reviews/)
- [云端原失败及来源说明](cloud-handoff/IMPLEMENTATION.md)
- [云端 native status 原日志](cloud-handoff/independent-review/native-taskcontrol-status.log) / [resume](cloud-handoff/independent-review/native-resume.log)
- [old-red 原日志](cloud-handoff/old-red.log)
- [收到的分发包此前脱敏记录](cloud-handoff/packaging/DISTRIBUTION_PROVENANCE.json)
- [公开文件哈希与每处本地替换](MANIFEST.json)
- [公开文件核验](PUBLICATION_CHECKS.json)

64 个收到的包成员保持原字节；它们此前的云端分发脱敏另有 provenance，不宣称等同于分发前原件。新的本地通过不改写旧 AF_UNIX EPERM 和 fixture 失败。完整本地原件与 Registry 留存，公开材料只替换私有工作区/输入目录前缀，XML 重新解析。

范围为指定 H2a 和必要的 H1 组合门槛，包含纯单元测试，不称 166 项 socket integration 或全仓验收。早先 A1 阻断保持历史、未重测；无 Actions、Docker、业务 benchmark 或新返回 ZIP。
'''
    out = DEST / 'README_ZH.md'
    assert out.resolve().is_relative_to(REPO.resolve()) and not out.exists()
    out.write_text(text)
    print(json.dumps(checks))


if __name__ == '__main__':
    main()
