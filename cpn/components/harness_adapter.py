"""Module-runtime adapter for the generic Registry/Petri harness boundary."""
from __future__ import annotations

from cpn.rpnh.harness import OperationDispatch, OperationDisposition, OperationProducts
from .registered_operation_dispatcher import (
    RegisteredOperationExecutionBlock, RegisteredOperationProducts,
    RegisteredOperationResourceWait, RegisteredOperationTerminalHandoff)


def adapt_registered_dispatcher(execution, dispatcher) -> OperationDispatch:
    """Hide module result types before returning to the mechanical harness."""
    def adapt(result):
        if isinstance(result, RegisteredOperationProducts):
            return OperationProducts(result.authority, result.timing_observation)
        if isinstance(result, RegisteredOperationResourceWait):
            if result.execution is not execution:
                raise TypeError(
                    "resource wait differs from adapted operation execution")
            return OperationDisposition(
                execution,
                "resource_wait",
                payload=result,
                resume=lambda grant: adapt(
                    dispatcher.resume_resource_grant(grant)),
            )
        for kind, label in (
            (RegisteredOperationExecutionBlock, "execution_block"),
            (RegisteredOperationTerminalHandoff, "terminal_handoff"),
        ):
            if isinstance(result, kind):
                return OperationDisposition(execution, label, payload=result)
        raise TypeError("module dispatcher returned no harness operation result")

    def invoke():
        return adapt(dispatcher.permit.dispatch())

    return OperationDispatch(execution, invoke)


__all__ = ("adapt_registered_dispatcher",)
