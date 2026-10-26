"""Offline presentation help shared with the browser; no execution authority."""
from __future__ import annotations

import argparse
from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
import json
import re
import sys
from typing import Sequence


SCHEMA = "rpnh/help/v1"
LANGUAGES = ("en", "zh-CN")
MAX_CATALOG_BYTES = 65536
FALLBACK = (
    "Help data unavailable. Use rpnh --help or "
    "docs/guides/troubleshooting.md; reinstall a complete matching package.")


class HelpDataError(ValueError):
    """The installed static help cannot be read safely."""


class HelpUsageError(ValueError):
    """A safe, non-reflecting help argument error."""


def _text(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 2400
            and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value))


def _relative_path(value: object, prefix: str) -> bool:
    return (isinstance(value, str) and len(value) <= 240
            and value.startswith(prefix + "/")
            and re.fullmatch(r"[A-Za-z0-9_./-]+", value) is not None
            and all(part not in {"", ".", ".."} for part in value.split("/")))


def _validate(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"schema", "topics"}:
        raise HelpDataError(FALLBACK)
    if value["schema"] != SCHEMA or not isinstance(value["topics"], list):
        raise HelpDataError(FALLBACK)
    if not 1 <= len(value["topics"]) <= 32:
        raise HelpDataError(FALLBACK)
    seen = set()
    for topic in value["topics"]:
        if (not isinstance(topic, dict) or set(topic) != {
                "id", "title", "summary", "steps", "owner", "source_paths", "docs"}):
            raise HelpDataError(FALLBACK)
        identifier = topic["id"]
        if (not isinstance(identifier, str)
                or re.fullmatch(r"[a-z][a-z0-9-]{0,31}", identifier) is None
                or identifier in seen or not _text(topic["owner"])):
            raise HelpDataError(FALLBACK)
        seen.add(identifier)
        for field in ("title", "summary", "steps"):
            localized = topic[field]
            if not isinstance(localized, dict) or set(localized) != set(LANGUAGES):
                raise HelpDataError(FALLBACK)
            for language in LANGUAGES:
                content = localized[language]
                if field == "steps":
                    if (not isinstance(content, list) or not 1 <= len(content) <= 10
                            or not all(_text(item) for item in content)):
                        raise HelpDataError(FALLBACK)
                elif not _text(content):
                    raise HelpDataError(FALLBACK)
        for field, prefix in (("source_paths", "cpn"), ("docs", "docs")):
            paths = topic[field]
            if (not isinstance(paths, list) or not 1 <= len(paths) <= 8
                    or not all(_relative_path(path, prefix) for path in paths)):
                raise HelpDataError(FALLBACK)
    return value


@lru_cache(maxsize=1)
def _loaded_catalog() -> dict:
    try:
        resource = files("cpn.frontend").joinpath("static", "help-catalog.json")
        with resource.open("rb") as stream:
            payload = stream.read(MAX_CATALOG_BYTES + 1)
        if len(payload) > MAX_CATALOG_BYTES:
            raise HelpDataError(FALLBACK)
        return _validate(json.loads(payload))
    except (OSError, UnicodeError, ValueError, TypeError):
        raise HelpDataError(FALLBACK) from None


def help_catalog() -> dict:
    """Return detached static data; callers cannot mutate the cached catalog."""
    return deepcopy(_loaded_catalog())


def help_topics() -> tuple[str, ...]:
    try:
        return tuple(topic["id"] for topic in _loaded_catalog()["topics"])
    except HelpDataError:
        return ()


class _HelpParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        # Do not reflect arbitrary topic/options (including terminal escapes).
        raise HelpUsageError(
            "Use rpnh help [TOPIC] [--language en|zh-CN] [--json].")


def help_command(argv: Sequence[str]) -> int:
    """Render local help only; safe to call from an active Basic session."""
    parser = _HelpParser(
        prog="rpnh help", allow_abbrev=False,
        description="Offline help; does not call a model or change a run.")
    parser.add_argument("topic", nargs="?")
    parser.add_argument("--language", choices=LANGUAGES, default="en")
    parser.add_argument("--json", action="store_true", help="print static help as JSON")
    as_json = "--json" in argv
    try:
        try:
            args = parser.parse_args(list(argv))
        except SystemExit as exit_signal:
            return int(exit_signal.code or 0)
        catalog = help_catalog()
        selected = next((topic for topic in catalog["topics"]
                         if topic["id"] == args.topic), None)
        if args.topic is not None and selected is None:
            raise HelpUsageError("Unknown help topic. Available: "
                                 + ", ".join(topic["id"] for topic in catalog["topics"]))
        if args.json:
            print(json.dumps(catalog if selected is None else {
                "schema": SCHEMA, "topic": selected}, ensure_ascii=False, indent=2))
            return 0
        language = args.language
        if selected is None:
            print("Offline help topics" if language == "en" else "离线帮助主题")
            for topic in catalog["topics"]:
                print(f"  {topic['id']:<12} {topic['title'][language]}")
            print("\nrpnh help viewer --language zh-CN  |  /help viewer")
            return 0
        print(selected["title"][language] + "\n" + selected["summary"][language])
        for number, step in enumerate(selected["steps"][language], 1):
            print(f"  {number}. {step}")
        labels = (("Component", "Source", "Docs") if language == "en"
                  else ("组件", "源码位置", "文档"))
        print(f"\n{labels[0]}: {selected['owner']}")
        print(f"{labels[1]}: " + ", ".join(selected["source_paths"]))
        print(f"{labels[2]}: " + ", ".join(selected["docs"]))
        return 0
    except (HelpDataError, HelpUsageError) as error:
        if as_json:
            print(json.dumps({"schema": SCHEMA, "error": {
                "code": "help_unavailable" if isinstance(error, HelpDataError)
                else "help_usage", "summary": str(error)}}, ensure_ascii=False))
        else:
            print(str(error), file=sys.stderr)
        return 2


__all__ = ("help_catalog", "help_command", "help_topics")
