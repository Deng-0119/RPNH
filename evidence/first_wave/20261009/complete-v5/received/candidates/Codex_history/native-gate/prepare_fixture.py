"""Create a new, synthetic Registry fixture. Never run a model or child task."""
from pathlib import Path
import argparse
import hashlib
import json
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Must not exist")
    parser.add_argument("--turns", type=int, default=60)
    parser.add_argument("--pending", action="store_true", help="Add an unexecuted pending-start turn")
    args = parser.parse_args()
    if not 2 <= args.turns <= 100:
        parser.error("turns must be from 2 through 100")
    repo = args.repo.resolve()
    package = Path(__file__).resolve().parent.parent
    manifest = json.loads((package / "file-manifest.json").read_text())
    for row in manifest["files"]:
        path = repo / row["path"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise SystemExit(f"Frozen product input differs: {row['path']}")
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(repo), str(repo / "tests")]
    from pytest import MonkeyPatch
    from cpn.rpnh.main_session import MainSession
    from test_codex_compat import _local_profile
    from test_codex_history import _append
    execution = _local_profile(output / "profiles")
    session = MainSession(output / "session", execution, task_control=object())
    with MonkeyPatch.context() as patch:
        for ordinal in range(1, args.turns + 1):
            _append(session._main_thread, patch, ordinal, private=ordinal == 1,
                    text=f"Native history gate question {ordinal:03d}")
        if args.pending:
            native = session._main_thread
            accepted = native.accept_turn(
                thread_ref=native.capture_read_cut().thread_ref,
                expected_ordinal=args.turns + 1,
                user_input={"text": "Pending gate turn: never execute", "required_task_kind": None},
                idempotency_key="gate-pending-accept")
            native.attach_attempt(thread_ref=accepted.thread_ref, turn_ref=accepted.turn_ref,
                                  idempotency_key="gate-pending-attach")
    (output / "gate.json").write_text(json.dumps({
        "schema": "rpnh/codex_history_native_gate/v1", "turns": args.turns,
        "pending_start": args.pending, "provider_calls": 0, "child_launches": 0,
        "patch_sha256": manifest["patch_sha256"],
        "fixture_note": "Synthetic committed main Registry with fixture-only child observations; no actual child execution",
    }, indent=2) + "\n")
    print(f"Prepared {args.turns} committed turns; pending={args.pending}; zero provider/child calls.")


if __name__ == "__main__":
    main()
