"""Deterministic bounded BFS over exact start/settle states."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import monotonic

from .contracts import (ActionBinding, AnalysisInput, AnalysisState,
                        BindingEnumeration, Successor, SupportResult,
                        canonical_json, state_key)


@dataclass(frozen=True, slots=True)
class GraphNode:
    state: AnalysisState
    depth: int
    parent_edge: int | None = None


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: int
    target: int | None
    successor: Successor


@dataclass(frozen=True, slots=True)
class Frontier:
    node: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExplorationGraph:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    expanded: tuple[int, ...]
    frontier: tuple[Frontier, ...]
    support: SupportResult
    initial_bindings: BindingEnumeration | None
    elapsed_seconds: float

    @property
    def complete(self) -> bool:
        return (self.support.supported and not self.frontier
                and set(self.expanded) == set(range(len(self.nodes))))

    def path_to(self, node: int) -> tuple[ActionBinding, ...]:
        path = []
        while self.nodes[node].parent_edge is not None:
            edge = self.edges[self.nodes[node].parent_edge]
            path.append(edge.successor.action.binding)
            node = edge.source
        return tuple(reversed(path))


def explore(analysis_input: AnalysisInput) -> ExplorationGraph:
    from . import semantics
    from .properties import classify_terminal

    started = monotonic()
    policy = analysis_input.policy
    support = semantics.check_supported_semantics(analysis_input)
    nodes = [GraphNode(analysis_input.initial_state, 0)]
    edges: list[GraphEdge] = []
    expanded: list[int] = []
    pending: dict[int, set[str]] = {}
    initial_bindings = None

    def cutoff(node, *reasons):
        pending.setdefault(node, set()).update(reasons)

    def result():
        return ExplorationGraph(tuple(nodes), tuple(edges), tuple(expanded),
                                tuple(Frontier(i, tuple(sorted(reasons)))
                                      for i, reasons in sorted(pending.items())),
                                support, initial_bindings, monotonic() - started)

    if not support.supported:
        cutoff(0, *support.reasons or ("unsupported semantics",))
        return result()
    if (policy.max_states < 1 or policy.max_edges < 0 or policy.max_depth < 0
            or policy.max_seconds <= 0 or policy.max_bindings < 1):
        cutoff(0, "invalid exploration budget")
        return result()
    seen = {state_key(nodes[0].state): 0}
    queue = deque([0])
    while queue:
        index = queue.popleft()
        node = nodes[index]
        if monotonic() - started >= policy.max_seconds:
            cutoff(index, "max_seconds")
            for waiting in queue:
                cutoff(waiting, "max_seconds")
            break
        terminal = classify_terminal(analysis_input, node.state)
        if (analysis_input.terminal_contract.stop_on_terminal
                and terminal.allowed):
            # Terminal stop closes the graph, but current enabledness is a
            # separate point query. Retain its bounded evidence when requested.
            if index == 0 and "enabledness" in (*policy.properties, *policy.required_properties):
                initial_bindings = semantics.enabled_bindings(analysis_input, node.state,
                    deadline=started + policy.max_seconds)
            expanded.append(index)
            continue
        enumeration = semantics.enabled_bindings(analysis_input, node.state,
                                                 deadline=started + policy.max_seconds)
        if index == 0:
            initial_bindings = enumeration
        if not enumeration.complete:
            cutoff(index, *enumeration.reasons or ("binding enumeration incomplete",))
        bindings = sorted(enumeration.bindings, key=canonical_json)
        fully_expanded = enumeration.complete
        for binding in bindings:
            if monotonic() - started >= policy.max_seconds:
                cutoff(index, "max_seconds")
                fully_expanded = False
                break
            if node.depth >= policy.max_depth:
                cutoff(index, "max_depth")
                fully_expanded = False
                break
            if len(edges) >= policy.max_edges:
                cutoff(index, "max_edges")
                fully_expanded = False
                break
            successors = semantics.successors(analysis_input, node.state, binding)
            # A case must identify one reproducible successor. Unmodeled choice
            # cannot be silently picked for a binding-only witness.
            if len(successors) != 1:
                cutoff(index, "binding has no unique modeled successor")
                fully_expanded = False
                continue
            successor = successors[0]
            if successor.unknown_reasons:
                cutoff(index, *successor.unknown_reasons)
                fully_expanded = False
            target = None
            if successor.state is not None:
                key = state_key(successor.state)
                target = seen.get(key)
                if target is None:
                    if len(nodes) >= policy.max_states:
                        cutoff(index, "max_states")
                        fully_expanded = False
                    else:
                        target = len(nodes)
                        seen[key] = target
                        nodes.append(GraphNode(successor.state, node.depth + 1,
                                               len(edges)))
                        queue.append(target)
            else:
                # The invalid outcome is replayable safety evidence, but has
                # no state on which completion/liveness can be evaluated.
                cutoff(index, "unsafe successor unresolved" if successor.safety_violations
                       else "successor unavailable")
                fully_expanded = False
            edges.append(GraphEdge(index, target, successor))
        if fully_expanded:
            expanded.append(index)
    return result()
