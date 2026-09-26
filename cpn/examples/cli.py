"""Inspect, export and verify the installed cross-adapter live-task example."""
from __future__ import annotations

import argparse
import importlib.resources
import json
import math
from pathlib import Path
from typing import Any, Sequence


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("example JSON contains a duplicate key")
        result[key] = value
    return result


def _decode_json(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_unique_object,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"example JSON contains non-finite value: {value}")),
    )


def _bundle():
    return importlib.resources.files("cpn.examples").joinpath("adapter_task")


def _read_json(name: str) -> Any:
    return _decode_json(_bundle().joinpath(name).read_text(encoding="utf-8"))


def _copy_tree(source, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            _copy_tree(child, target)
        elif child.is_file():
            target.write_bytes(child.read_bytes())


def _list() -> int:
    print(json.dumps(
        _read_json("manifest.json"), ensure_ascii=False, indent=2,
        sort_keys=True))
    return 0


def _export(output: Path) -> int:
    destination = output.expanduser().resolve()
    if destination.exists():
        raise ValueError("example output directory must not already exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _copy_tree(_bundle(), destination)
    print(json.dumps({
        "schema_version": "rpnh/example_export/v1",
        "example_id": _read_json("manifest.json")["example_id"],
        "output": str(destination),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _verify(result: Path) -> int:
    actual = _decode_json(result.read_text(encoding="utf-8"))
    expected = _read_json("expected.json")
    if not isinstance(actual, dict) or list(actual) != list(expected):
        raise ValueError(
            "result must be one JSON object with exactly the expected keys in order")
    for key, expected_value in expected.items():
        value = actual[key]
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or value != expected_value):
            raise ValueError(f"result field does not match the task contract: {key}")
    print(json.dumps({
        "schema_version": "rpnh/example_verification/v1",
        "example_id": _read_json("manifest.json")["example_id"],
        "status": "PASS",
        "normalized_result": actual,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rpnh examples",
        description="Export and verify installed RPNH example tasks without selecting a model.")
    commands = parser.add_subparsers(dest="command", required=True)
    listed = commands.add_parser("list", help="show the bundled task and host matrix")
    listed.set_defaults(handler=lambda _args: _list())
    exported = commands.add_parser("export", help="copy the example bundle to an absent directory")
    exported.add_argument("--output", required=True, type=Path)
    exported.set_defaults(handler=lambda args: _export(args.output))
    verified = commands.add_parser("verify", help="verify one answer-only JSON file")
    verified.add_argument("--result", required=True, type=Path)
    verified.set_defaults(handler=lambda args: _verify(args.result))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


__all__ = ("main",)


if __name__ == "__main__":
    raise SystemExit(main())
