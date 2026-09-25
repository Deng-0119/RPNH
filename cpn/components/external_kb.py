"""Immutable external-KB metadata search and exact bounded body reads."""

from __future__ import annotations

import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


INDEX_NAME = "INDEX_TIERED.json"
INDEX_SCHEMA = "external_kb_package_index/v1"
EXTERNAL_NODE_ID = "kb_package"
EXTERNAL_TOOL_NAME = "query_kb"
EXTERNAL_RESOURCE_KIND = "knowledge_base"
EXTERNAL_RESOURCE_PERMISSIONS = ("read",)
EXTERNAL_RESOURCE_INTERFACES = (
    "query_kb.search/v1",
    "query_kb.read/v1",
)
EXTERNAL_KB_PURPOSE_CONTENT_COVERAGE_DESCRIPTION = (
    "Read-only offline control-design knowledge package covering controllers, "
    "theory, solvers, known failures, anti-patterns, reviewer guidance, "
    "configuration patterns, and framework invariants; search and read it "
    "through the declared query_kb interfaces when this external component is "
    "relevant."
)
KB_QUERY_SKILL_ID = "kb_query_procedure/v1"
KB_QUERY_SKILL_DESCRIPTION = (
    "query_kb has two operations. search returns deterministic metadata pages "
    "without opening item bodies. read opens one exact item_id and returns one "
    "bounded character page. KB use is optional; an empty search is ordinary."
)
_TOKEN = re.compile(r"\w+", flags=re.UNICODE)
_INDEX_REQUIRED_FIELDS = frozenset({
    "schema_version", "package_id", "version", "n_items", "items",
})
_ITEM_REQUIRED_FIELDS = frozenset({
    "item_id", "path", "byte_size", "title", "kind",
    "tags", "summary_2_lines",
})
class ExternalKBFrameworkFault(RuntimeError):
    """The registered package or its read infrastructure is unavailable."""


class ExternalKBToolError(ValueError):
    """One model-supplied search/read request violates the tool interface."""


@dataclass(frozen=True, slots=True)
class ExternalKBQueryPolicy:
    """Run-registered query bounds; the KB module supplies no defaults."""

    search_default_page_size: int
    search_max_results: int
    read_max_chars: int

    def __post_init__(self) -> None:
        values = (
            self.search_default_page_size,
            self.search_max_results,
            self.read_max_chars,
        )
        if (any(isinstance(value, bool) or not isinstance(value, int)
                or value < 1 for value in values)
                or self.search_default_page_size > self.search_max_results):
            raise ValueError("external KB query policy is invalid")


@dataclass(frozen=True, slots=True)
class ExternalKBItem:
    item_id: str
    path: str
    byte_size: int
    title: str
    kind: str
    tags: tuple[str, ...]
    summary: str

    def metadata(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "byte_size": self.byte_size,
            "title": self.title,
            "kind": self.kind,
            "tags": list(self.tags),
            "summary_2_lines": self.summary,
        }


@dataclass(frozen=True, slots=True)
class ExternalKBPackage:
    root: Path
    package_id: str
    version: str
    items: tuple[ExternalKBItem, ...]

    def graph_node(self) -> dict[str, Any]:
        """Return the TeamNet permission declaration, never package authority."""

        return {
            "node_id": EXTERNAL_NODE_ID,
            "resource_kind": EXTERNAL_RESOURCE_KIND,
            "permissions": list(EXTERNAL_RESOURCE_PERMISSIONS),
        }

    def descriptor(self) -> dict[str, Any]:
        """Return the closed catalog registered for one run."""

        return {
            "package_id": self.package_id,
            "version": self.version,
            "root_anchor": os.fspath(self.root),
            "items": [{
                "item_id": item.item_id,
                "path": item.path,
                "byte_size": item.byte_size,
                "title": item.title,
                "kind": item.kind,
                "tags": list(item.tags),
                "summary_2_lines": item.summary,
            } for item in self.items],
            "read_only": True,
            "read_concurrency": "infinite",
        }


@dataclass(frozen=True, slots=True)
class ExternalKBSearchItem:
    item: ExternalKBItem
    score: int

    def metadata(self) -> dict[str, Any]:
        return {**self.item.metadata(), "score": self.score}


@dataclass(frozen=True, slots=True)
class ExternalKBSearchPage:
    package: Mapping[str, str]
    query: Mapping[str, Any]
    items: tuple[ExternalKBSearchItem, ...]
    offset: int
    page_size: int
    total_results: int
    next_offset: int | None

    def model_result(self) -> dict[str, Any]:
        return {
            "kind": "external_kb_search/v1",
            "operation": "search",
            "package": dict(self.package),
            "query": dict(self.query),
            "items": [item.metadata() for item in self.items],
            "page": {
                "offset": self.offset,
                "page_size": self.page_size,
                "returned_items": len(self.items),
                "total_results": self.total_results,
                "next_offset": self.next_offset,
            },
        }


@dataclass(frozen=True, slots=True)
class ExternalKBReadPage:
    package: Mapping[str, str]
    item: ExternalKBItem
    body: str
    offset_chars: int
    returned_chars: int
    total_chars: int
    next_offset_chars: int | None

    def model_result(self) -> dict[str, Any]:
        return {
            "kind": "external_kb_read/v1",
            "operation": "read",
            "package": dict(self.package),
            "item": self.item.metadata(),
            "body": self.body,
            "page": {
                "offset_chars": self.offset_chars,
                "returned_chars": self.returned_chars,
                "total_chars": self.total_chars,
                "next_offset_chars": self.next_offset_chars,
                "complete": self.next_offset_chars is None,
            },
        }

    def access_record(self) -> dict[str, Any]:
        return {
            "item_id": self.item.item_id,
            "path": self.item.path,
            "offset_chars": self.offset_chars,
            "returned_chars": self.returned_chars,
            "total_chars": self.total_chars,
            "byte_size": self.item.byte_size,
        }


@dataclass(frozen=True, slots=True, repr=False)
class TransientExternalKBResult:
    """Model result and exact body accesses for Registry attribution."""

    result: Mapping[str, Any]
    body_accesses: tuple[Mapping[str, Any], ...]


def _is_symlink(path: Path) -> bool:
    try:
        return stat.S_ISLNK(path.lstat().st_mode)
    except OSError as exc:
        raise ExternalKBFrameworkFault(
            f"KB path is unavailable: {path}") from exc


def _safe_item_path(root: Path, relative: str, *, require_file: bool) -> Path:
    pure = PurePosixPath(relative)
    if (not relative or pure.is_absolute() or ".." in pure.parts
            or pure.as_posix() != relative):
        raise ExternalKBFrameworkFault(
            "KB item path is absolute, escaping, or non-canonical")
    current = root
    for part in pure.parts:
        current = current / part
        if _is_symlink(current):
            raise ExternalKBFrameworkFault("KB item path contains a symlink")
    try:
        current.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ExternalKBFrameworkFault(
            "KB item path escapes or is missing") from exc
    if require_file and not current.is_file():
        raise ExternalKBFrameworkFault("KB item is not a regular file")
    return current


def load_external_kb_package(
    root_anchor: str | os.PathLike[str], *,
    expected: Mapping[str, object] | None = None,
) -> ExternalKBPackage:
    """Load package/index identity without opening an item body."""

    root = Path(root_anchor)
    if not root.is_absolute() or _is_symlink(root):
        raise ExternalKBFrameworkFault(
            "KB root anchor must be an absolute non-symlink directory")
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise ExternalKBFrameworkFault("KB root anchor is missing") from exc
    if not root.is_dir():
        raise ExternalKBFrameworkFault("KB root anchor is not a directory")
    index_path = root / INDEX_NAME
    if _is_symlink(index_path) or not index_path.is_file():
        raise ExternalKBFrameworkFault(
            "KB index must be one non-symlink regular file")
    try:
        document = json.loads(index_path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalKBFrameworkFault(
            "KB index is not valid UTF-8 JSON") from exc
    if (not isinstance(document, dict)
            or not _INDEX_REQUIRED_FIELDS.issubset(document)):
        raise ExternalKBFrameworkFault(
            "KB index fields differ from the registered package contract")
    items_raw = document.get("items")
    package_id = document.get("package_id")
    version = document.get("version")
    if (document.get("schema_version") != INDEX_SCHEMA
            or not isinstance(package_id, str) or not package_id
            or not isinstance(version, str) or not version
            or not isinstance(items_raw, list)
            or document.get("n_items") != len(items_raw)):
        raise ExternalKBFrameworkFault(
            "KB package identity or cardinality is malformed")

    items: list[ExternalKBItem] = []
    ids: set[str] = set()
    paths: set[str] = set()
    for ordinal, raw in enumerate(items_raw):
        if (not isinstance(raw, dict)
                or not _ITEM_REQUIRED_FIELDS.issubset(raw)):
            raise ExternalKBFrameworkFault(f"KB item {ordinal} fields differ")
        item_id = raw.get("item_id")
        relative = raw.get("path")
        size = raw.get("byte_size")
        title = raw.get("title")
        kind = raw.get("kind")
        tags = raw.get("tags")
        summary = raw.get("summary_2_lines")
        if (not isinstance(item_id, str) or not item_id
                or not isinstance(relative, str)
                or isinstance(size, bool) or not isinstance(size, int) or size < 0
                or not isinstance(title, str) or not isinstance(kind, str)
                or not isinstance(tags, list)
                or any(not isinstance(tag, str) for tag in tags)
                or not isinstance(summary, str)):
            raise ExternalKBFrameworkFault(
                f"KB item {ordinal} metadata is malformed")
        if item_id in ids or relative in paths:
            raise ExternalKBFrameworkFault(
                "KB index contains duplicate item IDs or paths")
        _safe_item_path(root, relative, require_file=True)
        ids.add(item_id)
        paths.add(relative)
        items.append(ExternalKBItem(
            item_id, relative, size, title, kind, tuple(tags), summary))

    package = ExternalKBPackage(root, package_id, version, tuple(items))
    if expected is not None and dict(expected) != package.descriptor():
        raise ExternalKBFrameworkFault(
            "KB package identity differs from registered authority")
    return package


def registered_external_kb_package(
    descriptor: Mapping[str, object],
) -> ExternalKBPackage:
    """Reconstruct a searchable package solely from registered metadata."""

    try:
        root_anchor = descriptor["root_anchor"]
        package_id = descriptor["package_id"]
        version = descriptor["version"]
        items_raw = descriptor["items"]
    except (KeyError, TypeError) as exc:
        raise ExternalKBFrameworkFault(
            "registered KB package descriptor is malformed") from exc
    if (not isinstance(root_anchor, str) or not root_anchor
            or not isinstance(package_id, str) or not package_id
            or not isinstance(version, str) or not version
            or not isinstance(items_raw, list)):
        raise ExternalKBFrameworkFault(
            "registered KB package descriptor is malformed")
    items: list[ExternalKBItem] = []
    item_ids: set[str] = set()
    item_paths: set[str] = set()
    for ordinal, raw in enumerate(items_raw):
        if not isinstance(raw, Mapping):
            raise ExternalKBFrameworkFault(
                f"registered KB item {ordinal} metadata is malformed")
        try:
            item_id = raw["item_id"]
            path = raw["path"]
            byte_size = raw["byte_size"]
            title = raw["title"]
            kind = raw["kind"]
            tags = raw["tags"]
            summary = raw["summary_2_lines"]
        except KeyError as exc:
            raise ExternalKBFrameworkFault(
                f"registered KB item {ordinal} metadata is malformed") from exc
        if (not isinstance(item_id, str) or not item_id
                or not isinstance(path, str) or not path
                or isinstance(byte_size, bool) or not isinstance(byte_size, int)
                or byte_size < 0 or not isinstance(title, str)
                or not isinstance(kind, str) or not isinstance(tags, list)
                or any(not isinstance(tag, str) for tag in tags)
                or not isinstance(summary, str)
                or item_id in item_ids or path in item_paths):
            raise ExternalKBFrameworkFault(
                f"registered KB item {ordinal} metadata is malformed")
        item_ids.add(item_id)
        item_paths.add(path)
        items.append(ExternalKBItem(
            item_id, path, byte_size, title, kind, tuple(tags), summary))
    return ExternalKBPackage(Path(root_anchor), package_id, version, tuple(items))


def canonical_kb_graph_node(root: Path | None = None) -> dict[str, Any]:
    root = root or Path(__file__).resolve().parents[1] / "kb"
    return load_external_kb_package(root).graph_node()


def _package_identity(package: ExternalKBPackage) -> dict[str, str]:
    return {"package_id": package.package_id, "version": package.version}


def _canonical_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _tokens(value: str) -> set[str]:
    return set(_TOKEN.findall(_canonical_text(value)))


def _sorted_unique_strings(value: object, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)):
        raise ExternalKBToolError(f"query_kb {field} is invalid")
    return tuple(sorted(set(value)))


def _closed_search_arguments(
    arguments: Mapping[str, Any], policy: ExternalKBQueryPolicy,
) -> dict[str, Any]:
    allowed = {
        "operation", "query", "kind", "tags_any", "tags_all",
        "offset", "page_size",
    }
    if not isinstance(arguments, Mapping) or set(arguments) - allowed:
        raise ExternalKBToolError("query_kb search arguments are invalid")
    query_raw = arguments.get("query")
    kind = arguments.get("kind")
    offset = arguments.get("offset", 0)
    page_size = arguments.get(
        "page_size", policy.search_default_page_size)
    if (arguments.get("operation") != "search"
            or not isinstance(query_raw, str) or not query_raw.strip()
            or kind is not None and (not isinstance(kind, str) or not kind)
            or isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
            or isinstance(page_size, bool) or not isinstance(page_size, int)
            or page_size < 1):
        raise ExternalKBToolError("query_kb search arguments are invalid")
    return {
        "query": unicodedata.normalize("NFKC", query_raw).strip(),
        "kind": kind,
        "tags_any": list(_sorted_unique_strings(
            arguments.get("tags_any"), field="tags_any")),
        "tags_all": list(_sorted_unique_strings(
            arguments.get("tags_all"), field="tags_all")),
        "offset": offset,
        "page_size": min(page_size, policy.search_max_results),
    }


def search_external_kb_metadata(
    package: ExternalKBPackage, arguments: Mapping[str, Any],
    policy: ExternalKBQueryPolicy,
) -> ExternalKBSearchPage:
    """Return one deterministic Unicode metadata page without body access."""

    if not isinstance(package, ExternalKBPackage):
        raise TypeError("external KB search requires a loaded package")
    options = _closed_search_arguments(arguments, policy)
    query = str(options["query"])
    query_tokens = _tokens(query)
    requested_kind = options["kind"]
    tags_any = set(options["tags_any"])
    tags_all = set(options["tags_all"])
    ranked: list[ExternalKBSearchItem] = []
    for item in package.items:
        if (requested_kind is not None and item.kind != requested_kind
                or tags_any and not tags_any.intersection(item.tags)
                or tags_all and not tags_all.issubset(item.tags)):
            continue
        score = (
            5 * len(query_tokens & _tokens(item.title))
            + 5 * len(query_tokens & _tokens(item.item_id))
            + 3 * len(query_tokens & _tokens(" ".join(item.tags)))
            + 2 * len(query_tokens & _tokens(item.summary))
            + len(query_tokens & _tokens(item.kind))
        )
        if _canonical_text(query) in _canonical_text(item.title):
            score += 6
        if score > 0:
            ranked.append(ExternalKBSearchItem(item, score))
    ranked.sort(key=lambda candidate: (-candidate.score, candidate.item.item_id))
    offset = int(options["offset"])
    page_size = int(options["page_size"])
    page = tuple(ranked[offset:offset + page_size])
    next_offset = offset + len(page)
    if next_offset >= len(ranked):
        next_offset = None
    return ExternalKBSearchPage(
        package=_package_identity(package), query=options, items=page,
        offset=offset, page_size=page_size, total_results=len(ranked),
        next_offset=next_offset)


def _closed_read_arguments(
    arguments: Mapping[str, Any], policy: ExternalKBQueryPolicy,
) -> dict[str, Any]:
    allowed = {"operation", "item_id", "offset_chars", "max_chars"}
    if not isinstance(arguments, Mapping) or set(arguments) - allowed:
        raise ExternalKBToolError("query_kb read arguments are invalid")
    item_id = arguments.get("item_id")
    offset = arguments.get("offset_chars", 0)
    maximum = arguments.get("max_chars", policy.read_max_chars)
    if (arguments.get("operation") != "read"
            or not isinstance(item_id, str) or not item_id
            or isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
            or isinstance(maximum, bool) or not isinstance(maximum, int)
            or maximum < 1):
        raise ExternalKBToolError("query_kb read arguments are invalid")
    return {
        "item_id": item_id,
        "offset_chars": offset,
        "max_chars": min(maximum, policy.read_max_chars),
    }


def read_external_kb_item(
    package: ExternalKBPackage, arguments: Mapping[str, Any],
    policy: ExternalKBQueryPolicy,
) -> ExternalKBReadPage:
    """Read one exact item ID and return one bounded Unicode-character page."""

    if not isinstance(package, ExternalKBPackage):
        raise TypeError("external KB read requires a loaded package")
    options = _closed_read_arguments(arguments, policy)
    item = next((candidate for candidate in package.items
                 if candidate.item_id == options["item_id"]), None)
    if item is None:
        raise ExternalKBToolError("query_kb item_id is not registered")
    path = _safe_item_path(package.root, item.path, require_file=True)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ExternalKBFrameworkFault("external KB item read failed") from exc
    if len(payload) != item.byte_size:
        raise ExternalKBFrameworkFault(
            "external KB item size differs from registered inventory")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExternalKBFrameworkFault(
            "external KB item decoding failed") from exc
    start = min(int(options["offset_chars"]), len(text))
    end = min(len(text), start + int(options["max_chars"]))
    return ExternalKBReadPage(
        package=_package_identity(package), item=item, body=text[start:end],
        offset_chars=start, returned_chars=end - start,
        total_chars=len(text),
        next_offset_chars=end if end < len(text) else None)


def execute_external_kb_operation(
    package: ExternalKBPackage, arguments: Mapping[str, Any],
    policy: ExternalKBQueryPolicy,
) -> TransientExternalKBResult:
    """Execute exactly one query_kb search or read operation."""

    operation = arguments.get("operation") if isinstance(arguments, Mapping) else None
    if operation == "search":
        page = search_external_kb_metadata(package, arguments, policy)
        return TransientExternalKBResult(page.model_result(), ())
    if operation == "read":
        page = read_external_kb_item(package, arguments, policy)
        return TransientExternalKBResult(
            page.model_result(), (page.access_record(),))
    raise ExternalKBToolError("query_kb operation must be search or read")


def registry_external_kb_access(
    package: ExternalKBPackage, arguments: Mapping[str, Any],
    policy: ExternalKBQueryPolicy,
) -> tuple[TransientExternalKBResult, dict[str, Any]]:
    """Return one model result and its exact Registry projection."""

    transient = execute_external_kb_operation(package, arguments, policy)
    result = transient.result
    if result["operation"] == "search":
        query = result["query"]
        page = result["page"]
        fields = {
            "operation": "search",
            "query": query["query"],
            "kind": query["kind"],
            "tags_any": list(query["tags_any"]),
            "tags_all": list(query["tags_all"]),
            "search_offset": page["offset"],
            "page_size": page["page_size"],
            "total_search_results": page["total_results"],
            "next_search_offset": page["next_offset"],
            "item_id": None,
            "offset_chars": None,
            "max_chars": None,
            "metadata_results": [
                {**dict(item), "rank": rank}
                for rank, item in enumerate(result["items"], 1)
            ],
            "body_pages": [],
            "outcome": "selected" if result["items"] else "empty",
        }
        return transient, fields
    item = result["item"]
    page = result["page"]
    fields = {
        "operation": "read",
        "query": None,
        "kind": None,
        "tags_any": [],
        "tags_all": [],
        "search_offset": None,
        "page_size": None,
        "total_search_results": None,
        "next_search_offset": None,
        "item_id": item["item_id"],
        "offset_chars": page["offset_chars"],
        "max_chars": min(
            int(arguments.get("max_chars", policy.read_max_chars)),
            policy.read_max_chars),
        "metadata_results": [],
        "body_pages": [{
            **dict(item),
            "body": result["body"],
            "offset_chars": page["offset_chars"],
            "returned_chars": page["returned_chars"],
            "total_chars": page["total_chars"],
            "next_offset_chars": page["next_offset_chars"],
            "complete": page["complete"],
        }],
        "outcome": (
            "read_complete" if page["complete"] else "read_partial"),
    }
    return transient, fields


def render_registered_external_kb_access(
    package: ExternalKBPackage, access: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Reconstruct a prior tool result solely from its registered access DTO."""

    if access.get("operation") == "search":
        items = [
            {key: item[key] for key in (
                "item_id", "byte_size", "title", "kind", "tags",
                "summary_2_lines", "score")}
            for item in access["metadata_results"]
        ]
        return {
            "kind": "external_kb_search/v1",
            "operation": "search",
            "package": _package_identity(package),
            "query": {
                "query": access["query"],
                "kind": access["kind"],
                "tags_any": list(access["tags_any"]),
                "tags_all": list(access["tags_all"]),
                "offset": access["search_offset"],
                "page_size": access["page_size"],
            },
            "items": items,
            "page": {
                "offset": access["search_offset"],
                "page_size": access["page_size"],
                "returned_items": len(items),
                "total_results": access["total_search_results"],
                "next_offset": access["next_search_offset"],
            },
        }
    pages = access.get("body_pages")
    if access.get("operation") != "read" or not isinstance(pages, list) \
            or len(pages) != 1:
        raise ExternalKBFrameworkFault(
            "registered KB access is not one closed search/read result")
    page = pages[0]
    return {
        "kind": "external_kb_read/v1",
        "operation": "read",
        "package": _package_identity(package),
        "item": {key: page[key] for key in (
            "item_id", "byte_size", "title", "kind", "tags",
            "summary_2_lines")},
        "body": page["body"],
        "page": {key: page[key] for key in (
            "offset_chars", "returned_chars", "total_chars",
            "next_offset_chars", "complete")},
    }


__all__ = [
    "EXTERNAL_KB_PURPOSE_CONTENT_COVERAGE_DESCRIPTION", "EXTERNAL_NODE_ID",
    "EXTERNAL_TOOL_NAME", "ExternalKBFrameworkFault",
    "ExternalKBToolError", "KB_QUERY_SKILL_DESCRIPTION", "KB_QUERY_SKILL_ID",
    "ExternalKBQueryPolicy", "ExternalKBItem",
    "ExternalKBPackage", "ExternalKBReadPage", "ExternalKBSearchItem",
    "ExternalKBSearchPage", "TransientExternalKBResult",
    "canonical_kb_graph_node", "execute_external_kb_operation",
    "load_external_kb_package", "read_external_kb_item",
    "registered_external_kb_package",
    "registry_external_kb_access", "render_registered_external_kb_access",
    "search_external_kb_metadata",
]
