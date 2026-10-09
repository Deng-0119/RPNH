"""Sequential manual native gate. Missing binaries are BLOCKED, never installed.

This orchestrator runs only after separate authorization on a native environment.
Every TUI must be manually scrolled to the oldest item, reviewed and exited.
The JSON status never declares certification solely from process exit codes.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory")
    parser.add_argument("--codex-0155", type=Path)
    parser.add_argument("--codex-0161", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    pair = json.loads((args.fixtures / "pair.json").read_text())
    if args.fixtures.resolve() != Path(pair["canonical_root"]):
        raise SystemExit("Synthetic roots moved; canonical identity gate is invalid")
    sys.path.insert(0, str(repo))
    from cpn.frontend.codex_app_server import resolve_codex_binary, _compatibility_profile
    report = {"schema": "rpnh/codex_candidate_native_pair/v1", "lanes": [],
              "status": "NATIVE_CERTIFICATION_NOT_ESTABLISHED"}
    def save():
        (output / "status.json").write_text(json.dumps(report, indent=2) + "\n")
    for selector, supplied in (("pinned-0.155.0", args.codex_0155),
                               ("candidate-0.161.0", args.codex_0161)):
        profile = _compatibility_profile(selector)
        lane = {"profile": selector, "version": profile.version, "runs": []}
        report["lanes"].append(lane)
        if supplied is None or not supplied.is_file():
            lane.update(status="BLOCKED", reason="Explicit already-installed binary is missing")
            save()
            continue
        try:
            binary = Path(resolve_codex_binary(str(supplied.resolve()), compatibility_profile=selector))
        except Exception as exc:
            lane.update(status="BLOCKED", reason=str(exc))
            save()
            continue
        lane.update(binary_realpath=str(binary.resolve()),
                    binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                    verified_reported_cli_version=profile.reported_cli_version,
                    viewport=list(os.get_terminal_size()) if sys.stdin.isatty() else None)
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            lane.update(status="BLOCKED", reason="Interactive native PTY is required")
            save()
            continue
        for fixture_name in ("cold", "pending"):
            for opening in ("first", "reopen"):
                log = output / f"{profile.version}-{fixture_name}-{opening}.jsonl"
                print(f"Review {profile.version} / {fixture_name} / {opening}; scroll to oldest, then exit TUI.", flush=True)
                result = subprocess.run([sys.executable, str(Path(__file__).with_name("run_profile_gate.py")),
                    "--repo", str(repo), "--fixture", str((args.fixtures / fixture_name).resolve()),
                    "--codex", str(binary), "--profile", selector, "--log", str(log)])
                lane["runs"].append({"fixture": fixture_name, "opening": opening,
                                    "exit_code": result.returncode, "log": log.name,
                                    "status": "RPC_AND_UI_REVIEW_REQUIRED" if result.returncode == 0 else "BLOCKED"})
                save()
                if result.returncode:
                    break
            if lane["runs"][-1]["exit_code"]:
                break
        lane["status"] = "RPC_AND_UI_REVIEW_REQUIRED" if all(x["exit_code"] == 0 for x in lane["runs"]) else "BLOCKED"
        save()
    save()


if __name__ == "__main__":
    main()
