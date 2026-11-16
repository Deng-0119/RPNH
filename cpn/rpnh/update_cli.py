"""Explicit application update controls; no provider or Registry initialization."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from zipfile import BadZipFile

from . import updates


def _entry(value: str) -> str:
    value = {"dsh": "dsh-web"}.get(value, value)
    if value not in updates.ENTRIES:
        raise argparse.ArgumentTypeError("choose basic, codex, opencode, dsh-web, dsh-legacy or dsh-operator")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rpnh update", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "check"):
        command = commands.add_parser(name, help="inspect eligible official RPNH releases")
        command.add_argument("--frontend", type=_entry, default="basic")
        command.add_argument("--json", action="store_true")
        command.add_argument("--root", type=Path,
                             help="inspect an existing managed-v2 root; no root is created")
        command.add_argument("--refresh", action="store_true")
        command.add_argument("--automatic", action="store_true",
                             help="native startup bridge: honor automatic off and 24-hour throttle")
        if name == "check":
            command.add_argument("--channel", choices=updates.CHANNELS,
                                 help="channel for this v2 read-only check; does not change preferences")
            command.add_argument("--plan-release", help="exact vVERSION for optional v2 local-material planning")
            command.add_argument("--candidate-wheel", type=Path,
                                 help="explicit local target wheel; its official asset identity is not implied")
            command.add_argument("--materials", type=Path,
                                 help="fixed local materials to check without preparing or activating")
    channel = commands.add_parser("channel", help="explicitly choose stable or prerelease offers")
    channel.add_argument("value", choices=updates.CHANNELS)
    download = commands.add_parser("download", help="fetch one bound official wheel into a new private directory; never install")
    download.add_argument("--root", type=Path, required=True)
    download.add_argument("--release", required=True, help="exact newer vVERSION selected by the v2 resolver")
    download.add_argument("--directory", type=Path, required=True)
    download.add_argument("--channel", choices=updates.CHANNELS)
    download.add_argument("--json", action="store_true")
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
    bootstrap = commands.add_parser("bootstrap", help="bootstrap an explicit complete selection from exact official release materials")
    bootstrap.add_argument("--root", type=Path, required=True, help="new dedicated managed-v2 root")
    bootstrap.add_argument("--release", required=True, help="exact published vVERSION; no predecessor is required")
    bootstrap.add_argument("--channel", choices=updates.CHANNELS, default="stable")
    bootstrap.add_argument("--platform", type=Path, required=True, help="JSON exact observed target platform, including emulation")
    bootstrap.add_argument("--runtime-bindings", type=Path, required=True, help="JSON exact observed runtimes for the complete selected recipes")
    bootstrap.add_argument("--components", type=Path, help="JSON complete exact profiles and managed bindings; omission selects only Basic")
    bootstrap.add_argument("--select", choices=("auto", "basic", "codex", "opencode", "dsh", "dsh-web"),
                           help="display choice within the complete new installation (default: Basic)")
    bootstrap.add_argument("--combination", help="exact v3/v4 combination ID, required when the target has multiple choices")
    bootstrap.add_argument("--extra", action="append", default=[], choices=("mcp", "skills"),
                           help="explicit Python optional dependency; repeat to select both (default: none)")
    bootstrap.add_argument("--inspect-only", action="store_true", help="read the exact selection without acquisition or installation")
    bootstrap.add_argument("--json", action="store_true")
    reconfigure = commands.add_parser("reconfigure", help="change the complete selection within the same published release")
    reconfigure.add_argument("--root", type=Path, required=True, help="existing published managed-v2 root")
    reconfigure.add_argument("--release", required=True, help="the current exact published vVERSION")
    reconfigure.add_argument("--channel", choices=updates.CHANNELS, default="stable")
    reconfigure.add_argument("--platform", type=Path, required=True)
    reconfigure.add_argument("--runtime-bindings", type=Path, required=True,
                             help="exact target bindings; Python preserves its original base binding")
    reconfigure.add_argument("--components", type=Path,
                             help="complete target profiles and managed bindings; omission preserves the current set")
    reconfigure.add_argument("--select", choices=("auto", "basic", "codex", "opencode", "dsh", "dsh-web"),
                             help="display choice for the new installation; omission preserves the current choice")
    reconfigure.add_argument("--combination", help="exact v3/v4 combination ID")
    extras = reconfigure.add_mutually_exclusive_group(required=True)
    extras.add_argument("--extra", action="append", choices=("mcp", "skills"),
                        help="complete optional dependency selection; repeat to select both")
    extras.add_argument("--no-extras", action="store_true", help="explicitly select the empty optional dependency set")
    reconfigure.add_argument("--inspect-only", action="store_true")
    reconfigure.add_argument("--json", action="store_true")
    rollback = commands.add_parser("rollback", help="switch to retained previous environment, not restore data")
    rollback.add_argument("--root", type=Path)
    rollback.add_argument("--json", action="store_true")
    materials = commands.add_parser("materials", help="describe one exact local Basic wheel closure without installing")
    materials.add_argument("--candidate-wheel", type=Path, required=True)
    materials.add_argument("--wheelhouse", type=Path, required=True)
    materials.add_argument("--python", type=Path, help="explicit Python interpreter to observe; defaults to this Python")
    materials.add_argument("--extra", action="append", default=[],
                           help="explicit complete RPNH optional dependency selection; repeat for several extras")
    materials.add_argument("--output", type=Path, help="new JSON materials file; existing files are retained")
    apply = commands.add_parser("apply", help="prepare and activate fixed local materials in a dedicated managed-v2 root")
    apply.add_argument("--root", type=Path, required=True)
    apply.add_argument("--candidate-wheel", type=Path, required=True)
    apply.add_argument("--materials", type=Path, required=True)
    apply.add_argument("--components", type=Path,
                       help="explicit complete component requests; omission preserves the current set")
    apply.add_argument("--select", choices=("auto", "basic", "codex", "opencode", "dsh", "dsh-web"),
                       help="display choice for the complete new installation")
    apply.add_argument("--json", action="store_true")
    plan = commands.add_parser("plan", help="read a local candidate's complete v2 frontend plan without installing")
    plan.add_argument("--candidate-wheel", type=Path, required=True)
    plan.add_argument("--root", type=Path, help="read a v2 root's complete selected set; no root is created")
    selection = plan.add_mutually_exclusive_group()
    selection.add_argument("--frontend", choices=("basic", "codex", "opencode", "dsh", "dsh-web"),
                           action="append", help="explicit complete target set; Basic is always included")
    selection.add_argument("--components", type=Path, help="JSON component requests with exact profiles and bindings")
    plan.add_argument("--runtime-bindings", type=Path, help="JSON exact runtime observations; this command does not qualify them")
    plan.add_argument("--materials", type=Path, help="check fixed local materials and use their exact runtime observations")
    plan.add_argument("--json", action="store_true")
    recover = commands.add_parser("recover", help="finish or abort a v2 pointer transaction according to current")
    recover.add_argument("--root", type=Path, required=True)
    recover.add_argument("--json", action="store_true")
    return parser


def _local_plan(options) -> dict:
    import platform
    from .install_contract import (
        build_install_plan, get_profile, load_profile_catalog, read_software_record,
    )
    from .install_v2 import read_target
    from .update_install import lifecycle_lease
    if getattr(options, "materials", None) is not None:
        from .install_executor import plan_local_install
        from .install_materials import selected_root_extras
        if options.root is None:
            raise ValueError("materials planning requires the explicit intended managed root")
        if options.runtime_bindings is not None:
            raise ValueError("materials already identify the exact runtime bindings")
        requested = None
        if options.components is not None:
            requested = read_software_record(options.components)
        elif options.frontend is not None:
            requested = {("dsh-web" if entry == "dsh" else entry): {} for entry in options.frontend}
        plan = plan_local_install(options.candidate_wheel, options.materials,
                                  root=options.root, requested=requested)
        material_document = read_software_record(options.materials, limit=1024 * 1024)
        return {"plan": plan.to_document(), "root_extras": list(selected_root_extras(material_document)), "qualification": {
            "release": "local_candidate_only", "dependencies": "materials_checked_not_installed",
            "runtime_health": "not_verified", "native_platform": "not_verified",
            "activation": "explicit_apply_required",
        }}
    wheel = options.candidate_wheel.expanduser().resolve(strict=True)
    catalog = load_profile_catalog(wheel=wheel)
    current = None
    if options.root is not None and (options.root.exists() or options.root.is_symlink()):
        with lifecycle_lease(options.root, exclusive=False):
            selected = read_target(options.root, allow_missing=True)
            current = None if selected is None else selected[2]
    runtime = ({"python": {
        "path": str(Path(getattr(sys, "_base_executable", sys.executable)).resolve()),
        "version": platform.python_version(), "implementation": platform.python_implementation().lower(),
        "architecture": platform.machine(),
    }} if current is None else current["runtime_bindings"])
    if options.runtime_bindings is not None:
        runtime = read_software_record(options.runtime_bindings)
    requested = None
    if options.components is not None:
        requested = read_software_record(options.components)
    elif options.frontend is not None:
        requested = {}
        for entry in dict.fromkeys(["basic", *("dsh-web" if name == "dsh" else name for name in options.frontend)]):
            profile = get_profile(entry, catalog=catalog)
            requested[entry] = {"profile": profile.profile, "binding": {"kind": "managed"}}
    libc_name, libc_version = platform.libc_ver()
    system = {"Linux": "linux", "Darwin": "darwin"}.get(platform.system(), platform.system().lower())
    target_platform = {
        "os": system, "architecture": platform.machine(), "os_release": platform.release(),
        "libc": ({"family": libc_name, "version": libc_version} if libc_name else None),
        "emulated": None,
    }
    source = {"kind": "local_candidate", "rpnh_version": catalog.rpnh_version,
              "release": None, "channel": None,
              "artifact": {"filename": wheel.name, "location": str(wheel)}}
    plan = build_install_plan(source, target_platform, runtime, requested=requested, current=current, catalog=catalog)
    return {"plan": plan.to_document(), "qualification": {
        "release": "local_candidate_only", "dependencies": "not_verified",
        "runtime_health": "not_verified", "native_platform": "not_verified",
        "activation": "not_enabled_without_fixed_materials_and_admission",
    }}


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


def _v2_status(options, root: Path) -> dict:
    if options.automatic and (options.refresh or options.command == "check"):
        raise ValueError("automatic and explicit refresh are mutually exclusive")
    # Keep the existing native notice envelope, with no v2 declaration ever
    # becoming a single-entry v1 Offer. Startup does not perform this check.
    if options.automatic:
        return {"schema_version": "rpnh/update_status/v1", "entry": options.frontend,
                "offer": None, "reason": "disabled" if os.environ.get("RPNH_UPDATES", "on") == "off"
                else "v2-explicit-complete-set-check-required"}
    if options.refresh:
        raise ValueError("v2 status does not refresh; use explicit update check")
    from .install_contract import PRIMARY_ENTRIES, V2_PROTOCOL
    if options.frontend not in PRIMARY_ENTRIES:
        raise ValueError("this auxiliary entry is outside the managed-v2 complete primary set")
    planning = None
    inputs = [getattr(options, name, None) for name in ("plan_release", "candidate_wheel", "materials")]
    if any(value is not None for value in inputs):
        if not all(value is not None for value in inputs):
            raise ValueError("local planning needs --plan-release, --candidate-wheel and --materials together")
        from .published_releases import LocalMaterialRequest
        planning = LocalMaterialRequest(*inputs)
    channel = getattr(options, "channel", None) or updates.read_state()["channel"]
    report = updates.discover_v2(root, channel=channel, check=options.command == "check", planning=planning)
    return {"schema_version": "rpnh/update_status/v2", "protocol": V2_PROTOCOL,
            "entry": options.frontend, "offer": None, "installable": False,
            "reason": "v2-declarations-only" if report["checked"] else "v2-explicit-check-required",
            "discovery": report}


def _render_v2_status(value: dict) -> str:
    def gap_lines(gaps, indent):
        text = []
        if not gaps["combinations"]:
            text.append(indent + "No exact backed combination; missing acceptance references: "
                        + ", ".join(gaps["unmatched_installation_requirements"]))
        for record in gaps["combinations"]:
            missing = set()
            for group in ("installation", "same_release_reconfiguration", "upgrade", "rollback", "ui_profile_rollback"):
                missing.update(record[group]["missing_case_ids"])
            text.append(indent + f"Acceptance [{record['combination']}] case references missing: "
                        + (", ".join(sorted(missing)) or "none in metadata"))
            for entry, coverage in record.get("entry_installation", {}).items():
                text.append(indent + f"  {entry} case references missing: "
                            + (", ".join(coverage["missing_case_ids"]) or "none in metadata"))
            for entry, coverage in record.get("entry_reconfiguration", {}).items():
                text.append(indent + f"  {entry} reconfiguration references missing: "
                            + (", ".join(coverage["missing_case_ids"]) or "none in metadata"))
        text.append(indent + "Case labels do not verify per-entry T02 execution or persistent write capabilities.")
        text.append(indent + "Dependency materials qualification: " + gaps["dependency_materials"]["status"]
                    + " (also applies to Basic with no extras).")
        if gaps["root_extras"]["status"] != "no_optional_selection":
            text.append(indent + "Root extras qualification: " + gaps["root_extras"]["status"])
        return text

    report = value.get("discovery")
    if report is None:
        return ("Automatic update notices are disabled." if value["reason"] == "disabled" else
                "V2 automatic notices do not fetch declarations; use explicit update check.")
    current = report["current"]
    selected = ", ".join(f"{name}:{item['profile']}" for name, item in current["components"].items())
    lines = [f"Current RPNH {current['rpnh_version']} / {current['installation_id']}",
             f"Complete selected frontends: {selected}"]
    if not report["checked"]:
        lines.append("Current selection inspected without network access; use update check for published declarations.")
    else:
        if any(row["code"] == "release_index_unavailable_or_invalid" for row in report["diagnostics"]):
            lines.append("Release index unavailable or invalid; no declaration was obtained.")
        elif not report["releases"]:
            lines.append("No newer release declarations passed the fixed release, asset and sidecar checks.")
        for release in report["releases"]:
            lines.append(f"{release['source']['release']}: {len(release['matching_combinations'])} "
                         "complete-set metadata matches; "
                         f"upgrade pairs={len(release['upgrade_from'])}, rollback pairs={len(release['rollback_to'])}")
            codes = sorted({row["code"] for row in release["diagnostics"]})
            if codes:
                lines.append("  diagnostics: " + ", ".join(codes))
            if "qualification_gaps" in release:
                lines.extend(gap_lines(release["qualification_gaps"], "  "))
        codes = sorted({row["code"] for row in report["diagnostics"]})
        if codes:
            lines.append("Discovery diagnostics: " + ", ".join(codes))
    local = report.get("local_material_plan")
    if local is not None:
        lines.append(f"Local material plan for {local['requested_release']}: {local['status']}")
        if local.get("plan") is not None:
            plan = local["plan"]
            lines.append("  complete target: " + ", ".join(
                f"{name}:{item['profile']}" for name, item in plan["components"].items()))
            for conflict in plan["conflicts"]:
                lines.append(f"  conflict: {conflict['entry']} / {conflict['code']}")
        for issue in local.get("diagnostics", []):
            lines.append(f"  {issue['stage']}: {issue['code']}")
        if local.get("root_extras") is not None:
            lines.append("  explicit root extras: " + (", ".join(local["root_extras"]) or "none"))
        correspondence = local.get("declaration_correspondence")
        if correspondence is not None:
            lines.append(f"  target-material metadata matches: {len(correspondence['matching_combinations'])}")
            codes = sorted({row["code"] for row in correspondence["diagnostics"]})
            if codes:
                lines.append("  target diagnostics: " + ", ".join(codes))
        if "qualification_gaps" in local:
            lines.extend(gap_lines(local["qualification_gaps"], "  target "))
        lines.append("  The local wheel is not verified as the official asset; no environment was prepared.")
    lines.append("Declarations only: installable=false. Referenced logs, payload/materials and full native acceptance "
                 "are not verified; published and cross-release activation remain gated.")
    return "\n".join(lines)


def _runtime_text(bindings):
    return "\n".join(f"  {name}: {row['path']} ({row['version']}, {row['implementation']}, {row['architecture']})"
                     for name, row in bindings.items())


def _published_material_text(materials):
    lines = []
    for row in materials["python_wheels"]:
        lines.append(f"  {row['name']} {row['version']}: {row['filename']} / "
                     f"asset {row['asset']['id']} / {row['asset']['size']} bytes / {row['asset']['url']}")
    for entry, row in materials.get("frontends", {}).items():
        artifact = row["artifact"]
        lines.append(f"  {entry}: {row['kind']} / {row['filename']} / asset {row['asset']['id']} / "
                     f"{row['asset']['size']} bytes / {row['asset']['url']}")
        lines.append(f"    artifact {artifact['kind']} {artifact['name']} {artifact['version']} / "
                     f"variant {artifact['variant']} / architecture {artifact['architecture']}")
        if "entry" in row:
            lines.append(f"    entry {row['entry']}")
    return "\n".join(lines)


def _confirm(message: str) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("installation requires explicit confirmation in a terminal")
    print(message)
    if input("Type UPDATE to continue: ").strip() != "UPDATE":
        raise ValueError("update cancelled; no installation changed")


def main(argv=None) -> int:
    options = _parser().parse_args(argv)
    v2_operation = (options.command in ("plan", "recover", "apply", "materials", "download", "bootstrap", "reconfigure")
                    or options.command in ("status", "check") and options.root is not None)
    try:
        from cpn.update_context import reject_candidate_runtime
        reject_candidate_runtime()
        if options.command == "bootstrap":
            from .install_contract import read_software_record
            from .published_install import bootstrap_published, inspect_bootstrap
            request = inspect_bootstrap(release=options.release, channel=options.channel,
                platform=read_software_record(options.platform),
                runtime_bindings=read_software_record(options.runtime_bindings),
                combination=options.combination, root_extras=options.extra,
                components=None if options.components is None else read_software_record(options.components),
                selected_frontend=options.select)
            selected = request.to_document()
            if options.inspect_only:
                print(json.dumps({"root": str(options.root.expanduser().absolute()), **selected},
                                 ensure_ascii=False, indent=2))
                return 0
            asset_text = _published_material_text(selected["materials"])
            target = selected["platform"]
            extras = ", ".join(selected["materials"]["root_extras"]) or "none"
            components = ", ".join(f"{entry}:{row['profile']}" for entry, row in selected["components"].items())
            runtimes = _runtime_text(selected["runtime_bindings"])
            _confirm(f"Prepare official RPNH {options.release}, complete frontends: {components}, Python optional dependencies: {extras}, "
                     f"in new root {options.root.expanduser().absolute()}.\n"
                     f"Target: {target['os']} {target['os_release']} / {target['architecture']}; "
                     f"Display choice: {selected['selected_frontend']}.\n"
                     f"Runtime bindings:\n{runtimes}\n"
                     f"Exact release materials:\n{asset_text}\n"
                     "These exact assets will be acquired and installed into a new isolated environment. "
                     "Current switches only after the original preparation and probes succeed. "
                     "Official acceptance declarations remain separate from this machine's validation; "
                     "business admission is checked when a task is opened. No task is started or resumed.")
            result = bootstrap_published(options.root, request).to_document()
            print(json.dumps(result, ensure_ascii=False) if options.json else
                  f"Official installation activated ({components}): {result['launcher']}; no task was resumed.")
            return 0
        if options.command == "reconfigure":
            from .install_contract import read_software_record
            from .published_install import inspect_reconfigure, reconfigure_published
            request = inspect_reconfigure(options.root, release=options.release, channel=options.channel,
                platform=read_software_record(options.platform),
                runtime_bindings=read_software_record(options.runtime_bindings),
                root_extras=[] if options.no_extras else options.extra, combination=options.combination,
                components=None if options.components is None else read_software_record(options.components),
                selected_frontend=options.select)
            document = request.to_document()
            if options.inspect_only:
                print(json.dumps(document, ensure_ascii=False, indent=2))
                return 0
            selected = document["selection"]
            old = ", ".join(document["current"]["materials"]["root_extras"]) or "none"
            new = ", ".join(selected["materials"]["root_extras"]) or "none"
            target = selected["platform"]
            old_components = ", ".join(f"{entry} ({row['profile']})"
                for entry, row in document["current"]["plan"]["components"].items())
            new_components = ", ".join(f"{entry} ({row['profile']})" for entry, row in selected["components"].items())
            old_runtimes = _runtime_text(document["current"]["plan"]["runtime_bindings"])
            runtimes = _runtime_text(selected["runtime_bindings"])
            old_materials = _published_material_text(document["current"]["materials"]["published_selection"])
            materials = _published_material_text(selected["materials"])
            _confirm(f"Reconfigure published {selected['release']} in {document['root']}.\n"
                     f"Current installation: {document['current']['target']}; optional dependencies: {old} -> {new}.\n"
                     f"Complete selection: {old_components} -> {new_components}.\n"
                     f"Display: {document['current']['frontend_view']['frontend']} -> {document['selected_frontend']}.\n"
                     f"Target: {target['os']} {target['os_release']} / {target['architecture']}.\n"
                     f"Current runtime bindings:\n{old_runtimes}\nTarget runtime bindings:\n{runtimes}\n"
                     f"Current exact release materials:\n{old_materials}\nTarget exact release materials:\n{materials}\n"
                     "Creates a new isolated installation with the same RPNH payload and keeps the previous installation. "
                     "Current switches after preparation, probes and payload comparison. No task is started or resumed.")
            result = reconfigure_published(options.root, request).to_document()
            result["root_extras"] = selected["materials"]["root_extras"]
            result["components"] = selected["components"]
            result["selected_frontend"] = document["selected_frontend"]
            print(json.dumps(result, ensure_ascii=False) if options.json else
                  f"Published selection reconfigured: {result['launcher']}; no task was resumed.")
            return 0
        if options.command == "download":
            from .published_download import download_published_wheel
            result = download_published_wheel(options.root,
                channel=options.channel or updates.read_state()["channel"], release=options.release,
                directory=options.directory)
            print(json.dumps(result, ensure_ascii=False, indent=2) if options.json else
                  f"Wheel acquired: {result['local_wheel']}\nSource observation: {result['receipt']}\n"
                  "No environment was prepared. This receipt grants no future apply or native qualification; installable=false.")
            return 0
        if options.command == "materials":
            from .install_materials import describe_basic_materials
            document = describe_basic_materials(options.candidate_wheel, options.wheelhouse,
                                                 python=options.python, root_extras=options.extra)
            content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
            if options.output is None:
                print(content, end="")
            else:
                with options.output.expanduser().open("x", encoding="utf-8") as output:
                    os.fchmod(output.fileno(), 0o600)
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                print(f"Exact local Basic materials written: {options.output}")
            return 0
        if options.command == "plan":
            result = _local_plan(options)
            if options.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                plan = result["plan"]
                print(f"Local RPNH candidate {plan['rpnh_version']} / {plan['installation_id']}")
                for entry, component in plan["components"].items():
                    print(f"  {entry}: {component['profile']} ({component['binding']['kind']})")
                if "root_extras" in result:
                    print("  RPNH optional dependencies: " + (", ".join(result["root_extras"]) or "none (Basic closure)"))
                for conflict in plan["conflicts"]:
                    print(f"  blocked: {conflict}")
                print("Plan only. Fixed dependencies, runtime health, data admission and native qualification are not certified; no installation was created.")
            return 1 if result["plan"]["conflicts"] else 0
        if options.command == "recover":
            from .install_v2 import recover
            result = recover(options.root).to_document()
            print(json.dumps(result, ensure_ascii=False) if options.json else
                  f"V2 transaction reconciled: current={result['current']}, previous={result['previous']}; no task started.")
            return 0
        if options.command == "apply":
            from .install_contract import read_software_record
            from .install_executor import execute_install, plan_local_install
            from .install_materials import selected_root_extras
            requested = None if options.components is None else read_software_record(options.components)
            # Bind the concrete material choice shown to the human. The file
            # may change while terminal confirmation is pending; the executor
            # compares this small document before any root/stage creation.
            material_document = read_software_record(options.materials, limit=1024 * 1024)
            extras = list(selected_root_extras(material_document))
            plan = plan_local_install(options.candidate_wheel, options.materials,
                                      root=options.root, requested=requested)
            if plan.conflicts:
                raise ValueError("the complete candidate plan contains unresolved conflicts")
            entries = ", ".join(f"{entry}:{item['profile']}" for entry, item in plan.components.items())
            _confirm(f"Prepare local RPNH candidate {plan.rpnh_version} ({entries}) in {options.root}. "
                      f"RPNH optional dependencies: {', '.join(extras) or 'none (Basic closure)'}. "
                     "Uses only the selected offline materials, creates a new installation, and switches current after probes. "
                     "No task is started or resumed. Native release qualification remains separate.")
            result = execute_install(options.root, plan, options.materials,
                                      selected_frontend=options.select,
                                      expected_materials=material_document).to_document()
            result["root_extras"] = extras
            print(json.dumps(result, ensure_ascii=False) if options.json else
                  f"Local candidate activated: {result['launcher']}; no task was resumed.")
            return 0
        from cpn.update_context import managed_context_hint
        from .update_install import _v2_root
        context = managed_context_hint()
        selected_root = getattr(options, "root", None) or context
        has_marker = selected_root is not None and os.path.lexists(selected_root / "managed.json")
        if selected_root is not None and (has_marker or selected_root == context) and _v2_root(selected_root):
            v2_operation = True
            if options.command == "rollback":
                from .install_executor import _published_rollback_pair, rollback_local, rollback_published
                from .install_v2 import marker, read_target
                from .update_install import lifecycle_lease
                with lifecycle_lease(selected_root, exclusive=False):
                    current = read_target(selected_root)
                    source_kind = current[2]["source"]["kind"]
                    pair = None
                    if source_kind == "published_release":
                        if marker(selected_root)["pending_activation"] is not None:
                            raise ValueError("recover the pending activation before inspecting rollback")
                        pair = _published_rollback_pair(current, read_target(selected_root, "previous", allow_missing=True))
                if pair is not None:
                    old_extras = ", ".join(pair["current"]["materials"]["root_extras"]) or "none"
                    next_extras = ", ".join(pair["previous"]["materials"]["root_extras"]) or "none"
                    source = pair["current"]["record"]["source"]
                    old_components = ", ".join(f"{entry} ({row['profile']})"
                        for entry, row in pair["current"]["plan"]["components"].items())
                    next_components = ", ".join(f"{entry} ({row['profile']})"
                        for entry, row in pair["previous"]["plan"]["components"].items())
                    current_runtimes = _runtime_text(pair["current"]["record"]["runtime_bindings"])
                    destination_runtimes = _runtime_text(pair["previous"]["record"]["runtime_bindings"])
                    current_materials = _published_material_text(pair["current"]["materials"]["published_selection"])
                    destination_materials = _published_material_text(pair["previous"]["materials"]["published_selection"])
                    _confirm(f"Restore retained published {source['release']} in {selected_root}.\n"
                             f"Current: {pair['current']['target']} (optional dependencies: {old_extras}).\n"
                             f"Destination: {pair['previous']['target']} (optional dependencies: {next_extras}).\n"
                             f"Complete selection: {old_components} -> {next_components}.\n"
                             f"Destination display: {pair['previous']['frontend_view']['frontend']}.\n"
                             f"Current runtime bindings:\n{current_runtimes}\nDestination runtime bindings:\n{destination_runtimes}\n"
                             f"Current exact release materials:\n{current_materials}\nDestination exact release materials:\n{destination_materials}\n"
                             f"Same release and original RPNH asset: {source['artifact']['filename']}. "
                             "The destination is reprobed before switching; databases and external effects are not restored. "
                             "No task is started or resumed.")
                    result = rollback_published(selected_root, expected_pair=pair).to_document()
                else:
                    _confirm("Switch to the retained compatible local candidate. Stop/checkpoint managed sessions first. "
                             "The destination is probed; databases and external effects are not restored.")
                    result = rollback_local(selected_root).to_document()
                print(json.dumps(result, ensure_ascii=False) if options.json else
                      f"Retained installation activated: {result['launcher']}; no task was resumed.")
                return 0
            if options.command in ("status", "check"):
                value = _v2_status(options, selected_root)
                print(json.dumps(value) if options.json else _render_v2_status(value))
                return 0
            print("Use update check for read-only published-v2 declarations, plan/apply for fixed local candidates, reconfigure for an explicit same-release published selection, rollback for the retained compatible installation, or recover for a pointer transaction. Cross-release activation remains gated.", file=sys.stderr)
            return 2
        if options.command in ("status", "check"):
            if options.root is not None:
                raise ValueError("explicit update status/check roots must use managed-v2")
            if getattr(options, "channel", None) is not None:
                raise ValueError("per-check --channel is v2-only; use update channel for v1 preferences")
            if any(getattr(options, name, None) is not None for name in ("plan_release", "candidate_wheel", "materials")):
                raise ValueError("published local-material planning requires a managed-v2 root")
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
    except (OSError, RuntimeError, ValueError, TypeError, BadZipFile) as exc:
        if v2_operation:
            from .install_v2 import ActivationError
            value = {"schema_version": "rpnh/update_error/v2", "operation": options.command,
                     "error": str(exc), "activation_status": "not_requested"
                     if options.command in ("download", "status", "check") or
                     options.command in ("bootstrap", "reconfigure") and options.inspect_only else "inspect_current_before_retry"}
            if isinstance(exc, ActivationError):
                value.update(current=exc.current, committed=exc.committed,
                             activation_status="committed_recovery_required" if exc.committed is True else
                             "not_committed" if exc.committed is False else "indeterminate")
            print(json.dumps(value, ensure_ascii=False) if getattr(options, "json", False) else
                  f"RPNH v2 {options.command}: {exc}", file=sys.stdout if getattr(options, "json", False) else sys.stderr)
            return 2
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
