"""Closed recovery policies for the external-provider adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


RECOVERY_STRATEGY = "bounded_same_route_health_probe/v1"
NO_AUTOMATIC_RETRY_STRATEGY = "no_automatic_retry/v1"
_RECOVERY_FIELDS = {
    "strategy", "max_probe_attempts", "probe_timeout_budget_seconds",
    "max_probe_success_formal_failure_cycles",
}


class ExternalProviderRecoveryConfigError(ValueError):
    """The external-provider recovery policy is not the current shape."""


def _positive(
        value: object, *, label: str, maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ExternalProviderRecoveryConfigError(
            f"external provider recovery {label} must be a positive integer")
    if maximum is not None and value > maximum:
        raise ExternalProviderRecoveryConfigError(
            f"external provider recovery {label} must be at most {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class ExternalProviderRecoveryPolicy:
    strategy: str
    max_probe_attempts: int
    probe_timeout_budget_seconds: int
    max_probe_success_formal_failure_cycles: int

    def as_document(self) -> dict[str, object]:
        if self.strategy == NO_AUTOMATIC_RETRY_STRATEGY:
            return {"strategy": self.strategy}
        return {
            "strategy": self.strategy,
            "max_probe_attempts": self.max_probe_attempts,
            "probe_timeout_budget_seconds": self.probe_timeout_budget_seconds,
            "max_probe_success_formal_failure_cycles": (
                self.max_probe_success_formal_failure_cycles),
        }


def recovery_policy_from_document(
        value: object,
) -> ExternalProviderRecoveryPolicy:
    if (isinstance(value, Mapping)
            and value.get("strategy") == NO_AUTOMATIC_RETRY_STRATEGY):
        if set(value) != {"strategy"}:
            raise ExternalProviderRecoveryConfigError(
                "no-automatic-retry recovery accepts only strategy")
        return ExternalProviderRecoveryPolicy(
            strategy=NO_AUTOMATIC_RETRY_STRATEGY,
            max_probe_attempts=0,
            probe_timeout_budget_seconds=0,
            max_probe_success_formal_failure_cycles=0,
        )
    if not isinstance(value, Mapping) or set(value) != _RECOVERY_FIELDS:
        raise ExternalProviderRecoveryConfigError(
            "external provider recovery fields are not current")
    strategy = value.get("strategy")
    if strategy != RECOVERY_STRATEGY:
        raise ExternalProviderRecoveryConfigError(
            "external provider recovery strategy is not supported")
    return ExternalProviderRecoveryPolicy(
        strategy=strategy,
        max_probe_attempts=_positive(
            value.get("max_probe_attempts"), label="max_probe_attempts",
            maximum=3),
        probe_timeout_budget_seconds=_positive(
            value.get("probe_timeout_budget_seconds"),
            label="probe_timeout_budget_seconds"),
        max_probe_success_formal_failure_cycles=_positive(
            value.get("max_probe_success_formal_failure_cycles"),
            label="max_probe_success_formal_failure_cycles", maximum=3),
    )


__all__ = [
    "ExternalProviderRecoveryConfigError",
    "ExternalProviderRecoveryPolicy",
    "RECOVERY_STRATEGY",
    "NO_AUTOMATIC_RETRY_STRATEGY",
    "recovery_policy_from_document",
]
