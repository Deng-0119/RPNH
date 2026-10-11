#!/usr/bin/env python3
"""Check/build maintained Markdown and linked support; never execute examples."""
from __future__ import annotations
import argparse
import ast
from collections import Counter
import csv
from dataclasses import dataclass
from html import escape
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
from urllib.parse import unquote, urlsplit, urlunsplit
import zlib
import xml.etree.ElementTree as ET

import yaml
from markdown_it import MarkdownIt

PARSER = MarkdownIt('commonmark', {'html': False}).enable('table')
STATIC_DOCUMENTS = {'AGENTS.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'}
ROOT_PROJECT_DOCUMENTS = (
    'CHANGELOG.md', 'CHANGELOG_ZH.md',
    'CONTRIBUTING.md', 'CONTRIBUTING_ZH.md',
    'SECURITY.md', 'SECURITY_ZH.md',
)
# Public source references and input fixtures linked by the maintained guides.
# Keep this explicit: internal files are not admitted by directory or suffix.
PUBLIC_DOWNLOADS = {
    'cpn/rpnh/agent_tasks.py',
    'cpn/components/agent_loop/tool_catalog.py',
    'cpn/plugins/managed_scheduler.py',
    'cpn/components/agent_loop/program_execution.py',
    'cpn/plugins/controlled_script.py',
    'examples/slopcodebench/sources.json',
    'examples/tool_pipeline/fixtures/usage.json',
    'examples/tool_pipeline/fixtures/tariff.json',
    'examples/tool_pipeline/module.json',
    'examples/tool_pipeline/host.py',
    'examples/tool_pipeline/tools.py',
    'examples/tool_pipeline/tests/pipe_transport.py',
}
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
# Exact already-reviewed report artwork only; new or changed SVGs require review.
# Keep path/content admission separate from static-XML safety validation below.
REVIEWED_REPORT_SVGS = {
    'docs/assets/technical-report/01-architecture-en.svg':
        '2f16b514871d48b8574e68306c06976f0f43e88ba961b826b3cf4143b835173b',
    'docs/assets/technical-report/01-architecture-zh.svg':
        '8ce5bea3c7d4b7663847a5a96e98983a9b22b36d07d4f5b13acedfeb10575da1',
    'docs/assets/technical-report/02-parallel-join-en.svg':
        'aede7eecba91f32e064ba4b9114d110d754ae4540230cdd5e8bf3177aa62e85c',
    'docs/assets/technical-report/02-parallel-join-zh.svg':
        '7156dbfa0d3f8c1900e5c50d1f127597d3fd9b8985ddfcedf464c4cbaf3553e7',
    'docs/assets/technical-report/03-settlement-en.svg':
        '96845dbcf3b5ab46600adf186bf0ababc0e85e3b1c381f31deef08d9f8991655',
    'docs/assets/technical-report/03-settlement-zh.svg':
        '264301b881c11a44ffb6b99b9a31c7eb91e1543081d68a37b57869e9ea41e2d5',
    'docs/assets/technical-report/04-revision-adoption-en.svg':
        'ea2cf30b0d62ca812bca7cd06909236264471a37a3d05fbcd6c02c411c6119a6',
    'docs/assets/technical-report/04-revision-adoption-zh.svg':
        'd1e4e197fcf05367233db96471c5fb0178f803631a52369360cfba9812dfe5bc',
    'docs/assets/technical-report/05-managed-tools-en.svg':
        '67c29342075c14832533dafe79bc22eb33c7d581caf8c41de130cd7e61408e0e',
    'docs/assets/technical-report/05-managed-tools-zh.svg':
        '1e48662084011d205edfae67681f4be501bad490af0e455b2d6b49e4f497ce98',
    'docs/assets/technical-report/06-source-cuts-en.svg':
        'b4d361190e66d84a7b4dc7a12e179e8924abe125530c1db4e31b925eba239ec6',
    'docs/assets/technical-report/06-source-cuts-zh.svg':
        '070b5de56a165706f9e20cd0085979e1d9c04e2af6982c0601c1363b9e6b1a4d',
    'docs/assets/technical-report/07-product-origin-en.svg':
        '2ff5d5bcc2e132b99a33bcfa0572a210236dc49dd0be1a51a8cc51d1f41b5c54',
    'docs/assets/technical-report/07-product-origin-zh.svg':
        'acdc94dfc0dd34e08d358e760b49ff4fa34d9910d7884da39a3d8fb71ef8ff13',
    'docs/assets/technical-report/08-finite-pn-policy-en.svg':
        'e9fc4b50c0d4c4ae1d19df8d4e133fc11289d4b670b704b682037630cbae2b86',
    'docs/assets/technical-report/08-finite-pn-policy-zh.svg':
        '0681fbc4c9059075a41503040847d141bb4819b9e6d569c1d14c5536a8c279cc',
}
REPORT_SVG_ELEMENTS = {
    'svg', 'title', 'desc', 'defs', 'marker', 'path', 'rect', 'g', 'text', 'circle',
}
REPORT_SVG_ATTRIBUTES = {
    'width', 'height', 'viewBox', 'role', 'aria-labelledby', 'id',
    '{http://www.w3.org/XML/1998/namespace}lang',
    'refX', 'refY', 'markerWidth', 'markerHeight', 'orient', 'd', 'fill',
    'x', 'y', 'rx', 'font-family', 'font-size', 'font-weight', 'text-anchor',
    'stroke', 'stroke-width', 'stroke-linejoin', 'marker-end',
    'stroke-dasharray', 'cx', 'cy', 'r',
}



@dataclass
class Page:
    path: Path
    metadata: dict
    tokens: list
    title: str
    headings: dict[str, str]


def pages_in(root: Path) -> list[Path]:
    paths = [root / name for name in ('README.md', 'README_ZH.md')]
    paths.extend(root / name for name in ROOT_PROJECT_DOCUMENTS)
    paths.extend(sorted((root / 'docs').glob('*.md')))
    for folder in ('guides', 'architecture', 'reference'):
        paths.extend(sorted((root / 'docs' / folder).glob('*.md')))
    paths.extend(sorted((root / 'docs' / 'results').glob('README*.md')))
    paths.extend(sorted((root / 'docs' / 'results').glob('*/README*.md')))
    paths.extend(sorted((root / 'examples').glob('**/README*.md')))
    return paths


def auxiliary_documents(root: Path) -> list[Path]:
    """Markdown checked for links but intentionally excluded from the site."""
    bundled = root / 'cpn' / 'examples'
    return sorted(bundled.glob('**/README*.md')) if bundled.is_dir() else []


def reviewed_assets(root: Path) -> list[Path]:
    """Return example PNGs and the explicit reviewed report SVGs."""
    return sorted({
        *root.glob('examples/**/assets/*.png'),
        *root.glob('cpn/examples/**/assets/*.png'),
        *(root / name for name in REVIEWED_REPORT_SVGS
          if (root / name).is_file()),
    })


def slug(text: str) -> str:
    return re.sub(r'\s+', '-', re.sub(r'[^\w\s-]', '', text.lower())).strip('-')


def parse_page(path: Path, *, maintained: bool = True) -> Page:
    text = path.read_text(encoding='utf-8')
    if text.startswith('---\n'):
        _, front, text = text.split('---\n', 2)
        info = yaml.safe_load(front)
        if not isinstance(info, dict):
            raise ValueError(f'{path}: invalid front matter')
        meta = info.get('metadata', {})
        if not isinstance(meta, dict):
            raise ValueError(f'{path}: metadata must be a mapping')
        if not all(isinstance(info.get(k), str) and info[k] for k in ('name', 'description')):
            raise ValueError(f'{path}: missing name/description')
        for key in ('document-kind', 'language', 'counterpart', 'revision', 'status'):
            if not isinstance(meta.get(key), str) or not meta[key]:
                raise ValueError(f'{path}: missing metadata {key}')
        meta = {**meta, 'name': info['name']}
    elif path.name in {'README.md', 'README_ZH.md'}:
        if path.parent.name == 'results' and path.parent.parent.name == 'docs':
            name, revision = 'rpnh-results', 'result-index-v1'
        elif path.parent.parent.name == 'results' and path.parent.parent.parent.name == 'docs':
            name, revision = 'rpnh-result-' + path.parent.name, 'source-result-v1'
        elif 'examples' in path.parts:
            index = path.parts.index('examples')
            suffix = '-'.join(path.parts[index + 1:-1]) or 'catalog'
            name, revision = 'rpnh-example-' + suffix, 'source-example-v1'
        else:
            name, revision = 'rpnh-readme', 'entry'
        meta = {'name': name, 'revision': revision,
                'language': 'zh-CN' if path.stem.endswith('_ZH') else 'en',
                'counterpart': 'README.md' if path.stem.endswith('_ZH') else 'README_ZH.md'}
    elif not maintained:
        meta = {'language': 'zh-CN' if path.stem.endswith('_ZH') else 'en'}
    else:
        raise ValueError(f'{path}: front matter required')
    if meta['language'] not in {'en', 'zh-CN'}:
        raise ValueError(f'{path}: unknown language')
    tokens = PARSER.parse(text)
    headings, counts, title, h1 = {}, Counter(), '', 0
    for index, token in enumerate(tokens):
        if token.type != 'heading_open':
            continue
        text = tokens[index + 1].content
        key = slug(text)
        occurrence = counts[key]
        counts[key] += 1
        key = key if not occurrence else f'{key}-{occurrence}'
        token.attrSet('id', key)
        headings[key] = text
        if token.tag == 'h1':
            h1 += 1
            title = text
    if maintained and h1 != 1:
        raise ValueError(f'{path}: require exactly one H1, got {h1}')
    return Page(path.resolve(), meta, tokens, title or path.stem, headings)


def local_target(page: Path, href: str, root: Path) -> tuple[Path, str] | None:
    url = urlsplit(href)
    if url.scheme or url.netloc:
        if url.scheme not in {'https', 'mailto'}:
            raise ValueError(f'{page}: unsupported external link {href}')
        return None
    target = ((page.parent / unquote(url.path)) if url.path else page).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f'{page}: link escapes documentation root: {href}')
    return target, unquote(url.fragment)


def validate_report_svg(payload: bytes) -> None:
    """Reject active/external XML independently of the exact-content allowlist."""
    text = payload.decode('utf-8')
    if '<!' in text or '<?' in text:
        raise ValueError('SVG declarations, entities and processing instructions are forbidden')
    document = ET.fromstring(text)
    namespace = '{http://www.w3.org/2000/svg}'
    if document.tag != namespace + 'svg':
        raise ValueError('SVG root namespace required')
    ids, references = set(), []
    allowed_elements = {namespace + name for name in REPORT_SVG_ELEMENTS}
    for element in document.iter():
        if element.tag not in allowed_elements:
            raise ValueError('unsupported SVG element')
        for key, value in element.attrib.items():
            if key not in REPORT_SVG_ATTRIBUTES:
                raise ValueError('unsupported SVG attribute')
            if key == 'id':
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*', value) or value in ids:
                    raise ValueError('invalid or duplicate SVG id')
                ids.add(value)
            if key in {'fill', 'stroke', 'marker-end'}:
                reference = re.fullmatch(r'url\(#([A-Za-z_][A-Za-z0-9_.-]*)\)', value)
                if reference:
                    references.append(reference.group(1))
                elif not re.fullmatch(r'none|#[0-9A-Fa-f]{3}|#[0-9A-Fa-f]{6}', value):
                    raise ValueError('SVG paint/reference must be a color or local fragment')
            elif 'url(' in value.lower():
                raise ValueError('unsupported SVG URL')
    if any(reference not in ids for reference in references):
        raise ValueError('missing local SVG reference')


def reviewed_image_target(page: Path, source: str, root: Path) -> Path:
    target = local_target(page, source, root)
    if target is None:
        raise ValueError(f'{page}: example images must be local: {source}')
    path, anchor = target
    relative = path.relative_to(root)
    reviewed_svg_digest = REVIEWED_REPORT_SVGS.get(relative.as_posix())
    allowed = (
        len(relative.parts) >= 4
        and relative.parts[-2] == 'assets'
        and relative.suffix == '.png'
        and (relative.parts[0] == 'examples'
             or relative.parts[:2] == ('cpn', 'examples'))
    )
    if (not allowed and reviewed_svg_digest is None) or anchor:
        raise ValueError(f'{page}: image is outside reviewed asset policy: {source}')
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f'{page}: missing image asset {path}') from exc
    if reviewed_svg_digest is not None:
        try:
            validate_report_svg(payload)
            if hashlib.sha256(payload).hexdigest() != reviewed_svg_digest:
                raise ValueError('unreviewed SVG content')
        except (UnicodeError, ET.ParseError, ValueError) as exc:
            raise ValueError(f'{page}: invalid reviewed report SVG asset {path}: {exc}') from exc
        return path
    try:
        if not payload.startswith(PNG_SIGNATURE):
            raise ValueError('signature')
        offset = len(PNG_SIGNATURE)
        chunks = []
        width = height = None
        while offset < len(payload):
            if len(payload) - offset < 12:
                raise ValueError('truncated chunk')
            length = struct.unpack('>I', payload[offset:offset + 4])[0]
            chunk_type = payload[offset + 4:offset + 8]
            chunk_end = offset + 12 + length
            if chunk_end > len(payload):
                raise ValueError('truncated chunk data')
            data = payload[offset + 8:offset + 8 + length]
            expected_crc = struct.unpack(
                '>I', payload[offset + 8 + length:chunk_end])[0]
            if (len(chunk_type) != 4
                    or any(not (65 <= value <= 90 or 97 <= value <= 122)
                           for value in chunk_type)
                    or zlib.crc32(chunk_type + data) & 0xffffffff
                    != expected_crc):
                raise ValueError('invalid chunk')
            if not chunks:
                if chunk_type != b'IHDR' or length != 13:
                    raise ValueError('missing IHDR')
                width, height = struct.unpack('>II', data[:8])
            elif chunk_type == b'IHDR':
                raise ValueError('duplicate IHDR')
            chunks.append(chunk_type)
            offset = chunk_end
            if chunk_type == b'IEND':
                if length != 0 or offset != len(payload):
                    raise ValueError('invalid IEND')
                break
        if (not chunks or chunks[-1] != b'IEND' or b'IDAT' not in chunks
                or width is None or height is None or width < 1 or height < 1):
            raise ValueError('incomplete PNG')
    except (ValueError, struct.error) as exc:
        raise ValueError(f'{page}: invalid PNG asset {path}') from exc
    return path


def children(tokens):
    for token in tokens:
        yield token
        if token.children:
            yield from children(token.children)


def headings_in(tokens) -> dict[str, str]:
    headings, counts = {}, Counter()
    for index, token in enumerate(tokens):
        if token.type != 'heading_open':
            continue
        text = tokens[index + 1].content
        key = slug(text)
        occurrence = counts[key]
        counts[key] += 1
        key = key if not occurrence else f'{key}-{occurrence}'
        headings[key] = text
    return headings


def supporting_documents(root: Path, pages: dict[Path, Page]):
    """Follow linked example Markdown and bounded public document downloads.

    Support stays outside maintained topic navigation and language pairing.
    Maintained pages still require direct reachability through maintained pages.
    Legal downloads retain their source-relative paths, including nested notices.
    """
    support, downloads = {}, set()
    queue = list(pages.values())
    while queue:
        page = queue.pop()
        for token in children(page.tokens):
            if token.type != 'link_open':
                continue
            target = local_target(page.path, token.attrGet('href'), root)
            if target is None:
                continue
            dest, _anchor = target
            if dest in pages or dest in support or dest in downloads:
                continue
            relative = dest.relative_to(root)
            parts = relative.parts
            markdown = (len(parts) >= 2 and parts[0] == 'examples'
                        and dest.suffix == '.md')
            json_document = dest.suffix == '.json' and (
                (len(parts) == 4 and parts[0] == 'examples' and parts[2] == 'results')
                or (len(parts) == 4 and parts[:2] == ('docs', 'results'))
                or (len(parts) == 3 and parts[:2] == ('docs', 'reference'))
                or parts[:2] == ('cpn', 'schemas'))
            csv_document = (dest.suffix == '.csv' and len(parts) == 4
                            and parts[0] == 'examples'
                            and parts[2] in {'results', 'comparison'})
            python_source = (dest.suffix == '.py' and len(parts) >= 4
                             and parts[0] == 'examples' and parts[2] == 'src')
            legal = dest.name in STATIC_DOCUMENTS
            public_download = relative.as_posix() in PUBLIC_DOWNLOADS
            if not (markdown or json_document or csv_document or python_source
                    or legal or public_download):
                continue  # The link checker reports unsupported destinations.
            if not dest.is_file():
                raise ValueError(f'{page.path}: undocumented or missing link {dest}')
            if legal or json_document or csv_document or python_source or public_download:
                downloads.add(dest)
            else:
                support[dest] = parse_page(dest, maintained=False)
                queue.append(support[dest])
    return support, downloads


def check(root: Path) -> tuple[dict[Path, Page], dict[str, int]]:
    pages, _support, _downloads, stats = checked_documents(root)
    return pages, stats


def checked_documents(root: Path):
    root = root.resolve()
    pages = {path.resolve(): parse_page(path) for path in pages_in(root)}
    support, downloads = supporting_documents(root, pages)
    documents = {**pages, **support}
    stats = Counter(pages=len(pages), support_documents=len(support))
    for path in sorted(downloads):
        if path.suffix == '.json':
            try:
                json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda value: (
                    _ for _ in ()).throw(ValueError(f'nonfinite JSON: {value}')))
            except ValueError as exc:
                raise ValueError(f'{path}: invalid JSON document: {exc}') from exc
            stats['json_documents_syntax_only'] += 1
        elif path.suffix == '.csv':
            try:
                with path.open(encoding='utf-8', newline='') as handle:
                    for _row in csv.reader(handle, strict=True):
                        pass
            except csv.Error as exc:
                raise ValueError(f'{path}: invalid CSV document: {exc}') from exc
            stats['csv_documents_syntax_only'] += 1
        elif path.suffix == '.py':
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            stats['python_documents_syntax_only'] += 1
    image_assets = set()
    names = set()
    for path, page in pages.items():
        identity = (page.metadata['name'], page.metadata['language'])
        if identity in names:
            raise ValueError(f'{path}: duplicate topic/language')
        names.add(identity)
        other = (path.parent / page.metadata['counterpart']).resolve()
        if other not in pages:
            raise ValueError(f'{path}: counterpart missing')
        pair = pages[other]
        if ((other.parent / pair.metadata['counterpart']).resolve() != path
                or pair.metadata['name'] != page.metadata['name']
                or pair.metadata['revision'] != page.metadata['revision']
                or pair.metadata['language'] == page.metadata['language']):
            raise ValueError(f'{path}: counterpart mismatch')
    for path, page in documents.items():
        for token in children(page.tokens):
            if token.type == 'image':
                image_assets.add(reviewed_image_target(
                    path, token.attrGet('src'), root))
                stats['reviewed_image_references'] += 1
            if token.type == 'link_open':
                target = local_target(path, token.attrGet('href'), root)
                if target is None:
                    stats['external_links_not_fetched'] += 1
                else:
                    dest, anchor = target
                    if dest not in documents and dest not in downloads:
                        raise ValueError(f'{path}: undocumented or missing link {dest}')
                    if anchor and (dest not in documents or anchor not in documents[dest].headings):
                        raise ValueError(f'{path}: missing anchor {anchor}')
                    kind = ('internal_links' if dest in pages else
                            'legal_links' if dest.name in STATIC_DOCUMENTS else 'support_links')
                    stats[kind] += 1
            if token.type != 'fence':
                continue
            language = token.info.strip().split(' ', 1)[0]
            if language == 'python':
                ast.parse(token.content, filename=str(path))
                stats['python_blocks_syntax_only'] += 1
            elif language == 'json':
                json.loads(token.content, parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f'nonfinite JSON: {value}')))
                stats['json_blocks_syntax_only'] += 1
            elif language == 'bash':
                result = subprocess.run(['bash', '-n'], input=token.content,
                                        text=True, capture_output=True, timeout=5)
                if result.returncode:
                    raise ValueError(f'{path}: Bash syntax: {result.stderr}')
                stats['bash_blocks_syntax_only'] += 1
    auxiliary = {}
    for path in auxiliary_documents(root):
        resolved = path.resolve()
        tokens = PARSER.parse(path.read_text(encoding='utf-8'))
        auxiliary[resolved] = (tokens, headings_in(tokens))
    for path, (tokens, headings) in auxiliary.items():
        stats['auxiliary_documents'] += 1
        for token in children(tokens):
            if token.type == 'image':
                image_assets.add(reviewed_image_target(
                    path, token.attrGet('src'), root))
                stats['reviewed_image_references'] += 1
            if token.type != 'link_open':
                continue
            target = local_target(path, token.attrGet('href'), root)
            if target is None:
                stats['external_links_not_fetched'] += 1
                continue
            dest, anchor = target
            if not dest.is_file():
                raise ValueError(f'{path}: missing auxiliary link {dest}')
            if anchor:
                target_headings = (pages[dest].headings if dest in pages else
                                   auxiliary.get(dest, (None, {}))[1])
                if anchor not in target_headings:
                    raise ValueError(f'{path}: missing anchor {anchor}')
            stats['auxiliary_links'] += 1
    reachable, queue = set(), [root / 'README.md']
    while queue:
        path = queue.pop()
        if path in reachable:
            continue
        reachable.add(path)
        for token in children(pages[path].tokens):
            if token.type == 'link_open':
                target = local_target(path, token.attrGet('href'), root)
                if target and target[0] in pages:
                    queue.append(target[0])
    if reachable != set(pages):
        raise ValueError('Pages not reachable from README: ' + str(set(pages) - reachable))
    stats['reachable_pages'] = len(reachable)
    stats['language_pairs'] = len(pages) // 2
    stats['reviewed_image_assets'] = len(image_assets)
    return pages, support, downloads, dict(stats)


def html_path(relative: Path) -> Path:
    if str(relative) == 'README.md':
        return Path('index.html')
    if str(relative) == 'README_ZH.md':
        return Path('index_ZH.html')
    return relative.with_suffix('.html')


def navigation_group(path: Path, root: Path, language: str) -> tuple[int, str]:
    """Map maintained pages to a stable task-oriented navigation group."""
    relative = path.relative_to(root).as_posix()
    labels = ({
        'start': 'Start', 'build': 'Build', 'observe': 'Observe',
        'integrations': 'Integrations', 'reference': 'Reference',
        'project': 'Project', 'results': 'Results', 'examples': 'Source examples',
    } if language == 'en' else {
        'start': '入门', 'build': '构建', 'observe': '查看运行',
        'integrations': '宿主集成', 'reference': '参考',
        'project': '项目状态', 'results': '结果', 'examples': '源码案例',
    })
    if relative.startswith('docs/results/'):
        key, order = 'results', 6
    elif relative.startswith('examples/'):
        key, order = 'examples', 7
    elif relative.startswith('docs/reference/') or relative in {
            'docs/ARCHITECTURE.md', 'docs/ARCHITECTURE_ZH.md',
            'docs/PROVIDER_MODEL_CONFIGURATION.md',
            'docs/PROVIDER_MODEL_CONFIGURATION_ZH.md'}:
        key, order = 'reference', 4
    elif relative.startswith('docs/architecture/') or any(
            name in relative for name in (
                'customization', 'net-operations', 'repository-layout')):
        key, order = 'build', 1
    elif any(name in relative for name in (
            'viewer', 'runtime-registry', 'DISPLAY_OBSERVATION')):
        key, order = 'observe', 2
    elif any(name in relative for name in (
            'adapters', 'opencode', 'dsh', 'RPNH_VS_CODEX')):
        key, order = 'integrations', 3
    elif any(name in relative for name in (
            'development', 'release-validation', 'examples-validation',
            'PROVENANCE', 'CHANGELOG',
            'CONTRIBUTING', 'SECURITY')):
        key, order = 'project', 5
    else:
        key, order = 'start', 0
    return order, labels[key]


CSS = '''body{margin:0;font:17px/1.65 system-ui,sans-serif;color:#202a36;background:#fff}
header{padding:1rem 2rem;border-bottom:1px solid #d8e0e8;background:#f4f7fa}
a{color:#145d87;text-decoration:none}a:hover{text-decoration:underline}
.layout{display:grid;grid-template-columns:17rem minmax(0,1fr);max-width:1320px;margin:auto}
nav{padding:2rem 1.5rem;border-right:1px solid #d8e0e8}nav a{display:block;margin:0 0 .75rem}
.nav-group{margin:0 0 1.5rem}.nav-group strong{display:block;margin:0 0 .65rem;color:#52647a;font-size:.78rem;text-transform:uppercase;letter-spacing:.06em}
main{padding:2rem 3rem;max-width:950px;min-width:0}h1{font-size:2.2rem;line-height:1.2}
h2{margin-top:2.1rem;font-size:1.45rem}code{font-size:.88em;background:#eef3f7;padding:.1em .3em}
pre{overflow:auto;background:#f1f5f8;padding:1rem;border-radius:6px}pre code{padding:0}
img{display:block;max-width:100%;height:auto;border:1px solid #d8e0e8;border-radius:6px}
table{display:block;overflow:auto;border-collapse:collapse;width:100%;font-size:.93rem}
th,td{text-align:left;border-bottom:1px solid #d8e0e8;padding:.6rem;vertical-align:top}
th{background:#eef3f7}footer{margin-top:3rem;font-size:.8rem;color:#506070}
.toc{font-size:.9rem;display:flex;gap:1rem;flex-wrap:wrap;border-bottom:1px solid #d8e0e8;padding-bottom:1rem}
@media(max-width:800px){.layout{display:block}nav{border-right:0;border-bottom:1px solid #d8e0e8;padding:1rem}nav a{display:inline-block;margin-right:1rem}main{padding:1.5rem}h1{font-size:1.8rem}}
'''


def build(root: Path, output: Path) -> dict[str, int]:
    root, output = root.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(root):
        raise ValueError('Use a new output directory outside the source tree')
    pages, support, downloads, stats = checked_documents(root)
    documents = {**pages, **support}
    assets = set(downloads)
    output.mkdir(parents=True)
    for path, page in documents.items():
        relative = html_path(path.relative_to(root))
        def href_for(target: Path) -> str:
            destination = (html_path(target.relative_to(root)) if target in documents
                           else target.relative_to(root))
            return Path(os.path.relpath(destination, relative.parent)).as_posix()
        for token in children(page.tokens):
            if token.type == 'link_open':
                old = token.attrGet('href')
                target = local_target(path, old, root)
                if target:
                    url = urlsplit(old)
                    token.attrSet('href', urlunsplit(('', '', href_for(target[0]),
                                                     url.query, url.fragment)))
            elif token.type == 'image':
                old = token.attrGet('src')
                target = reviewed_image_target(path, old, root)
                assets.add(target)
                token.attrSet('src', href_for(target))
        groups = {}
        for p, other in pages.items():
            if (other.metadata['language'] != page.metadata['language']
                    or p in {root / 'README.md', root / 'README_ZH.md'}):
                continue
            key = navigation_group(p, root, page.metadata['language'])
            groups.setdefault(key, []).append((p, other))
        navigation = ''.join(
            '<section class="nav-group">'
            f'<strong>{escape(label)}</strong>'
            + ''.join(
                f'<a href="{escape(href_for(p))}">{escape(other.title)}</a>'
                for p, other in items)
            + '</section>'
            for (_order, label), items in sorted(groups.items()))
        language_link = ''
        if path in pages:
            counterpart = (path.parent / page.metadata['counterpart']).resolve()
            switch = '中文' if page.metadata['language'] == 'en' else 'English'
            language_link = (f' · <a href="{escape(href_for(counterpart))}" '
                             f'lang="{pages[counterpart].metadata["language"]}">{switch}</a>')
        toc = ''.join(f'<a href="#{escape(key)}">{escape(value)}</a>'
                      for key, value in list(page.headings.items())[1:])
        body = PARSER.renderer.render(page.tokens, PARSER.options, {})
        footer = ('当前源码快照的文档；发行与验收范围见对应指南。本站不会执行示例。'
                  if page.metadata['language'] == 'zh-CN' else
                  'Source-snapshot documentation; see the guides for release and validation scope. Examples are not executed by this site.')
        html = (f'<!doctype html><html lang="{page.metadata["language"]}"><head>'
                '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                f'<title>{escape(page.title)} — RPNH</title><style>{CSS}</style></head><body>'
                f'<header><a href="{escape(href_for(root / "README.md"))}">RPNH</a>{language_link}'
                '</header><div class="layout"><nav aria-label="Topics">'
                f'{navigation}</nav><main><div class="toc">{toc}</div>{body}'
                f'<footer>{footer}</footer>'
                '</main></div></body></html>')
        dest = output / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding='utf-8')
    for name in STATIC_DOCUMENTS:
        source = root / name
        if source.is_file():
            shutil.copyfile(source, output / name)
    for source in assets:
        destination = output / source.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return {'built_html_pages': len(documents), **stats}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'build'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'build':
            if args.output is None:
                parser.error('build requires --output')
            result = build(args.root, args.output)
        else:
            _, result = check(args.root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, SyntaxError, yaml.YAMLError,
            subprocess.SubprocessError) as exc:
        print(f'Documentation check failed: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
