#!/usr/bin/env python3
"""Search Python source files with exact and regular-expression rules."""
import argparse
import json
import re
import sys
from pathlib import Path


def fail(message):
    print(message, file=sys.stderr)
    return 2


def parse_rules(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            rules = json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read rules: {exc}") from exc
    if not isinstance(rules, list):
        raise ValueError("rules file must contain a JSON array")
    seen = set()
    parsed = []
    flag_values = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("each rule must be an object")
        rule_id, kind, pattern = rule.get("id"), rule.get("kind"), rule.get("pattern")
        if not isinstance(rule_id, str) or not rule_id:
            raise ValueError("rule id must be a non-empty string")
        if rule_id in seen:
            raise ValueError("rule ids must be unique")
        seen.add(rule_id)
        if kind not in ("exact", "regex"):
            raise ValueError("rule kind must be exact or regex")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("rule pattern must be a non-empty string")
        languages = rule.get("languages", ["python"])
        if not isinstance(languages, list) or any(not isinstance(x, str) or x != "python" for x in languages):
            raise ValueError("languages may only contain python strings")
        flags = rule.get("regex_flags", [])
        if kind == "regex":
            if not isinstance(flags, list) or any(not isinstance(x, str) or x not in flag_values for x in flags):
                raise ValueError("invalid regex_flags")
            value = 0
            for flag in flags:
                value |= flag_values[flag]
            try:
                matcher = re.compile(pattern, value)
            except re.error as exc:
                raise ValueError(f"invalid regex for {rule_id}: {exc}") from exc
        else:
            if "regex_flags" in rule:
                raise ValueError("regex_flags is only allowed for regex rules")
            matcher = None
        parsed.append((rule_id, kind, pattern, matcher))
    return parsed


def position(text, index):
    line = text.count("\n", 0, index) + 1
    previous = text.rfind("\n", 0, index)
    return {"line": line, "col": index - previous}


def find_exact(text, pattern):
    start = 0
    while True:
        index = text.find(pattern, start)
        if index < 0:
            return
        yield index, index + len(pattern), pattern
        start = index + len(pattern)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("root_dir")
    parser.add_argument("--rules", required=True)
    parser.add_argument("--encoding", default="utf-8")
    args = parser.parse_args(argv)
    try:
        rules = parse_rules(args.rules)
    except ValueError as exc:
        return fail(str(exc))
    root = Path(args.root_dir)
    if not root.is_dir():
        return fail(f"root_dir is not a directory: {root}")
    results = []
    try:
        files = sorted((p for p in root.rglob("*.py") if p.is_file()), key=lambda p: p.relative_to(root).as_posix())
    except OSError as exc:
        return fail(str(exc))
    for path in files:
        try:
            text = path.read_text(encoding=args.encoding)
        except (OSError, UnicodeError, LookupError):
            continue
        relative = path.relative_to(root).as_posix()
        for rule_id, kind, pattern, matcher in rules:
            matches = find_exact(text, pattern) if kind == "exact" else ((m.start(), m.end(), m.group(0)) for m in matcher.finditer(text))
            for start, end, matched in matches:
                results.append({"rule_id": rule_id, "file": relative, "language": "python", "start": position(text, start), "end": position(text, end), "match": matched})
    results.sort(key=lambda item: (item["file"], item["start"]["line"], item["start"]["col"], item["rule_id"]))
    for item in results:
        print(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
