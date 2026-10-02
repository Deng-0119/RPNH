"""MASFrameworkAdapter boundary for the Office benchmark campaign.

No driver is embedded here. Passing None is a hard setup error, not a mock run.
A completed business score must come from the official evaluator afterwards.
"""
from __future__ import annotations
from pathlib import Path
from .backend import BackendService, FaultSpec
from .bank_transfer import save_initial, restore_connection, factory_from_snapshot
from .constants import OFFICE_EFFECTS
from .lifecycle import NativeTerminationUnconfirmed
from .workflow import ARGUMENT_SCHEMA_REVISION
from .crosswalk import crosswalk
from .driver_contract import DriverRequest, DriverResult, LiveLimits
from .evidence import read_events
from .jsonio import write_new, read
from .native_plugin import configuration
from .projection import ObservationCollector, save_normalized_actions
from .state_diff import diagnose_case
from .office_cases import plugin_roles_for_task
from .task_view import public_task


class RPNHHarnessAuditAdapter:
    framework_id = "rpnh_office_b1"

    def __init__(self, *, driver, root: Path, execution_profile: Path, limits: LiveLimits,
                 bank_factory, dispatch, fault: FaultSpec = FaultSpec()):
        if driver is None or not callable(getattr(driver, "execute", None)):
            raise RuntimeError("a native driver is required for task execution")
        self.driver, self.root = driver, Path(root)
        self.execution_profile, self.limits = Path(execution_profile), limits
        self.bank_factory, self.dispatch, self.fault = bank_factory, dispatch, fault

    async def run(self, ctx, initial_input):
        from multi_agent.frameworks.core.base import RunOutcome
        if not isinstance(initial_input, str): raise ValueError("Office campaign uses text-only initial input")
        if self.root.exists(): raise FileExistsError("a fresh adapter run directory is required")
        public = public_task(ctx.task, ctx.catalog)
        view = public.as_dict()
        if initial_input != view["goal"]:
            raise ValueError("unexpected augmented input; declare a separate protocol before adding instructions")
        if not self.execution_profile.is_file(): raise FileNotFoundError("exact execution profile missing")
        self.root.mkdir(parents=True)
        roles = {r["role"] for r in view["agents"]}
        collector = ObservationCollector(roles=roles, tools=set(OFFICE_EFFECTS))
        write_new(self.root / "public_input.json", view)
        write_new(self.root / "protocol.json", {
            "framework": self.framework_id, "task_id": view["task_id"],
            "hidden_rules_exposed": False, "tool_filtering": "none",
            "driver": type(self.driver).__module__ + "." + type(self.driver).__qualname__,
            "fault": self.fault.mode, "limits": self.limits.__dict__,
            "profile_path_recorded": False,
            "argument_schema_revision": ARGUMENT_SCHEMA_REVISION,
            "requiredness_policy": "baseline-unchanged-no-required-list",
            "workflow_origin": "public-role-fanout-join-v1",
            "role_inventory": [row["role"] for row in view["agents"]],
            "node_count": len(view["agents"]) + 1,
            "track": "original-task-adapter-corrected",
        })
        result = None
        error = None
        before = after = None
        shutdown_unconfirmed = False
        final_snapshot_permitted = False
        initial_path = self.root / "bank.initial.sqlite"
        try:
            counters = save_initial(ctx.bank, initial_path)
            transferred_factory = factory_from_snapshot(self.bank_factory, initial_path, counters)
            with BackendService(run_id=ctx.run_id, root=self.root / "backend", tools=OFFICE_EFFECTS,
                                bank_factory=transferred_factory, dispatch=self.dispatch, fault=self.fault,
                                max_calls=self.limits.max_tool_calls,
                                plugin_roles=plugin_roles_for_task(view)) as service:
                before = service.snapshot(self.root / "bank.before.sqlite")
                try:
                    request = DriverRequest(task=public, initial_input=initial_input,
                        run_dir=self.root / "rpnh-run", execution_profile=self.execution_profile,
                        plugin_configuration=configuration(service.endpoints, ctx.run_id), limits=self.limits)
                    result = await self.driver.execute(request, collector)
                    if not isinstance(result, DriverResult): raise TypeError("native driver returned the wrong contract")
                except NativeTerminationUnconfirmed:
                    shutdown_unconfirmed = True
                    raise
                finally:
                    monitor_path = self.root / "rpnh-monitor.json"
                    monitor = read(monitor_path) if monitor_path.is_file() else {}
                    lifecycle_confirmed = (
                        isinstance(result, DriverResult)
                        or monitor.get("final_snapshot_permitted") is True)
                    if not shutdown_unconfirmed and lifecycle_confirmed:
                        after = service.snapshot(self.root / "bank.after.sqlite")
                        # The upstream runner must judge the ACTUAL post-run state.
                        restore_connection(self.root / "bank.after.sqlite", ctx.bank.conn)
                        after_counters = service.counters()
                        for name, value in after_counters.items(): setattr(ctx.bank, name, value)
                        write_new(self.root / "bank.runtime.after.json", after_counters)
                        final_snapshot_permitted = True
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)
        finally:
            collector.save(self.root / "observations.json")
            collector.export_to(ctx.action_sink)
            save_normalized_actions(self.root / "actions.normalized.json", ctx.action_sink)
            if before is not None and after is not None:
                write_new(self.root / "state_diagnostic.json", diagnose_case(before, after, task_id=view["task_id"]))
            witness_file = self.root / "backend" / "witness.jsonl"
            links = crosswalk(read_events(witness_file) if witness_file.exists() else [],
                              result.registry_records if isinstance(result, DriverResult) else None)
            write_new(self.root / "crosswalk.json", links)
            if isinstance(result, DriverResult):
                write_new(self.root / "driver_result.json", result.__dict__)
            completeness = (isinstance(result, DriverResult) and result.context_capture_complete
                            and bool(result.roles_observed) and set(result.roles_observed) <= roles
                            and len(collector) > 0 and links["complete"])
            # A failed task can still be fully observed and eligible for scoring.
            write_new(self.root / "run_status.json", {
                "error": error, "native_terminal_present": bool(result and result.terminal_evidence_ref),
                "business_task_success": None,
                "execution_mode": result.execution_mode if result else None,
                "trace_complete": bool(completeness and final_snapshot_permitted), "official_score": None,
                "shutdown_unconfirmed": shutdown_unconfirmed,
                "final_snapshot_permitted": final_snapshot_permitted,
                "actual_model_calls": result.actual_model_calls if result else None,
                "fault_extension": self.fault.mode != "none",
                "baseline_comparability": "office-b1-frozen-upper-protocol",
                "model_budget_exceeded": (
                    None if self.limits.max_model_calls is None else
                    bool(result and result.actual_model_calls
                         > self.limits.max_model_calls)),
            })
        return RunOutcome(framework=self.framework_id,
                          final_output=result.final_output if result else "", error=error)
