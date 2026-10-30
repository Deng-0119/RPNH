"""Explicit application update controls; no provider or Registry initialization."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from . import updates


def _entry(value: str) -> str:
    value = {"dsh": "dsh-web"}.get(value, value)
    if value not in updates.ENTRIES:
        raise argparse.ArgumentTypeError("choose basic, opencode, dsh-web, dsh-legacy or dsh-operator")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rpnh update", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "check"):
        command = commands.add_parser(name, help="inspect eligible official RPNH releases")
        command.add_argument("--frontend", type=_entry, default="basic")
        command.add_argument("--json", action="store_true")
        command.add_argument("--refresh", action="store_true")
        command.add_argument("--automatic", action="store_true",
                             help="native startup bridge: honor automatic off and 24-hour throttle")
    channel = commands.add_parser("channel", help="explicitly choose stable or prerelease offers")
    channel.add_argument("value", choices=updates.CHANNELS)
    reset = commands.add_parser("reset-skip", help="allow a previously skipped version again")
    reset.add_argument("--frontend", type=_entry)
    choose = commands.add_parser("choose", help="record a human UI choice; never runs an installer")
    choose.add_argument("--frontend", type=_entry, required=True)
    choose.add_argument("--release", required=True)
    choose.add_argument("--choice", choices=("update", "later", "skip"), required=True)
    choose.add_argument("--json", action="store_true")
    install = commands.add_parser("install", help="explicitly bootstrap a new managed-v1 environment")
    install.add_argument("--root", type=Path, required=True)
    install.add_argument("--frontend", type=_entry, required=True)
    install.add_argument("--version", required=True)
    rollback = commands.add_parser("rollback", help="switch to retained previous environment, not restore data")
    rollback.add_argument("--root", type=Path)
    return parser


def _status(options) -> dict:
    reason = "no-supported-release"
    if options.automatic and (options.refresh or options.command == "check"):
        raise ValueError("automatic and explicit refresh are mutually exclusive")
    if options.automatic and os.environ.get("RPNH_UPDATES", "on") == "off":
        return {"schema_version": "rpnh/update_status/v1", "entry": options.frontend,
                "offer": None, "reason": "disabled"}
    if options.automatic:
        try:
            updates.automatic_refresh_sync()
        except (OSError, ValueError, TypeError):
            reason = "check-unavailable"
    elif options.command == "check" or options.refresh:
        try:
            updates.refresh()
        except (OSError, ValueError, TypeError):
            reason = "check-unavailable"
    offer = updates.status(options.frontend)
    result = None
    if offer is not None:
        result = {key: value for key, value in asdict(offer).items()
                  if key not in ("wheel_url", "evidence", "entry")}
        # This machine API records choices or gives manual guidance only. It
        # cannot elevate a model/tool request into an install authorization.
        result.update(choices=["update", "later", "skip"], mode="manual")
        reason = "available"
    return {"schema_version": "rpnh/update_status/v1", "entry": options.frontend,
            "offer": result, "reason": reason}


def _confirm(message: str) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("installation requires explicit confirmation in a terminal")
    print(message)
    if input("Type UPDATE to continue: ").strip() != "UPDATE":
        raise ValueError("update cancelled; no installation changed")


def main(argv=None) -> int:
    options = _parser().parse_args(argv)
    try:
        if options.command in ("status", "check"):
            value = _status(options)
            if options.json:
                print(json.dumps(value, ensure_ascii=False))
            elif value["offer"]:
                offer = updates.status(options.frontend)
                print(f"Available: RPNH {offer.rpnh_version} / {offer.entry} "
                      f"{offer.upstream_version} ({offer.channel})")
                print(updates.manual_guidance(offer))
            else:
                print("No verified published RPNH update is available for this entry/channel. "
                      "Source candidates and upstream latest are not update offers.")
        elif options.command == "channel":
            updates.mutate_state(lambda value: value.update(
                channel=options.value, offers=[], checked_at=0, last_attempt=0))
            print(f"RPNH update channel: {options.value}")
        elif options.command == "reset-skip":
            def reset(value):
                value["skipped"] = ([] if options.frontend is None else
                    [key for key in value["skipped"] if not key.startswith(options.frontend + ":")])
                value["later"] = ({} if options.frontend is None else
                    {key: time for key, time in value["later"].items()
                     if not key.startswith(options.frontend + ":")})
            updates.mutate_state(reset)
            print("Update reminder preferences reset.")
        elif options.command == "choose":
            offer = updates.status(options.frontend)
            if offer is None or offer.tag != options.release:
                raise ValueError("the offered release is no longer eligible")
            result = "manual" if options.choice == "update" else (
                "deferred" if options.choice == "later" else "skipped")
            # Even update/manual is deferred so repeated UI activation does not
            # immediately repeat the same notice. It is not permanent suppression.
            updates.record_choice(offer, "skip" if options.choice == "skip" else "later")
            value = {"schema_version": "rpnh/update_choice/v1", "entry": offer.entry,
                     "release": offer.tag, "choice": options.choice, "result": result}
            if options.choice == "update":
                value["instructions"] = updates.manual_guidance(offer)
            print(json.dumps(value, ensure_ascii=False) if options.json else
                  value.get("instructions", result))
        elif options.command == "install":
            from .update_install import ENTRY_FRONTEND, install_managed
            if options.frontend not in ENTRY_FRONTEND:
                raise ValueError("this frontend uses its documented manual package/source procedure")
            offers = updates.discover(channel=updates.read_state()["channel"], installed=None)
            offer = next((o for o in offers if o.entry == options.frontend
                          and o.rpnh_version == options.version), None)
            if offer is None:
                raise ValueError("no eligible published release; managed installation was not created")
            _confirm(f"Install published RPNH {offer.rpnh_version} with {offer.entry} "
                     f"{offer.upstream_version} ({offer.channel}) into a NEW isolated directory. "
                     "Downloads packages; does not change PATH, models, tasks or databases.")
            command = install_managed(offer, root=options.root, bootstrap=True)
            print(f"Verified managed installation ready: {command}\n"
                  "Start it explicitly; your old installation and data are retained.")
        elif options.command == "rollback":
            from .update_install import managed_root, rollback
            root = options.root or managed_root()
            if root is None:
                raise ValueError("not a managed-v1 installation")
            _confirm("Switch to the retained previous environment. Stop/checkpoint all managed tasks first. "
                     "This does not restore databases or undo external effects.")
            command = rollback(root)
            print(f"Previous environment activated. Restart with {command}; no task was resumed.")
        return 0
    except (OSError, RuntimeError, ValueError, TypeError):
        if getattr(options, "json", False):
            print(json.dumps({"schema_version": "rpnh/update_error/v1", "error": "unavailable"}))
        else:
            print("RPNH update unavailable or cancelled. No new environment was activated. "
                  "Only verified official releases and a dedicated managed-v1 layout are supported; "
                  "source/editable/mixed installations require manual update. "
                  "Stop active managed tasks and retry explicitly if appropriate.", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("RPNH update cancelled; no automatic task resume.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

