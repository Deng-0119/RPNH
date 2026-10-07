"""Discover, export and verify installed editable examples without execution."""
from __future__ import annotations

import argparse
import importlib.resources
import json
import math
from pathlib import Path
from typing import Any, Sequence

from cpn.examples.gallery import catalog, export_files, write_export


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
    manifest = _read_json("manifest.json")
    # Preserve the original adapter manifest API while adding discovery.
    manifest["catalog_schema_version"] = catalog()["schema_version"]
    manifest["examples"] = catalog()["examples"]
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _export(output: Path, example_id: str = "adapter_task") -> int:
    supplied = output.expanduser()
    destination = supplied.resolve()
    if supplied.is_symlink() or destination.exists():
        raise ValueError("example output directory must not already exist")
    # Resolve every declared resource before creating any output directory.
    manifest, copied = (None, None) if example_id == "adapter_task" else export_files(example_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if copied is None:
        _copy_tree(_bundle(), destination)
        exported_id = _read_json("manifest.json")["example_id"]
    else:
        write_export(destination, copied)
        exported_id = manifest["example_id"]
    print(json.dumps({
        "schema_version": "rpnh/example_export/v1",
        "example_id": exported_id,
        "output": str(destination),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


_SUMMARY_KEYS = ["count", "total", "mean", "minimum", "maximum"]


def _finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _validate_expected(expected: Any) -> None:
    if not isinstance(expected, dict) or list(expected) != _SUMMARY_KEYS:
        raise ValueError("expected must contain exactly count, total, mean, minimum, maximum in order")
    for value in expected.values():
        if not _finite_number(value):
            raise ValueError("expected fields must be finite, representable JSON numbers")
    count = expected["count"]
    if not isinstance(count, int) or count <= 0:
        raise ValueError("expected count must be a positive integer")
    if not expected["minimum"] <= expected["mean"] <= expected["maximum"]:
        raise ValueError("expected mean must be within minimum and maximum")
    if not math.isclose(expected["total"] / count, expected["mean"], rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError("expected total/count must equal mean")


def _verify(result: Path, expected_path: Path | None = None) -> int:
    actual = _decode_json(result.read_text(encoding="utf-8"))
    expected = (_read_json("expected.json") if expected_path is None else
                _decode_json(expected_path.read_text(encoding="utf-8")))
    _validate_expected(expected)
    if not isinstance(actual, dict) or list(actual) != list(expected):
        raise ValueError(
            "result must be one JSON object with exactly the expected keys in order")
    for key, expected_value in expected.items():
        value = actual[key]
        if not _finite_number(value) or value != expected_value:
            raise ValueError(f"result field does not match the task contract: {key}")
    print(json.dumps({
        "schema_version": "rpnh/example_verification/v1",
        "example_id": _read_json("manifest.json")["example_id"],
        "status": "PASS",
        "expected_source": "installed-stock" if expected_path is None else "explicit-local-file",
        "normalized_result": actual,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rpnh examples",
        description="Discover and export editable RPNH examples without selecting a model.")
    commands = parser.add_subparsers(dest="command", required=True)
    listed = commands.add_parser("list", help="show installed exports and source-only gallery prerequisites")
    listed.set_defaults(handler=lambda _args: _list())
    exported = commands.add_parser("export", help="copy the example bundle to an absent directory")
    exported.add_argument("--output", required=True, type=Path)
    exported.add_argument("--example", default="adapter_task", metavar="NAME",
                          help="example id from list; default: adapter_task")
    exported.set_defaults(handler=lambda args: _export(args.output, args.example))
    verified = commands.add_parser("verify", help="verify one answer-only JSON file")
    verified.add_argument("--result", required=True, type=Path)
    verified.add_argument("--expected", type=Path,
                          help="explicit edited batch-summary fixture; default: installed stock expected.json")
    verified.set_defaults(handler=lambda args: _verify(args.result, args.expected))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


__all__ = ("main",)


if __name__ == "__main__":
    raise SystemExit(main())
