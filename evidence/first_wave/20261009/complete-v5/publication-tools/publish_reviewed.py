#!/usr/bin/env python3
"""Stage reviewed evidence only. No Git, subprocess, network, or candidate execution."""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

sys.dont_write_bytecode = True
WORKSPACE = Path('<WORKSPACE>')
TASK = WORKSPACE / 'task-complete-v5-20261009'
BUNDLE = TASK / 'rpnh-complete-local-bundle-v5'
DESTINATION = WORKSPACE / 'RPNH-main/evidence/first_wave/20261009/complete-v5'
DEFAULT_BUDGET = 64 * 1024 * 1024
PREFIXES = sorted([
    ('<WORKSPACE>', '<WORKSPACE>'),
    ('<USER_HOME>', '<USER_HOME>'),
    ('<INPUT_DIRECTORY>', '<INPUT_DIRECTORY>'),
], key=lambda item: len(item[0]), reverse=True)
TEXT_SUFFIXES = {'.md', '.txt', '.json', '.jsonl', '.xml', '.log', '.tap',
                 '.exit', '.exitcode', '.py', '.sh', '.mjs', '.ts', '.sha256',
                 '.diff', '.csv', '.tsv', '.yaml', '.yml'}
TEXT_NAMES = {'LICENSE', 'SHA256SUMS', 'ARCHIVE_SHA256SUMS', 'BUNDLE_SHA256SUMS'}
RECEIVED_DIRS = {'tools', 'scripts', 'audit-tools', 'native-gate', 'review',
                 'reviews', 'independent-review', 'independent-reviews',
                 'independent_review', 'evidence', 'reports', 'design',
                 'packaging', 'provenance'}
RECORD_DIRS = RECEIVED_DIRS | {'tests', 'original_scripts', 'independent_probes',
    'author-evidence', 'pre-d92', 'g1-final', 'clean-apply-g1', 'fake-pytest-run',
    'freeze', 'portable-runner-smoke', 'probes', 'native', 'review-exact',
    'current-g1-package', 'readback', 'gates'}
EXCLUDED_DIRS = {'source', 'baseline', 'inputs', 'replacement', 'upstream',
    'third_party', 'latest-main-overlay', 'worktree-independent', 'packages',
    '__pycache__', '.pytest_cache', '.git', '.venv', 'venv', 'node_modules',
    '.cache', 'cache', 'compiled', 'resume-compiled', 'exact-before',
    'historical-source-deltas'}
ARCHIVE_SUFFIXES = {'.zip', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.7z', '.whl'}
# A plain "profile" is legitimate public test/schema terminology, not private data.
PRIVATE_NAME = re.compile(r'(^|[._-])(private[-_]?profiles?|credentials?|secrets?|auth)([._-]|$)', re.I)


def private_file(path):
    match = PRIVATE_NAME.search(path.name)
    # Public auth test/tool source is not credential data merely by filename.
    # Its contents still go through privacy scanning and the parent's review.
    public_auth_source = match and match.group(2).lower() == 'auth' and path.suffix.lower() in {'.py', '.sh', '.mjs', '.ts'}
    return bool(match and not public_auth_source)


class Refusal(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


def checked(path, root=WORKSPACE):
    """Reject symlinks (including existing ancestors) and lexical escapes."""
    path = Path(os.path.abspath(path))
    require(path == root or root in path.parents, 'Path outside allowed root')
    for ancestor in (path, *path.parents):
        require(not ancestor.is_symlink(), f'Symlink refused: {path}')
    require(path.resolve() == path, f'Noncanonical path refused: {path}')
    return path


def relative_path(value):
    require(isinstance(value, str), 'Expected a relative path string')
    path = Path(value)
    require(value and not path.is_absolute() and '..' not in path.parts,
            'Expected a nonescaping relative path')
    return path


def read(path, limit=None):
    path = checked(path)
    require(path.is_file(), f'Missing regular file: {path}')
    with path.open('rb') as stream:
        data = stream.read() if limit is None else stream.read(limit + 1)
    require(limit is None or len(data) <= limit,
            f'Record exceeds remaining byte budget: {path.relative_to(WORKSPACE)}')
    return data


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + '\n').encode()


def parse_json(data):
    def invalid_constant(_):
        raise ValueError('Nonfinite JSON number')
    return json.loads(data.decode('utf-8'), parse_constant=invalid_constant)


def syntax_state(data, suffix):
    if suffix not in {'.json', '.xml'}:
        return 'not_applicable'
    try:
        parse_json(data) if suffix == '.json' else ET.fromstring(data)
        return 'parseable'
    except (ValueError, UnicodeError, ET.ParseError):
        return 'malformed'


def scan_private(data, source):
    """Review aid only; never print matched content or claim certification."""
    patterns = {
        'private_key': rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        'provider_token': rb'\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[A-Z0-9]{16})\b',
        'credential_in_url': rb'https?://[^\s/<>"\x00:]+:[^\s/<>"\x00]+@',
        'private_endpoint': rb'https?://(?:10\.[0-9.]+|192\.168\.[0-9.]+|172\.(?:1[6-9]|2[0-9]|3[01])\.[0-9.]+|[A-Za-z0-9.-]+\.(?:internal|local))(?=[:/\s"\x00]|$)',
    }
    for kind, pattern in patterns.items():
        require(not re.search(pattern, data),
                f'PRIVATE REVIEW REQUIRED: {source} ({kind}); no matched value printed')
    assignments = re.finditer(
        rb'''(?i)["']?(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)["']?\s*[:=]\s*["']([^"'\r\n\x00]{16,})["']''', data)
    for match in assignments:
        value = match.group(1).lower()
        placeholder = any(word in value for word in
                          (b'example', b'placeholder', b'dummy', b'synthetic', b'redacted', b'fake', b'test-', b'${', b'<'))
        require(placeholder, f'PRIVATE REVIEW REQUIRED: {source} (credential_assignment); no matched value printed')


def transform(data, suffix, frozen=False):
    """Only the three approved prefix substitutions, recorded in original offsets."""
    state = syntax_state(data, suffix)
    xml_escape = suffix == '.xml' and state == 'parseable'
    pattern = re.compile(b'|'.join(re.escape(old.encode()) for old, _ in PREFIXES))
    replacements = dict(PREFIXES)
    edits, pieces, cursor, included_offset = [], [], 0, 0
    for match in pattern.finditer(data):
        require(not frozen, 'Frozen received patch/control contains a private prefix; parent review required')
        old = match.group().decode()
        new = replacements[old]
        if xml_escape:
            new = escape(new, {'"': '&quot;', "'": '&apos;'})
        new_bytes = new.encode()
        start, end = match.span()
        unchanged = data[cursor:start]
        pieces.extend([unchanged, new_bytes])
        included_offset += len(unchanged)
        edits.append({
            'operation': 'private_prefix', 'old': old, 'new': new,
            'original_bytes': [start, end],
            'included_bytes': [included_offset, included_offset + len(new_bytes)],
            'original_lines': [data.count(b'\n', 0, start) + 1,
                               data.count(b'\n', 0, end - 1) + 1],
            'included_lines': [data.count(b'\n', 0, start) + 1,
                               data.count(b'\n', 0, end - 1) + 1],
            'xml_escaped': xml_escape,
        })
        included_offset += len(new_bytes)
        cursor = end
    pieces.append(data[cursor:])
    included = b''.join(pieces)
    if xml_escape:
        require(syntax_state(included, '.xml') == 'parseable', 'XML substitution broke parsing')
    return included, edits


def companion(data, target):
    return json_bytes({'label': 'Escaped UTF-8 view; companion, NOT a replacement or repaired result',
                       'raw_payload': target, 'raw_sha256': digest(data),
                       'text': data.decode('utf-8')})


def text_file(path):
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES


def excluded_dir(name):
    low = name.lower()
    return (name in EXCLUDED_DIRS or low.startswith('package') or
            low.startswith('source-') or low.endswith('-source') or
            'snapshot' in low or bool(PRIVATE_NAME.search(name)))


def enumerate_selection(gate_path, gate):
    """Positive-list metadata/records; never traverse a repository or runtime tree."""
    selected, skipped, candidates = {}, [], []

    def add(path, target, category, frozen=False, expected=None):
        path = checked(path, TASK)
        target = relative_path(str(target)).as_posix()
        require(target not in selected, f'Duplicate payload target: {target}')
        require(not private_file(path), f'Private file requested: {path.relative_to(WORKSPACE)}')
        require(text_file(path) or path.suffix == '.patch', f'Nontext file requested: {path}')
        require(path.is_file(), f'Missing selected file: {path}')
        selected[target] = {'path': path, 'category': category, 'frozen': frozen,
                            'expected_sha256': expected}

    def walk(root, out, category, received=False, top_dirs=None):
        root = checked(root, TASK)
        if not root.is_dir():
            return
        for directory, dirs, files in os.walk(root, followlinks=False):
            directory = Path(directory)
            allowed = top_dirs if directory == root and top_dirs is not None else RECORD_DIRS
            if directory == TASK / 'stages/core/S1/native':
                allowed = allowed | {'a001', 'a002', 'a003', 'a004', 'control001'}
            elif directory == TASK / 'stages/core/H7_history':
                allowed = allowed | {'review-resume-exact'}
            for name in sorted(dirs):
                child = directory / name
                if child.is_symlink() or excluded_dir(name) or name not in allowed:
                    dirs.remove(name)
                    skipped.append({'source': child.relative_to(WORKSPACE).as_posix(),
                                    'reason': 'directory outside positive list; not traversed'})
            dirs.sort()
            for name in sorted(files):
                path = directory / name
                rel = path.relative_to(root)
                historical_patch = path.suffix == '.patch' and len(rel.parts) > 1
                if (path.is_symlink() or private_file(path) or
                        not (text_file(path) or historical_patch)):
                    skipped.append({'source': path.relative_to(WORKSPACE).as_posix(),
                                    'reason': 'outside text-record positive list'})
                    continue
                # Received root/control JSON and exact selected patches are immutable.
                frozen = received and len(rel.parts) == 1 and (path.suffix == '.json' or path.name in TEXT_NAMES)
                add(path, out / rel, 'historical_patch_not_application_target' if historical_patch
                    else category, frozen=frozen)

    idx = parse_json(read(BUNDLE / 'CANDIDATE_INDEX.json'))
    lock = parse_json(read(BUNDLE / 'ARCHIVE_LOCK.json'))
    require(len(idx['candidates']) == 12 and len({c['id'] for c in idx['candidates']}) == 12,
            'Expected exactly 12 distinct candidates')
    walk(BUNDLE, Path('received/bundle'), 'received_bundle', received=True,
         top_dirs={'tools', 'plan-v5', 'templates', 'evidence'})
    archives = [{k: a[k] for k in ('id', 'path', 'sha256', 'size_bytes', 'roots', 'member_count')}
                for a in lock['archives']]
    for c in idx['candidates']:
        cid = relative_path(c['id']).as_posix()
        require('/' not in cid, 'Candidate id must be one path component')
        extraction = TASK / 'raw' / relative_path(c['archive_path']).stem
        member = relative_path(c['selected_final_patch_member'])
        require(member.parts[0] == c['root_member'] and len(member.parts) == 2,
                f'Expected exact ROOT patch for {cid}')
        possible = [p for p in (extraction, extraction / c['root_member'])
                    if (p / member.name).is_file()]
        require(len(possible) == 1, f'NOT_FOUND/AMBIGUOUS candidate root: {cid}')
        root = checked(possible[0], TASK / 'raw')
        archive = next((a for a in lock['archives'] if a['path'] == c['archive_path']), None)
        require(archive and archive['sha256'] == c['archive_sha256'], f'Archive lock mismatch: {cid}')
        out = Path('received/candidates') / cid
        walk(root, out, 'received_candidate', received=True, top_dirs=RECEIVED_DIRS)
        patch_target = (out / member.name).as_posix()
        add(root / member.name, patch_target, 'selected_candidate_patch_evidence_only',
            frozen=True, expected=c['patch_sha256'])
        skipped[:] = [item for item in skipped if item['source'] !=
                      (root / member.name).relative_to(WORKSPACE).as_posix()]
        if cid == 'H7_core':
            # V5 references this old failure version. Select only its patch;
            # source-v1 remains excluded from recursive snapshot traversal.
            historical = Path('evidence/source-v1/H7-core.patch')
            add(root / historical, out / historical,
                'historical_patch_not_application_target', frozen=True)
        # Check original received members against the received archive lock, without opening ZIPs.
        member_hashes = {m['path']: m['sha256'] for m in archive['members']}
        for target, entry in selected.items():
            if target.startswith(out.as_posix() + '/'):
                key = (Path(c['root_member']) / entry['path'].relative_to(root)).as_posix()
                require(key in member_hashes, f'Received member absent from archive lock: {cid}/{key}')
                entry['expected_sha256'] = member_hashes[key]
        require(selected[patch_target]['expected_sha256'] == c['patch_sha256'],
                f'Selected patch lock mismatch: {cid}')
        candidates.append({'id': cid, 'source_root': root.relative_to(WORKSPACE).as_posix(),
                           'selected_patch': patch_target, 'patch_sha256': c['patch_sha256'],
                           'label': 'candidate evidence only; never applied or merged'})

    results = checked(TASK / relative_path(gate['results_summary']), TASK)
    result_manifest = checked(TASK / relative_path(gate['result_manifest']), TASK)
    require(results.suffix == '.md' and result_manifest.suffix == '.json',
            'Final summary must be Markdown and result manifest JSON')
    # Stable public entry names; original paths remain recorded in the inventory.
    add(results, 'RESULTS_ZH.md', 'final_summary')
    add(result_manifest, 'result-manifest.json', 'final_control_json')
    add(gate_path, 'final-gate-summary.json', 'final_control_json', frozen=True)
    reserved = {results, result_manifest, gate_path}
    for path in sorted(TASK.iterdir()):
        if path.is_file() and path not in reserved and path.suffix in {'.py', '.json', '.jsonl', '.md'}:
            add(path, Path('local') / path.name, 'local_helpers_and_control')
    walk(TASK / 'reviews', Path('local/reviews'), 'local_review')
    walk(TASK / 'logs', Path('local/logs'), 'local_log')
    for stage in ('core', 'rsi', 'codex', 'dsh', 'opencode'):
        walk(TASK / 'stages' / stage, Path('stages') / stage, 'stage_record',
             top_dirs=RECORD_DIRS | {'S1', 'H7_core', 'H7_history', 'H7_lowering'})
    for name in ('publish_reviewed.py', 'validate_reviewed.py', 'README.md'):
        add(TASK / 'publication' / name, Path('publication-tools') / name, 'publication_tool')

    source_targets = defaultdict(list)
    for target, entry in selected.items():
        source_targets[entry['path']].append(target)
    controls, junit = [], []
    for field, output, suffix in (('final_control_json', controls, '.json'),
                                  ('final_accepted_junit', junit, '.xml')):
        values = gate.get(field)
        require(isinstance(values, list) and values, f'{field} must explicitly list final files')
        for value in values:
            path = checked(TASK / relative_path(value), TASK)
            require(path.suffix == suffix and len(source_targets[path]) == 1,
                    f'Final file must have one positive-listed payload target: {value}')
            output.append(source_targets[path][0])
    controls = sorted(set(controls + ['result-manifest.json', 'final-gate-summary.json']))
    return selected, skipped, candidates, archives, controls, sorted(set(junit))


def load_gate(path):
    path = checked(path, TASK)
    gate = parse_json(read(path))
    for field in ('final', 'publication_approved', 'privacy_review_complete', 'evidence_only'):
        require(gate.get(field) is True, f'Parent final gate must explicitly set {field}=true')
    return gate


def stats(entries):
    categories = defaultdict(lambda: {'files': 0, 'bytes': 0})
    for entry in entries:
        bucket = categories[entry['category']]
        bucket['files'] += 1
        bucket['bytes'] += entry['size']
    return {'files': sum(c['files'] for c in categories.values()),
            'bytes': sum(c['bytes'] for c in categories.values()),
            'categories': dict(sorted(categories.items()))}


def public_readme():
    return '''# Complete v5 stage evidence

[Final results (Chinese)](RESULTS_ZH.md) · [Result manifest](result-manifest.json)
· [Parent final gate](final-gate-summary.json) · [Publication inventory](publication-manifest.json)

Stage records, including original failures and inconclusive attempts:
[core](stages/core/) · [RSI](stages/rsi/) · [Codex](stages/codex/)
· [DSH](stages/dsh/) · [OpenCode](stages/opencode/).
Received failures/reviews remain under [candidate evidence](received/candidates/);
the inventory explicitly lists malformed originals, empty files and NUL records.
Those are historical evidence, never repaired into a pass. Escaped JSON views are
companions, not replacements. Only explicitly nominated final control JSON and
accepted JUnit files must parse; historical XML/JSON may remain malformed.
Accepted means reviewed for publication; parseable FAIL results, including native
g7, remain valid evidence and are never relabeled PASS.

[Received bundle metadata](received/bundle/) includes archive SHA/size/member
metadata; archives, source snapshots, databases, private profiles, package work
copies and caches are excluded. Selected root patches are candidate evidence;
historical patches are labeled separately in the inventory. No patch is applied,
no candidate code is merged, and no product default changes here. Candidate source
copies are deliberately omitted: the selected patches carry the changed paths.

[Local commands/helpers](local/) and [publication helpers](publication-tools/)
record original execution sources; they require local path configuration and are
not advertised as portable. Approved local prefix substitutions are longest-first:
`<INPUT_DIRECTORY>` → `<INPUT_DIRECTORY>`, `<WORKSPACE>` →
`<WORKSPACE>`, `<USER_HOME>` → `<USER_HOME>`. Parseable XML uses escaped
replacement text; malformed XML keeps its byte structure and its malformed label.
Every change has original/included hashes, sizes, byte offsets and line ranges.
Received root JSON controls and exact selected patches remain frozen; a private
prefix there aborts instead of silently rewriting the received control.

The parent supplied the final gate and privacy review. Regex scanning is a review
aid, not a secrecy certificate. Consult the final results for status and limitations;
this index invents no test totals or success claims.
'''.encode()


def file_record(source, target, category, original, included, edits, frozen):
    return {'source': source.relative_to(WORKSPACE).as_posix(), 'target': target,
            'category': category, 'frozen': frozen,
            'original_sha256': digest(original), 'included_sha256': digest(included),
            'original_size': len(original), 'included_size': len(included),
            'original_bytes': [0, len(original)], 'included_bytes': [0, len(included)],
            'original_lines': [1, original.count(b'\n') + 1] if original else [],
            'included_lines': [1, included.count(b'\n') + 1] if included else [],
            'original_syntax': syntax_state(original, source.suffix),
            'included_syntax': syntax_state(included, source.suffix),
            'nul_bytes': original.count(b'\x00'), 'transformations': edits}


def special_lists(records):
    return {name: [r['target'] for r in records if predicate(r)] for name, predicate in {
        'malformed_historical_originals': lambda r: r['original_syntax'] == 'malformed',
        'zero_byte_originals': lambda r: r['original_size'] == 0,
        'nul_originals': lambda r: r['nul_bytes'] > 0,
        'historical_patches_not_application_targets': lambda r: r['category'] == 'historical_patch_not_application_target',
    }.items()}


def check_final(payload, controls, junit):
    # Publication review accepts evidence, including FAIL JUnit; no PASS-only gate.
    for target in controls:
        require(syntax_state(payload[target], '.json') == 'parseable', f'Final control JSON malformed: {target}')
    for target in junit:
        require(syntax_state(payload[target], '.xml') == 'parseable', f'Final accepted JUnit malformed: {target}')
        require(ET.fromstring(payload[target]).tag in {'testsuite', 'testsuites'},
                f'Final accepted JUnit has wrong root: {target}')


def prepare(gate_path, budget):
    gate = load_gate(gate_path)
    selected, skipped, candidates, archives, controls, junit = enumerate_selection(gate_path, gate)
    selection_stats = stats({'category': e['category'], 'size': e['path'].stat().st_size}
                            for e in selected.values())
    print(json.dumps({'selection_before_writes': selection_stats, 'budget_bytes': budget}))
    require(selection_stats['bytes'] <= budget, 'Selection exceeds budget; nothing written, no failure evidence silently omitted')
    payload, records, generated = {}, [], []
    original_bytes = 0
    for target, entry in sorted(selected.items()):
        source = entry['path']
        original = read(source, limit=budget - original_bytes)
        original_bytes += len(original)
        if entry['expected_sha256']:
            require(digest(original) == entry['expected_sha256'], f'Received bytes differ from lock: {source.relative_to(WORKSPACE)}')
        scan_private(original, source.relative_to(WORKSPACE))
        try:
            original.decode('utf-8')
        except UnicodeError:
            raise Refusal(f'Non-UTF8 record requires parent review; not omitted: {source.relative_to(WORKSPACE)}') from None
        try:
            included, edits = transform(original, source.suffix, entry['frozen'])
        except Refusal as exc:
            raise Refusal(f'{source.relative_to(WORKSPACE)}: {exc}') from None
        payload[target] = included
        record = file_record(source, target, entry['category'], original, included, edits, entry['frozen'])
        records.append(record)
        if record['nul_bytes'] or record['original_syntax'] == 'malformed':
            companion_target = target + '.escaped-view.json'
            require(companion_target not in selected and companion_target not in payload,
                    f'Companion target collision: {companion_target}')
            payload[companion_target] = companion(included, target)
            generated.append({'target': companion_target, 'generator': 'escaped_view',
                              'raw_payload': target, 'category': 'escaped_companion'})
    require(stats({'category': r['category'], 'size': r['original_size']} for r in records) == selection_stats,
            'Live source sizes changed during preparation; nothing written')
    check_final(payload, controls, junit)
    payload['README.md'] = public_readme()
    generated.append({'target': 'README.md', 'generator': 'public_readme', 'category': 'public_index'})
    for entry in generated:
        data = payload[entry['target']]
        entry.update(included_sha256=digest(data), included_size=len(data),
                     included_bytes=[0, len(data)], included_lines=[1, data.count(b'\n') + 1])
    manifest = {'purpose': 'stage evidence only; no candidate application',
                'gate_source': gate_path.relative_to(WORKSPACE).as_posix(),
                'destination': DESTINATION.relative_to(WORKSPACE).as_posix(),
                'budget_bytes': budget, 'selection': selection_stats,
                'prefix_policy': PREFIXES, 'files': records, 'generated': generated,
                'selected_candidates': candidates, 'archive_metadata_only': archives,
                'excluded': skipped, 'final_control_json': controls,
                'final_accepted_junit': junit, **special_lists(records)}
    payload['publication-manifest.json'] = json_bytes(manifest)
    total = sum(map(len, payload.values()))
    print(json.dumps({'payload_before_writes': stats(
        [{'category': r['category'], 'size': r['included_size']} for r in records + generated]
        + [{'category': 'publication_manifest', 'size': len(payload['publication-manifest.json'])}]),
        'budget_bytes': budget}))
    require(total <= budget, 'Payload including companions/inventory exceeds budget; nothing written')
    # Live sources must have stopped changing before publication.
    for record in records:
        require(digest(read(WORKSPACE / record['source'])) == record['original_sha256'],
                f'Live source changed during preparation: {record["source"]}')
    return payload


def write_new(path, data):
    """Exclusive new files only: no overwrite, symlink following, or hard-link writes."""
    path = checked(path, DESTINATION)
    missing = []
    parent = path.parent
    while not parent.exists():
        checked(parent)
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        checked(directory)
        directory.mkdir()
    checked(path, DESTINATION)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode) and os.fstat(stream.fileno()).st_nlink == 1,
                'Output must be a new unlinked regular file')
        stream.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gate-summary', type=Path, required=True)
    parser.add_argument('--budget-bytes', type=int, default=DEFAULT_BUDGET)
    parser.add_argument('--run', action='store_true', help='Explicit parent authorization to stage evidence')
    args = parser.parse_args()
    require(args.run, 'Inert: parent final gate and explicit --run required')
    require(args.budget_bytes > 0, 'Budget must be positive')
    gate_path = checked(args.gate_summary, TASK)
    checked(DESTINATION)
    require(not DESTINATION.exists(), 'Destination already exists; no overwrite or deletion performed')
    payload = prepare(gate_path, args.budget_bytes)
    # Inventory is written last; an interrupted directory must be reviewed by the parent.
    for target, data in payload.items():
        write_new(DESTINATION / relative_path(target), data)
    sys.modules['publish_reviewed'] = sys.modules[__name__]
    from validate_reviewed import validate
    validate(DESTINATION)
    print('Evidence staged and verified locally. No Git operation or network publication performed.')


if __name__ == '__main__':
    try:
        main()
    except (Refusal, OSError, ValueError, KeyError, TypeError) as exc:
        # JSON/parser exception text may contain source contents; never echo it.
        print(str(exc) if isinstance(exc, Refusal) else f'Aborted ({type(exc).__name__}); inspect inputs privately', file=sys.stderr)
        sys.exit(1)
