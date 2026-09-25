"""Optional default execution component; not imported by RPNH registration.

The execution owner explicitly grants this HOST implementation the firing-local
dispatcher context. External executors use the same keyword contract and return
typed operation products through the Registry gateway, not Success or terminal
commands. This function never imports Python implementation locators from JSON.
"""

from __future__ import annotations

from .default_agent_executor import DefaultAgentExecutor


def execute_default_operation(*, execution, gateway, resources, host_context):
    """Execute the configured current component against its exact gateway."""
    component = DefaultAgentExecutor(
        execution=execution, gateway=gateway,
        resources=resources, host_context=host_context)
    return component.execute(execution)


__all__ = ("execute_default_operation",)
