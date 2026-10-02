"""Command-line entrypoint for the formal local RRSI v0.6 campaign."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _validated_run_root(value: str) -> Path:
    destination = Path(value).resolve(strict=False)
    module_path = Path(__file__).resolve()
    example_root = module_path.parents[2]
    source_module = example_root / "src" / "rpnh_rrsi" / "formal_cli.py"
    protected_root: Path | None = None
    if ((example_root / "pyproject.toml").is_file()
            and source_module.resolve() == module_path):
        repository_root = example_root.parents[1]
        if ((repository_root / "pyproject.toml").is_file()
                and (repository_root / "examples" / "rrsi_v06").resolve()
                == example_root):
            protected_root = repository_root
        else:
            protected_root = example_root
    if (protected_root is not None
            and (destination == protected_root
                 or destination.is_relative_to(protected_root))):
        raise ValueError(
            "--run-root must be outside the source checkout")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rpnh-rrsi")
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--execution", required=True)
    parser.add_argument("--protocol", required=True)
    args = parser.parse_args(argv)

    try:
        run_root = _validated_run_root(args.run_root)
    except ValueError as exc:
        parser.error(str(exc))

    from cpn.llm_adapters import load_llm_execution_selection
    from .formal_campaign import run_formal_campaign
    from .formal_protocol import load_formal_protocol

    protocol = load_formal_protocol(Path(args.protocol).resolve())
    selection = load_llm_execution_selection(Path(args.execution).resolve())
    report = run_formal_campaign(
        run_dir=run_root, protocol=protocol, selection=selection)
    print(json.dumps({
        "result": ("formal_campaign_complete"
                   if report["formal_rrsi_v06_local_complete"]
                   else "formal_campaign_incomplete"),
        "scope": report["scope"],
        "campaign_complete": report["campaign_complete"],
        "formal_rrsi_v06_local_complete": report[
            "formal_rrsi_v06_local_complete"],
        "h0_evolve_score": report["h0_evolve"]["aggregate"]["score"],
        "hfinal_evolve_score": report[
            "hfinal_evolve"]["aggregate"]["score"],
        "h0_heldout_score": report[
            "h0_heldout"]["aggregate"]["score"],
        "hfinal_heldout_score": report[
            "hfinal_heldout"]["aggregate"]["score"],
    }, ensure_ascii=False))
    return 0 if report["formal_rrsi_v06_local_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
