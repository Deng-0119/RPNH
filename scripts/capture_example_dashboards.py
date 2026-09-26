"""Capture public dashboard images from deterministic and accepted example runs."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib
import os
import re
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
from typing import Any, Iterator, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples._support.profile import write_scripted_profile
from examples.hybrid_summary.run import run_example as run_hybrid_example
from examples.workflow_patterns.run import run_example
from cpn.plugins.catalog import BoundPlugin, PluginCatalog
from cpn.plugins.runtime import run_plugin
from cpn.rpnh.agent_tasks import AgentStage
from cpn.rpnh.main_session import MainSession
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.run_authority import current_run_execution_authority
from cpn.rpnh.task_control import TaskControl


DEFAULT_OUTPUT_ROOT = ROOT


def _row_ref(row: Mapping[str, Any], object_type: str) -> VersionRef:
    return VersionRef(
        object_type,
        TypedId.parse(str(row["logical_id"])),
        TypedId.parse(str(row["version_id"])),
    )


def require_terminal_registry(run_dir: Path) -> None:
    """Require exact terminal authority and its sole registered final result."""
    core = _RegistryCore(run_dir, create=False, read_only=True)
    kernel = _ResourceServiceKernel(core)
    _authority_ref, authority = current_run_execution_authority(core, kernel)
    evidence_rows = core.event_store.canonical_object_rows(
        object_type="run_terminal_evidence/v1")
    final_rows = core.event_store.canonical_object_rows(
        object_type="final_result_index/v1")
    if (authority.get("status") != "terminal"
            or len(evidence_rows) != 1 or len(final_rows) != 1):
        raise RuntimeError(
            "public example image requires terminal Registry authority, one "
            "terminal evidence object and one final result index")

    evidence_ref = _row_ref(evidence_rows[0], "run_terminal_evidence/v1")
    final_ref = _row_ref(final_rows[0], "final_result_index/v1")
    evidence = dict(kernel._exact_object(
        evidence_ref, expected_type="run_terminal_evidence/v1").metadata)
    final = dict(kernel._exact_object(
        final_ref, expected_type="final_result_index/v1").metadata)
    evidence_payload = {
        "entity_type": evidence_ref.entity_type,
        "logical_id": str(evidence_ref.entity_id),
        "version_id": str(evidence_ref.version_id),
    }
    final_payload = {
        "entity_type": final_ref.entity_type,
        "logical_id": str(final_ref.entity_id),
        "version_id": str(final_ref.version_id),
    }
    if (authority.get("terminal_evidence_ref") != evidence_payload
            or evidence.get("terminal_evidence_ref") != evidence_payload
            or evidence.get("final_result_index_ref") != final_payload
            or final.get("final_result_index_ref") != final_payload
            or evidence.get("terminal_result_ref")
            != final.get("terminal_result_ref")
            or evidence.get("terminal_occurrence_ref")
            != final.get("terminal_occurrence_ref")
            or evidence.get("run_outcome")
            != final.get("terminal_outcome")):
        raise RuntimeError(
            "public example image Registry terminal/final-result linkage is invalid")


@contextmanager
def viewer(run_dir: Path) -> Iterator[str]:
    process = subprocess.Popen(
        [
            sys.executable, "-u", "-m", "cpn.rpnh_cli", "net",
            "--run", str(run_dir), "--view", "--no-open",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=20):
                raise RuntimeError("dashboard did not publish a loopback URL")
        first_line = process.stdout.readline()
        matched = re.search(r"http://127\.0\.0\.1:\d+/", first_line)
        if matched is None:
            detail = process.stderr.read()
            raise RuntimeError(
                "dashboard did not publish a loopback URL: "
                f"{first_line.strip()} {detail.strip()}")
        yield matched.group()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def capture(*, page, run_dir: Path, mode: str, destination: Path,
            expected_nodes: set[str]) -> None:
    with viewer(run_dir) as url:
        page.goto(url, wait_until="networkidle")
        page.wait_for_selector('#paper[data-ready="true"]', timeout=45_000)
        page.locator("#auto-refresh").uncheck()
        page.locator("#language").select_option("en")
        unsettled = page.locator("#metric-active").inner_text().strip()
        if unsettled != "0":
            raise RuntimeError(
                f"public example image requires zero unsettled records, got {unsettled}")
        page.locator(f"#mode-{mode}").click()
        page.locator("#fit").click()
        page.wait_for_timeout(500)
        visible = set(page.locator("#paper .node").evaluate_all(
            "nodes => nodes.map(node => node.dataset.id)"))
        missing = expected_nodes - visible
        if missing:
            raise RuntimeError(
                f"dashboard did not render expected nodes: {sorted(missing)}")
        # Public images keep the real graph, controls and aggregate state while
        # omitting run-specific checkpoint identities and local observation
        # timestamps. Raw Registry evidence remains private operational data.
        page.locator("#time-position").evaluate(
            "node => { node.textContent = 'Saved checkpoint'; }")
        page.locator("footer").evaluate(
            "node => { node.style.visibility = 'hidden'; }")
        destination.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(destination), full_page=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    for name in (
            "native", "hybrid", "serial", "parallel", "document",
            "long-process", "task"):
        parser.add_argument(f"--{name}-run", type=Path)
    parser.add_argument("--net-operations-run", type=Path)
    for host in ("basic", "codex", "dsh", "opencode"):
        parser.add_argument(f"--adapter-{host}-run", type=Path)
    args = parser.parse_args(argv)
    output_root = args.output_root.resolve()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Install the optional capture dependency with `python -m pip "
            "install playwright`, then install Chromium with `python -m "
            "playwright install chromium`.") from exc

    with tempfile.TemporaryDirectory(prefix="rpnh-dashboard-capture-") as raw:
        temporary = Path(raw)
        execution = write_scripted_profile(temporary / "profile")
        runs: dict[str, Path] = {
            name: path.resolve()
            for name, path in {
                "native": args.native_run,
                "hybrid": args.hybrid_run,
                "serial": args.serial_run,
                "parallel": args.parallel_run,
                "document": args.document_run,
                "long_process": args.long_process_run,
                "task": args.task_run,
            }.items()
            if path is not None
        }

        if "native" not in runs:
            native_path = str(ROOT / "examples" / "native_plugin")
            if native_path not in sys.path:
                sys.path.insert(0, native_path)
            sys.modules.pop("rpnh_demo", None)
            native = importlib.import_module("rpnh_demo")
            native_run = temporary / "native-plugin"
            native_result = run_plugin(
                PluginCatalog((BoundPlugin(native.plugin(), {}),)),
                "demo/add", {"left": 2, "right": 3}, run_dir=native_run,
            )
            if (native_result["stop_reason"] != "terminal"
                    or native_result["terminal_evidence_ref"] is None):
                raise RuntimeError("native example did not reach terminal")
            runs["native"] = native_run

        if "hybrid" not in runs:
            hybrid_run = temporary / "hybrid-summary"
            hybrid_result = run_hybrid_example(
                run_dir=hybrid_run,
                input_path=ROOT / "examples" / "hybrid_summary" / "input.txt",
                graph_path=ROOT / "examples" / "hybrid_summary" / "graph.json",
                plugin_config_path=(
                    ROOT / "examples" / "native_plugin" / "plugins.json"),
                execution_config_path=execution,
            )
            if hybrid_result["terminal_evidence_ref"] is None:
                raise RuntimeError("hybrid example did not reach terminal")
            runs["hybrid"] = hybrid_run

        for scenario in ("serial", "parallel", "document", "long_process"):
            if scenario in runs:
                continue
            run_dir = temporary / scenario
            result = run_example(
                scenario_name=scenario,
                run_dir=run_dir,
                execution_config_path=execution,
            )
            if result["status"] != "PASS":
                raise RuntimeError(f"{scenario} example did not reach terminal")
            runs[scenario] = run_dir

        if "task" not in runs:
            plugin_setting = os.environ.pop("RPNH_PLUGIN_CONFIG", None)
            try:
                control = TaskControl(temporary / "task-control")
                session = MainSession(
                    temporary / "task-session", execution,
                    task_control=control)
                first = session.launch(
                    "List exactly three data quality checks for a small batch table.",
                    (AgentStage(
                        "worker",
                        "Complete the requested task and return its result."),),
                )
                second = session.launch(
                    "Task values: 4, 8, 12. Summarize the count, total and mean.",
                    (AgentStage(
                        "worker",
                        "Complete the requested task and return its result."),),
                )
                if first.process.wait(timeout=30) != 0:
                    raise RuntimeError("first independent task failed")
                if second.process.wait(timeout=30) != 0:
                    raise RuntimeError("second independent task failed")
                if control.status(second.task_id)["registry"][
                        "execution_status"] != "terminal":
                    raise RuntimeError("independent task did not reach terminal")
                runs["task"] = second.run_dir
            finally:
                if plugin_setting is not None:
                    os.environ["RPNH_PLUGIN_CONFIG"] = plugin_setting

        optional_runs = {
            "net_operations": args.net_operations_run,
            **{
                f"adapter_{host}": getattr(args, f"adapter_{host}_run")
                for host in ("basic", "codex", "dsh", "opencode")
            },
        }
        for name, path in optional_runs.items():
            if path is not None:
                runs[name] = path.resolve()

        for run_dir in dict.fromkeys(runs.values()):
            require_terminal_registry(run_dir)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": 1440, "height": 980},
                locale="en-US",
                color_scheme="light",
            )
            capture(
                page=page,
                run_dir=runs["native"],
                mode="petri",
                destination=(output_root / "examples" / "native_plugin" /
                             "assets" / "native-plugin-petrinet.png"),
                expected_nodes={"plugin.run"},
            )
            capture(
                page=page,
                run_dir=runs["hybrid"],
                mode="flow",
                destination=(output_root / "examples" / "hybrid_summary" /
                             "assets" / "hybrid-summary-flow.png"),
                expected_nodes={
                    "team.normalize", "team.summarize", "team.explain"},
            )
            capture(
                page=page,
                run_dir=runs["serial"],
                mode="petri",
                destination=(output_root / "examples" / "workflow_patterns" /
                             "assets" / "serial-petrinet.png"),
                expected_nodes={
                    "team.intake", "team.work", "team.deliver"},
            )
            capture(
                page=page,
                run_dir=runs["parallel"],
                mode="overview",
                destination=(output_root / "examples" / "workflow_patterns" /
                             "assets" / "parallel-overview.png"),
                expected_nodes={
                    "team.prepare", "team.facts", "team.risks", "team.join"},
            )
            capture(
                page=page,
                run_dir=runs["parallel"],
                mode="petri",
                destination=(output_root / "examples" / "workflow_patterns" /
                             "assets" / "parallel-petrinet.png"),
                expected_nodes={
                    "team.prepare", "team.facts", "team.risks", "team.join"},
            )
            capture(
                page=page,
                run_dir=runs["document"],
                mode="flow",
                destination=(output_root / "examples" / "workflow_patterns" /
                             "assets" / "document-flow.png"),
                expected_nodes={
                    "team.outline", "team.draft", "team.review",
                    "team.publish"},
            )
            capture(
                page=page,
                run_dir=runs["long_process"],
                mode="overview",
                destination=(output_root / "examples" / "workflow_patterns" /
                             "assets" / "long-process-overview.png"),
                expected_nodes={
                    "team.intake", "team.research", "team.analyze",
                    "team.draft", "team.review", "team.publish"},
            )
            capture(
                page=page,
                run_dir=runs["task"],
                mode="petri",
                destination=(output_root / "examples" / "task_workspace" /
                             "assets" / "independent-task-petrinet.png"),
                expected_nodes={"worker.run"},
            )

            optional_captures = {
                "net_operations": (
                    "examples/net_operations/assets/"
                    "live-replacement-petrinet.png",
                    "petri", {"replacement_worker.run"}),
                "adapter_basic": (
                    "cpn/examples/adapter_task/assets/basic-petrinet.png",
                    "petri", {"main.run"}),
                "adapter_codex": (
                    "cpn/examples/adapter_task/assets/codex-petrinet.png",
                    "petri", {"main.run"}),
                "adapter_dsh": (
                    "cpn/examples/adapter_task/assets/dsh-petrinet.png",
                    "petri", {"dsh.model", "dsh.finalize"}),
                "adapter_opencode": (
                    "cpn/examples/adapter_task/assets/opencode-petrinet.png",
                    "petri", {"main.run"}),
            }
            for name, (relative, mode, expected) in optional_captures.items():
                if name in runs:
                    capture(
                        page=page,
                        run_dir=runs[name],
                        mode=mode,
                        destination=output_root / relative,
                        expected_nodes=expected,
                    )
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
