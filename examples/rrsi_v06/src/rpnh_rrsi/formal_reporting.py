"""Atomic application progress exports, without Registry or adapter authority."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from cpn.rpnh.llm_contracts import LLMInputPortFailure, LLMInputPortInterrupted

from .formal_execution import FormalRunStopped
from .formal_observation import public_failure_code, usage_summary


EXECUTION_CONTROL_VERSION = "rrsi_v06/execution_control/v2"


def failure_summary(exc: BaseException, *, stage: str) -> dict:
    """Export type and bounded control facts, never exception messages/args."""
    interrupted = isinstance(exc, (LLMInputPortInterrupted, KeyboardInterrupt))
    if isinstance(exc, FormalRunStopped):
        interrupted = exc.stop_reason == "stopped_by_owner"
    result = {"status": "interrupted" if interrupted else "failed",
              "stage": stage, "error_type": type(exc).__name__}
    if isinstance(exc, (LLMInputPortInterrupted, LLMInputPortFailure)):
        result["submission_state"] = exc.submission_state
    if isinstance(exc, LLMInputPortFailure):
        result["disposition"] = exc.disposition
        result["failure_code"] = public_failure_code(exc.failure_code)
    if isinstance(exc, FormalRunStopped):
        result["harness_stop_reason"] = exc.stop_reason
    return result


def add_secondary_failure(primary: BaseException, secondary: BaseException,
                          *, stage: str) -> None:
    # The caller still receives the identical original exception object.
    primary.add_note(f"RRSI secondary {stage} failure: {type(secondary).__name__}")


def atomic_report(path: Path, report: dict) -> None:
    """Replace the previous complete JSON snapshot only after a flushed write."""
    temporary = None
    primary_error = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=".formal-report-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except BaseException as secondary:
                if primary_error is None:
                    raise
                add_secondary_failure(primary_error, secondary, stage="report_cleanup")


class CampaignProgress:
    def __init__(self, destination: Path, report: dict) -> None:
        self.destination = destination
        self.report = report
        self.stage = "campaign_start"
        self.failure = None

    def checkpoint(self) -> None:
        self.report["execution_control_version"] = EXECUTION_CONTROL_VERSION
        attempts = self.report["attempt_inventory"]
        self.report["usage"] = {
            "roles": usage_summary([row for row in attempts if row["owner"] != "Policy"]),
            "policy": usage_summary([row for row in attempts if row["owner"] == "Policy"]),
        }
        try:
            atomic_report(self.destination / "formal-report.json", self.report)
        except BaseException as exc:
            if self.failure is None:
                self.failure = failure_summary(exc, stage="report_write")
            raise

    def failed(self, exc: BaseException) -> None:
        if self.failure is None:
            self.failure = failure_summary(exc, stage=self.stage)
        self.report.update({
            "report_status": "incomplete", "termination": self.failure,
            "campaign_complete": False, "formal_rrsi_v06_local_complete": False,
        })
        try:
            self.checkpoint()
        except BaseException as secondary:
            add_secondary_failure(exc, secondary, stage="report_write")
