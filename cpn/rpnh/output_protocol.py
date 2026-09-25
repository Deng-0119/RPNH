"""Host-registered output bundles; semantic callbacks do not own settlement.

Only a registered key is serialized in an operation declaration. Host Python
supplies these immutable contracts and optional semantic callbacks explicitly.
This module deliberately has no Registry, component, or workflow imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .terminal import TerminalProductContract


class OutputProtocolError(ValueError):
    """Products do not satisfy their registered mechanical output contract."""


@dataclass(frozen=True, slots=True)
class OutputProductPort:
    port_id: str
    content_schema_id: str
    minimum: int = 1
    maximum: int = 1

    def __post_init__(self) -> None:
        if (not self.port_id or not self.content_schema_id
                or isinstance(self.minimum, bool)
                or isinstance(self.maximum, bool)
                or not isinstance(self.minimum, int)
                or not isinstance(self.maximum, int)
                or not 0 <= self.minimum <= self.maximum):
            raise OutputProtocolError("invalid registered product port")


@dataclass(frozen=True, slots=True)
class OutputOutcome:
    outcome_id: str
    outcome_port_id: str
    products: tuple[OutputProductPort, ...]

    def __post_init__(self) -> None:
        ids = tuple(port.port_id for port in self.products)
        if (not self.outcome_id or not self.outcome_port_id
                or len(ids) != len(set(ids))
                or self.outcome_port_id not in ids):
            raise OutputProtocolError("invalid registered outcome bundle")


@dataclass(frozen=True, slots=True)
class OutputInterpretation:
    """Semantic witnesses only; exact membership/effects remain core checks."""

    outcome: str
    target_node_refs: tuple[Any, ...] = ()
    final_result_refs: tuple[Any, ...] = ()
    current_result_refs: tuple[Any, ...] = ()
    prior_result_refs: tuple[Any, ...] = ()
    upstream_outcome: str | None = None
    upstream_disposition_refs: tuple[Any, ...] = ()
    no_deliverable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RegisteredOutputProtocol:
    key: str
    outcomes: tuple[OutputOutcome, ...]
    interpreter: Callable[..., OutputInterpretation] | None = None
    guidance: Callable[[Mapping[str, Any]], str] | None = None
    output_kind: str = "ordinary_document"
    role_label: str = "LLM actor"
    terminal_contract: TerminalProductContract | None = None
    terminal_interpreter: Callable[..., OutputInterpretation] | None = None

    def __post_init__(self) -> None:
        ids = tuple(item.outcome_id for item in self.outcomes)
        ports = tuple(item.outcome_port_id for item in self.outcomes)
        if (not self.key or not ids or len(ids) != len(set(ids))
                or len(ports) != len(set(ports))
                or self.interpreter is not None and not callable(self.interpreter)
                or self.guidance is not None and not callable(self.guidance)):
            raise OutputProtocolError("invalid registered output protocol")
        if self.terminal_contract is not None:
            if not isinstance(self.terminal_contract, TerminalProductContract):
                raise OutputProtocolError("terminal requires a typed registered contract")
            for outcome, port in self.terminal_contract.outcome_ports.items():
                declared = next((item for item in self.outcomes
                                 if item.outcome_id == outcome), None)
                if (declared is None or declared.outcome_port_id != port
                        or not any(product.port_id == port
                                   and product.minimum == product.maximum == 1
                                   and product.content_schema_id
                                   == self.terminal_contract.content_schema_id
                                   for product in declared.products)):
                    raise OutputProtocolError("terminal differs from registered product bundle")
        if (self.terminal_interpreter is not None
                and (not callable(self.terminal_interpreter)
                     or self.terminal_contract is None)):
            raise OutputProtocolError("terminal interpreter lacks registered product contract")

    def selected_outcome(self, port_id: str, declared_outcome: object) -> str | None:
        matches = tuple(item for item in self.outcomes
                        if item.outcome_port_id == port_id)
        if not matches:
            product_outcomes = {
                item.outcome_id for item in self.outcomes
                if any(product.port_id == port_id for product in item.products)
            }
            if (declared_outcome is not None
                    and declared_outcome not in product_outcomes):
                raise OutputProtocolError(
                    "product colour is outside its registered outcomes")
            return None
        outcome = matches[0].outcome_id
        if declared_outcome != outcome:
            raise OutputProtocolError("selected binding has no exact registered outcome")
        return outcome

    def validate_bundle(
            self, counts: Mapping[str, int], schemas: Mapping[str, str],
            declared_outcomes: Mapping[str, object]) -> str:
        """Validate the entire bundle, including absent and extra products."""
        if set(counts) != set(schemas) or set(counts) != set(declared_outcomes):
            raise OutputProtocolError("output bundle lacks exact port membership")
        selected = tuple(item for item in self.outcomes
                         if counts.get(item.outcome_port_id, 0))
        if len(selected) != 1:
            raise OutputProtocolError("completion must select one registered outcome")
        outcome = selected[0]
        products = {item.port_id: item for item in outcome.products}
        for port_id, count in counts.items():
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise OutputProtocolError("invalid output quantity")
            self.selected_outcome(port_id, declared_outcomes[port_id])
            product = products.get(port_id)
            if product is None:
                if count:
                    raise OutputProtocolError("product outside selected outcome bundle")
            elif (schemas[port_id] != product.content_schema_id
                  or not product.minimum <= count <= product.maximum):
                raise OutputProtocolError("registered product schema/cardinality mismatch")
        if any(item.minimum and item.port_id not in counts
               for item in outcome.products):
            raise OutputProtocolError("registered bundle lacks a required product port")
        return outcome.outcome_id

    def interpret(self, payload: bytes, *, selected_outcome: str, **context: Any
                  ) -> OutputInterpretation:
        if selected_outcome not in {item.outcome_id for item in self.outcomes}:
            raise OutputProtocolError("semantic input has no registered outcome")
        result = (OutputInterpretation(selected_outcome)
                  if self.interpreter is None else self.interpreter(
                      payload, selected_outcome=selected_outcome, **context))
        if not isinstance(result, OutputInterpretation) or result.outcome != selected_outcome:
            raise OutputProtocolError("semantic interpreter changed selected binding outcome")
        return result

    def interpret_terminal(self, payload: bytes, *, selected_outcome: str, **context: Any):
        if (self.terminal_contract is None
                or selected_outcome not in self.terminal_contract.outcome_ports):
            raise OutputProtocolError("selected outcome has no registered terminal binding")
        result = (self.interpret(payload, selected_outcome=selected_outcome, **context)
                  if self.terminal_interpreter is None else self.terminal_interpreter(
                      payload, selected_outcome=selected_outcome, **context))
        if not isinstance(result, OutputInterpretation) or result.outcome != selected_outcome:
            raise OutputProtocolError("terminal interpretation changed exact selected outcome")
        return result
