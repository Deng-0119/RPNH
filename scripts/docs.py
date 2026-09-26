#!/usr/bin/env python3
"""Check/build only maintained Markdown; never import RPNH or execute examples."""
from __future__ import annotations
import argparse
import ast
from collections import Counter
from dataclasses import dataclass
from html import escape
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
from urllib.parse import unquote, urlsplit, urlunsplit
import zlib

import yaml
from markdown_it import MarkdownIt

PARSER = MarkdownIt('commonmark', {'html': False}).enable('table')
STATIC_DOCUMENTS = {'LICENSE', 'THIRD_PARTY_NOTICES.md'}
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


@dataclass
class Page:
    path: Path
    metadata: dict
    tokens: list
    title: str
    headings: dict[str, str]


def pages_in(root: Path) -> list[Path]:
    paths = [root / name for name in ('README.md', 'README_ZH.md')]
    paths.extend(sorted((root / 'docs').glob('*.md')))
    for folder in ('guides', 'architecture', 'reference'):
        paths.extend(sorted((root / 'docs' / folder).glob('*.md')))
    paths.extend(sorted((root / 'examples').glob('**/README*.md')))
    return paths


def auxiliary_documents(root: Path) -> list[Path]:
    """Markdown checked for links but intentionally excluded from the site."""
    bundled = root / 'cpn' / 'examples'
    return sorted(bundled.glob('**/README*.md')) if bundled.is_dir() else []


def reviewed_assets(root: Path) -> list[Path]:
    """Return public example images from the two explicit asset roots."""
    return sorted({
        *root.glob('examples/**/assets/*.png'),
        *root.glob('cpn/examples/**/assets/*.png'),
    })


def slug(text: str) -> str:
    return re.sub(r'\s+', '-', re.sub(r'[^\w\s-]', '', text.lower())).strip('-')


def parse_page(path: Path) -> Page:
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
        if 'examples' in path.parts:
            index = path.parts.index('examples')
            suffix = '-'.join(path.parts[index + 1:-1]) or 'catalog'
            name, revision = 'rpnh-example-' + suffix, 'source-example-v1'
        else:
            name, revision = 'rpnh-readme', 'entry'
        meta = {'name': name, 'revision': revision,
                'language': 'zh-CN' if path.stem.endswith('_ZH') else 'en',
                'counterpart': 'README.md' if path.stem.endswith('_ZH') else 'README_ZH.md'}
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
    if h1 != 1:
        raise ValueError(f'{path}: require exactly one H1, got {h1}')
    return Page(path.resolve(), meta, tokens, title, headings)


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


def reviewed_image_target(page: Path, source: str, root: Path) -> Path:
    target = local_target(page, source, root)
    if target is None:
        raise ValueError(f'{page}: example images must be local: {source}')
    path, anchor = target
    relative = path.relative_to(root)
    allowed = (
        len(relative.parts) >= 4
        and relative.parts[-2] == 'assets'
        and relative.suffix == '.png'
        and (relative.parts[0] == 'examples'
             or relative.parts[:2] == ('cpn', 'examples'))
    )
    if not allowed or anchor:
        raise ValueError(f'{page}: image is outside reviewed asset policy: {source}')
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f'{page}: missing image asset {path}') from exc
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


def check(root: Path) -> tuple[dict[Path, Page], dict[str, int]]:
    root = root.resolve()
    pages = {path.resolve(): parse_page(path) for path in pages_in(root)}
    stats = Counter(pages=len(pages))
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
                    if dest not in pages and dest.name not in STATIC_DOCUMENTS:
                        raise ValueError(f'{path}: undocumented or missing link {dest}')
                    if dest.name in STATIC_DOCUMENTS and not dest.is_file():
                        raise ValueError(f'{path}: missing linked legal document {dest}')
                    if anchor and (dest not in pages or anchor not in pages[dest].headings):
                        raise ValueError(f'{path}: missing anchor {anchor}')
                    stats['internal_links' if dest in pages else 'legal_links'] += 1
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
    return pages, dict(stats)


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
        'project': 'Project', 'examples': 'Source examples',
    } if language == 'en' else {
        'start': '入门', 'build': '构建', 'observe': '查看运行',
        'integrations': '宿主集成', 'reference': '参考',
        'project': '项目状态', 'examples': '源码案例',
    })
    if relative.startswith('examples/'):
        key, order = 'examples', 6
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
            'PROVENANCE', 'DEFERRED_ENGINEERING_WORK')):
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
    pages, stats = check(root)
    assets = set()
    output.mkdir(parents=True)
    for path, page in pages.items():
        relative = html_path(path.relative_to(root))
        def href_for(target: Path) -> str:
            destination = (html_path(target.relative_to(root)) if target in pages
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
        counterpart = (path.parent / page.metadata['counterpart']).resolve()
        switch = '中文' if page.metadata['language'] == 'en' else 'English'
        toc = ''.join(f'<a href="#{escape(key)}">{escape(value)}</a>'
                      for key, value in list(page.headings.items())[1:])
        body = PARSER.renderer.render(page.tokens, PARSER.options, {})
        footer = ('按源码核对的发布前文档。本站不会执行示例。'
                  if page.metadata['language'] == 'zh-CN' else
                  'Source-reviewed pre-release documentation. Examples are not executed by this site.')
        html = (f'<!doctype html><html lang="{page.metadata["language"]}"><head>'
                '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                f'<title>{escape(page.title)} — RPNH</title><style>{CSS}</style></head><body>'
                f'<header><a href="{escape(href_for(root / "README.md"))}">RPNH</a> · '
                f'<a href="{escape(href_for(counterpart))}" lang="{pages[counterpart].metadata["language"]}">{switch}</a>'
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
    return {'built_html_pages': len(pages), **stats}


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
