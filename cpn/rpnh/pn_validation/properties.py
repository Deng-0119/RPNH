"""Separate quantified conclusions; a frontier is never a dead end."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .contracts import AnalysisInput, AnalysisState, PropertyResult, state_key

if TYPE_CHECKING:
    from .explorer import ExplorationGraph


@dataclass(frozen=True, slots=True)
class TerminalClassification:
    classification: str
    terminal_marked: bool
    completion_violations: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.classification in ("SUCCESS", "PERMITTED_FAILURE")


def classify_terminal(analysis_input: AnalysisInput,
                      state: AnalysisState) -> TerminalClassification:
    contract = analysis_input.terminal_contract
    live = tuple(token for token in state.base.tokens
                 if token.consumed_by is None)
    places = {token.place for token in live}
    success = bool(places.intersection(contract.success_places))
    failure = bool(places.intersection(contract.permitted_failure_places))
    if not success and not failure:
        label = ("WAITING_EXTERNAL" if analysis_input.environment_contract.external_wait
                 else "NONTERMINAL")
        return TerminalClassification(label, False)
    reasons = []
    if state.active:
        reasons.append("active firing occurrences remain")
    allowed_places = set(contract.success_places + contract.permitted_failure_places
                         + contract.persistent_places + contract.released_lease_places)
    for token in live:
        if token.place in contract.unfinished_places or token.place not in allowed_places:
            reasons.append(f"unfinished token {token.token_id} at {token.place}")
        if token.lease_claims:
            reasons.append(f"unreleased lease claims on token {token.token_id}")
        if (token.lease_identity_ref is not None
                and token.place not in contract.released_lease_places
                and token.place not in contract.persistent_places):
            reasons.append(f"unreleased lease token {token.token_id}")
    if success and failure:
        reasons.append("simultaneous success and permitted-failure markers")
    if reasons:
        return TerminalClassification("NONTERMINAL", True, tuple(reasons))
    return TerminalClassification("SUCCESS" if success else "PERMITTED_FAILURE", True)


def _reverse_reachable(graph, targets):
    incoming = [[] for _ in graph.nodes]
    for edge in graph.edges:
        if edge.target is not None:
            incoming[edge.target].append(edge.source)
    reached = set(targets)
    queue = deque(sorted(reached))
    while queue:
        for source in incoming[queue.popleft()]:
            if source not in reached:
                reached.add(source)
                queue.append(source)
    return reached


def _nonterminal_cycle(graph, terminal):
    """Iterative DFS: return an exact-state prefix and loop, without fairness."""
    if 0 in terminal:
        return None
    outgoing = [[] for _ in graph.nodes]
    for i, edge in enumerate(graph.edges):
        if edge.target is not None and edge.source not in terminal and edge.target not in terminal:
            outgoing[edge.source].append(i)
    colors = {0: 1}
    path_nodes = [0]
    path_edges = []
    stack = [iter(outgoing[0])]
    while stack:
        edge_index = next(stack[-1], None)
        if edge_index is None:
            colors[path_nodes.pop()] = 2
            stack.pop()
            if path_edges:
                path_edges.pop()
            continue
        edge = graph.edges[edge_index]
        target = edge.target
        if colors.get(target) == 1:
            start = path_nodes.index(target)
            sequence = path_edges + [edge_index]
            return (tuple(graph.edges[i].successor.action.binding for i in sequence),
                    start, target)
        if colors.get(target, 0) == 0:
            colors[target] = 1
            path_nodes.append(target)
            path_edges.append(edge_index)
            stack.append(iter(outgoing[target]))
    return None


def _before_completion(graph, terminal):
    """States reached before the first clean completion on a path."""
    reached = set()
    queue = deque([0])
    outgoing = [[] for _ in graph.nodes]
    for edge in graph.edges:
        if edge.target is not None:
            outgoing[edge.source].append(edge.target)
    while queue:
        node = queue.popleft()
        if node in reached or node in terminal:
            continue
        reached.add(node)
        queue.extend(outgoing[node])
    return reached


def evaluate_properties(analysis_input: AnalysisInput,
                        graph: ExplorationGraph) -> tuple[PropertyResult, ...]:
    from . import semantics
    from .evidence import replay_witness

    assumptions = graph.support.assumptions
    limits = "; ".join(sorted({reason for item in graph.frontier for reason in item.reasons}))
    unknown_reason = limits or "complete supported graph required"
    classifications = tuple(classify_terminal(analysis_input, node.state) for node in graph.nodes)
    allowed = {i for i, item in enumerate(classifications) if item.allowed}
    success = {i for i, item in enumerate(classifications) if item.classification == "SUCCESS"}
    terminal_defined = bool(analysis_input.terminal_contract.success_places
                            or analysis_input.terminal_contract.permitted_failure_places)
    results = []

    def emit(name, verdict, reason, quantifier, witness=(), *, endpoint=None,
             violation=False):
        # Recheck each finite path used to establish a positive existential or
        # negative universal result. Graph completeness proofs use all edges.
        if witness or endpoint is not None or violation:
            replay = replay_witness(analysis_input, witness)
            if (not replay.valid
                    or (endpoint is not None and (replay.state is None or
                        state_key(replay.state) != state_key(graph.nodes[endpoint].state)))
                    or (violation and not replay.safety_violations)):
                verdict, reason = "UNKNOWN", "witness replay rejected"
                witness = ()
        results.append(PropertyResult(name, verdict, reason, quantifier,
                                      assumptions=assumptions, witness=witness))

    # AnalysisPolicy normalizes categories before selection, reporting or gating.
    selected = tuple(dict.fromkeys(analysis_input.policy.properties
                                   + analysis_input.policy.required_properties))
    for name in selected:
        if not graph.support.supported:
            emit(name, "UNKNOWN", unknown_reason, "unsupported input semantics")
            continue
        if name == "safety":
            bad = next(((i, reasons) for i, node in enumerate(graph.nodes)
                        if (reasons := semantics.check_state_safety(analysis_input, node.state))), None)
            bad_edge = next((edge for edge in graph.edges if edge.successor.safety_violations), None)
            if bad is not None:
                i, reasons = bad
                emit(name, "VIOLATED", "; ".join(reasons), "all reachable states and steps",
                     graph.path_to(i), endpoint=i)
            elif bad_edge is not None:
                emit(name, "VIOLATED", "; ".join(bad_edge.successor.safety_violations),
                     "all reachable states and steps", graph.path_to(bad_edge.source)
                     + (bad_edge.successor.action.binding,), violation=True)
            else:
                emit(name, "HOLDS" if graph.complete else "UNKNOWN",
                     "all exact states and steps satisfy safety" if graph.complete else unknown_reason,
                     "all reachable states and steps")
        elif name == "enabledness":
            enumeration = graph.initial_bindings
            if enumeration and enumeration.bindings:
                # Enabledness is a state query, independent of a terminal-stop
                # scheduling decision; validate using the exact marking API.
                binding = enumeration.bindings[0]
                try:
                    valid = bool(semantics.successors(analysis_input, graph.nodes[0].state, binding))
                except (ValueError, TypeError, KeyError):
                    valid = False
                emit(name, "HOLDS" if valid else "UNKNOWN", "current state has a modeled action"
                     if valid else "enabled binding replay rejected", "exists current enabled action")
            elif enumeration and enumeration.complete:
                emit(name, "VIOLATED", "current state has no enabled action", "exists current enabled action",
                     endpoint=0)
            else:
                emit(name, "UNKNOWN", "current enabled bindings not fully enumerated", "exists current enabled action")
        elif name == "terminal_classification":
            classification = classifications[0]
            emit(name, "HOLDS" if terminal_defined else "UNKNOWN",
                 classification.classification if terminal_defined else "terminal contract absent",
                 "current state classification", endpoint=0)
        elif name == "proper_completion":
            bad = next((i for i, item in enumerate(classifications) if item.completion_violations), None)
            if bad is not None:
                emit(name, "VIOLATED", "; ".join(classifications[bad].completion_violations),
                     "all reachable terminal-marked states", graph.path_to(bad), endpoint=bad)
            else:
                emit(name, "HOLDS" if graph.complete and terminal_defined else "UNKNOWN",
                     "every terminal-marked state is clean" if graph.complete and terminal_defined
                     else unknown_reason if terminal_defined else "terminal contract absent",
                     "all reachable terminal-marked states")
        elif name == "possible_successful_completion":
            if success:
                node = min(success)
                emit(name, "HOLDS", "clean SUCCESS state is reachable", "exists path to SUCCESS",
                     graph.path_to(node), endpoint=node)
            else:
                emit(name, "VIOLATED" if graph.complete and terminal_defined else "UNKNOWN",
                     "no reachable SUCCESS state" if graph.complete and terminal_defined else unknown_reason,
                     "exists path to SUCCESS")
        elif name == "allowed_completion_from_every_state":
            reachable = _reverse_reachable(graph, allowed)
            missing = sorted(set(range(len(graph.nodes))) - reachable)
            if graph.complete and terminal_defined:
                if missing:
                    node = missing[0]
                    emit(name, "VIOLATED", "reachable state has no path to an allowed completion",
                         "forall reachable states exists path to allowed completion",
                         graph.path_to(node), endpoint=node)
                else:
                    emit(name, "HOLDS", "each reachable state can reach an allowed completion",
                         "forall reachable states exists path to allowed completion")
            else:
                emit(name, "UNKNOWN", unknown_reason if terminal_defined else "terminal contract absent",
                     "forall reachable states exists path to allowed completion")
        elif name == "dead_transition":
            transitions = {transition.name for transition in analysis_input.compiled.symbolic.transitions}
            fired = {edge.successor.action.binding.transition_id for edge in graph.edges
                     if edge.successor.action.binding.kind == "START" and edge.successor.state is not None
                     and not edge.successor.unknown_reasons}
            dead = sorted(transitions - fired)
            if not transitions:
                emit(name, "NOT_APPLICABLE", "net contains no transitions", "every declared transition can fire")
            else:
                emit(name, ("VIOLATED" if dead else "HOLDS") if graph.complete else "UNKNOWN",
                     ("never fires: " + ", ".join(dead) if dead else "every transition fires in the reachable graph")
                     if graph.complete else unknown_reason, "every declared transition can fire")
        elif name == "inevitable_completion_without_fairness":
            if analysis_input.scheduler_contract.fairness != "none":
                emit(name, "UNKNOWN", "fairness is not supported", "all maximal paths reach allowed completion")
                continue
            cycle = _nonterminal_cycle(graph, allowed)
            if cycle:
                witness, cycle_start, target = cycle
                replay = replay_witness(analysis_input, witness[:cycle_start])
                loop = replay_witness(analysis_input, witness)
                if (replay.valid and loop.valid and replay.state is not None and loop.state is not None
                        and state_key(replay.state) == state_key(loop.state)):
                    emit(name, "VIOLATED", f"exact-state nonterminal cycle starts after {cycle_start} actions",
                         "all maximal paths reach allowed completion", witness, endpoint=target)
                    continue
            if not graph.complete or not terminal_defined:
                emit(name, "UNKNOWN", unknown_reason if terminal_defined else "terminal contract absent",
                     "all maximal paths reach allowed completion")
                continue
            outgoing = {edge.source for edge in graph.edges if edge.target is not None}
            dead = next((i for i in sorted(_before_completion(graph, allowed)) if i not in outgoing), None)
            if dead is not None:
                emit(name, "VIOLATED", "nonterminal maximal finite path",
                     "all maximal paths reach allowed completion", graph.path_to(dead), endpoint=dead)
            elif cycle is None:
                emit(name, "HOLDS", "complete graph has no nonterminal maximal path or cycle",
                     "all maximal paths reach allowed completion")
            else:
                emit(name, "UNKNOWN", "cycle replay rejected", "all maximal paths reach allowed completion")
        elif name == "local_progress":
            emit(name, "UNKNOWN", "explicit modeled local agent/task progress obligations absent",
                 "each declared local progress obligation")
        else:
            emit(name, "UNKNOWN", "unsupported property ID", "unspecified")
    return tuple(results)
