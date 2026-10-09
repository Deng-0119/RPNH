"""Input-bound analysis evidence and executor-free exact witness replay."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import math

from .contracts import (ActionBinding, AnalysisInput, AnalysisState, PropertyResult,
                        SCHEMA_VERSION, SEMANTICS_VERSION, canonical_data,
                        canonical_json, state_key)
from .explorer import ExplorationGraph, Frontier, GraphEdge, GraphNode, explore

ALGORITHM_VERSION = "exact-bfs/v1"


def input_digest(analysis_input: AnalysisInput) -> str:
    """Replace full re-analysis at adoption with exact input equality checking."""
    return sha256(canonical_json(analysis_input).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReplayResult:
    valid: bool
    state: AnalysisState | None
    safety_violations: tuple[str, ...] = ()
    reason: str = ""


def replay_witness(analysis_input: AnalysisInput,
                   witness: tuple[ActionBinding, ...]) -> ReplayResult:
    from . import semantics
    from .properties import classify_terminal

    support = semantics.check_supported_semantics(analysis_input)
    if not support.supported:
        return ReplayResult(False, None, reason="unsupported input semantics")
    state = analysis_input.initial_state
    for index, binding in enumerate(witness):
        if (analysis_input.terminal_contract.stop_on_terminal
                and classify_terminal(analysis_input, state).allowed):
            return ReplayResult(False, state, reason="action after explicit terminal stop")
        try:
            candidates = semantics.successors(analysis_input, state, binding)
        except (ValueError, TypeError, KeyError) as exc:
            return ReplayResult(False, state, reason=f"binding rejected: {exc}")
        if len(candidates) != 1:
            return ReplayResult(False, state, reason="binding has no unique successor")
        successor = candidates[0]
        if successor.action.binding != binding or successor.unknown_reasons:
            return ReplayResult(False, state, reason="unmodeled or mismatched action")
        if successor.safety_violations:
            return ReplayResult(index == len(witness) - 1, successor.state,
                                successor.safety_violations, "safety counterexample")
        if successor.state is None:
            return ReplayResult(False, state, reason="missing successor")
        state = successor.state
    return ReplayResult(True, state,
                        semantics.check_state_safety(analysis_input, state))


def verify_graph(analysis_input: AnalysisInput, graph: ExplorationGraph) -> bool:
    """Replay stored before/after, binding, outcome and occurrence evidence."""
    from . import semantics

    if not graph.nodes or graph.nodes[0].state != analysis_input.initial_state:
        return False
    for edge in graph.edges:
        try:
            candidates = semantics.successors(analysis_input, graph.nodes[edge.source].state,
                                               edge.successor.action.binding)
            if len(candidates) != 1 or canonical_json(candidates[0]) != canonical_json(edge.successor):
                return False
            if edge.target is not None and (edge.successor.state is None or
                    state_key(graph.nodes[edge.target].state) != state_key(edge.successor.state)):
                return False
        except (IndexError, ValueError, TypeError, KeyError):
            return False
    return True


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    input_json: str
    input_digest: str
    graph: ExplorationGraph
    properties: tuple[PropertyResult, ...]
    schema_version: str = SCHEMA_VERSION
    semantics_version: str = SEMANTICS_VERSION
    algorithm_version: str = ALGORITHM_VERSION

    def to_dict(self) -> dict:
        """Full semantic evidence for the private committed gate.

        Input projection must contain semantic data only, never provider secrets
        or executor payloads. Use to_public_dict for a shareable report summary.
        """
        material = json.loads(self.input_json)
        return {
            "schema_version": self.schema_version,
            "semantics_version": self.semantics_version,
            "algorithm_version": self.algorithm_version,
            "input_digest": self.input_digest,
            "input": material,
            "algorithm_config": material["policy"],
            "assumed_contracts": {key: material[key] for key in (
                "operation_models", "terminal_contract", "environment_contract", "scheduler_contract")},
            "properties": canonical_data(self.properties),
            "graph": dict(canonical_data(self.graph), complete=self.graph.complete),
        }

    def to_public_dict(self) -> dict:
        """Semantic summary without source context, executable or full net data."""
        result = self.to_dict()
        result.pop("input")
        return result

    def verify_binding(self, current_input: AnalysisInput) -> bool:
        return verify_report_binding(self, current_input)


def verify_report_binding(report: AnalysisReport | Mapping,
                          current_input: AnalysisInput) -> bool:
    """Reject stale inputs; this checks binding, not third-party authenticity."""
    try:
        document = report.to_dict() if isinstance(report, AnalysisReport) else report
        if not isinstance(document, Mapping):
            return False
        if (document.get("schema_version") != SCHEMA_VERSION
                or document.get("semantics_version") != SEMANTICS_VERSION
                or document.get("algorithm_version") != ALGORITHM_VERSION):
            return False
        current = canonical_data(current_input)
        material = document.get("input")
        return (canonical_json(material) == canonical_json(current)
                and document.get("input_digest") == input_digest(current_input)
                and canonical_json(document.get("algorithm_config")) == canonical_json(current["policy"])
                and canonical_json(document.get("assumed_contracts")) == canonical_json({
                    key: current[key] for key in ("operation_models", "terminal_contract",
                                                 "environment_contract", "scheduler_contract")}))
    except (ValueError, TypeError, KeyError):
        return False


def _binding_from_data(data) -> ActionBinding:
    from ..registry.identities import TypedId
    from ..registry.models import VersionRef

    def ref(value):
        if value is None:
            return None
        return VersionRef(value["entity_type"], TypedId(**value["entity_id"]),
                          TypedId(**value["version_id"]))

    binding = ActionBinding(data["kind"], data["transition_id"],
                            tuple(ref(value) for value in data["token_refs"]),
                            ref(data["firing_ref"]), data["case_id"])
    if canonical_json(binding) != canonical_json(data):
        raise ValueError("noncanonical action binding")
    return binding


def _checked_graph(current_input: AnalysisInput, data: Mapping) -> ExplorationGraph:
    """Reconstruct DTOs from real successors within the stored graph scope.

    Nodes are derived from their parent edges, then their full typed state is
    compared to the stored state. This needs no second token/claim DTO schema.
    Only expanded nodes require exhaustive outgoing enumeration. Wall-clock
    cutoff is an allowed stopping reason, never a claim of graph closure.
    """
    from . import semantics
    from .properties import classify_terminal

    policy = current_input.policy
    raw_nodes, raw_edges = data["nodes"], data["edges"]
    count = len(raw_nodes)
    if not 1 <= count <= max(1, policy.max_states) or len(raw_edges) > max(0, policy.max_edges):
        raise ValueError("graph exceeds bound")

    def index(value, limit):
        if type(value) is not int or not 0 <= value < limit:
            raise ValueError("invalid graph index")
        return value

    def depth(value):
        if type(value) is not int or not 0 <= value <= max(0, policy.max_depth):
            raise ValueError("invalid graph depth")
        return value

    support = semantics.check_supported_semantics(current_input)
    if canonical_json(support) != canonical_json(data["support"]):
        raise ValueError("support mismatch")
    nodes = [GraphNode(current_input.initial_state, 0)]
    if canonical_json(nodes[0]) != canonical_json(raw_nodes[0]):
        raise ValueError("initial state mismatch")
    replayed = {}

    def replay(edge_index):
        if edge_index not in replayed:
            edge = raw_edges[edge_index]
            source = index(edge["source"], len(nodes))
            if nodes[source].depth >= policy.max_depth:
                raise ValueError("edge beyond depth budget")
            binding = _binding_from_data(edge["successor"]["action"]["binding"])
            candidates = semantics.successors(current_input, nodes[source].state, binding)
            if len(candidates) != 1 or canonical_json(candidates[0]) != canonical_json(edge["successor"]):
                raise ValueError("successor evidence mismatch")
            replayed[edge_index] = candidates[0]
        return replayed[edge_index]

    for node_index in range(1, count):
        raw = raw_nodes[node_index]
        parent = index(raw["parent_edge"], len(raw_edges))
        edge = raw_edges[parent]
        source = index(edge["source"], node_index)
        if type(edge["target"]) is not int or edge["target"] != node_index:
            raise ValueError("parent edge does not reach node")
        successor = replay(parent)
        if successor.state is None:
            raise ValueError("invalid outcome cannot be a graph node")
        node = GraphNode(successor.state, depth(raw["depth"]), parent)
        if node.depth != nodes[source].depth + 1 or canonical_json(node) != canonical_json(raw):
            raise ValueError("node state or parent/depth mismatch")
        nodes.append(node)
    keys = [state_key(node.state) for node in nodes]
    if len(set(keys)) != count:
        raise ValueError("duplicate exact graph state")
    edges = []
    outgoing = [[] for _ in nodes]
    first_incoming = {}
    for edge_index, raw in enumerate(raw_edges):
        source = index(raw["source"], count)
        target = None if raw["target"] is None else index(raw["target"], count)
        successor = replay(edge_index)
        if target is not None:
            if successor.state is None or state_key(successor.state) != keys[target]:
                raise ValueError("edge target state mismatch")
            first_incoming.setdefault(target, edge_index)
        edges.append(GraphEdge(source, target, successor))
        outgoing[source].append(edges[-1])
    for node_index in range(1, count):
        if first_incoming.get(node_index) != nodes[node_index].parent_edge:
            raise ValueError("parent is not the first incoming edge")
    expanded = tuple(index(i, count) for i in data["expanded"])
    frontier = tuple(Frontier(index(item["node"], count), tuple(item["reasons"]))
                     for item in data["frontier"])
    pending = {item.node: item.reasons for item in frontier}
    if (len(set(expanded)) != len(expanded) or len(pending) != len(frontier)
            or set(expanded).intersection(pending)
            or set(expanded).union(pending) != set(range(count))
            or any(not reasons or any(type(reason) is not str or not reason for reason in reasons)
                   for reasons in pending.values())):
        raise ValueError("unfinished nodes must have a frontier")
    if not support.supported:
        if count != 1 or edges or expanded or data["initial_bindings"] is not None:
            raise ValueError("unsupported graph cannot be expanded")
        if pending[0] != (support.reasons or ("unsupported semantics",)):
            # Explorer records a sorted reason set, not support's input order.
            if pending[0] != tuple(sorted(set(support.reasons or ("unsupported semantics",)))):
                raise ValueError("unsupported frontier mismatch")
        initial_bindings = None
    else:
        enumerations = {}
        raw_initial = data["initial_bindings"]
        stored_initial = None
        if raw_initial is not None:
            from .contracts import BindingEnumeration
            candidate_count = raw_initial["candidates_examined"]
            if (type(candidate_count) is not int or not 0 <= candidate_count <= policy.max_bindings
                    or type(raw_initial["complete"]) is not bool):
                raise ValueError("initial enumeration exceeds its candidate bound")
            stored_initial = BindingEnumeration(
                tuple(_binding_from_data(binding) for binding in raw_initial["bindings"]),
                raw_initial["complete"], tuple(raw_initial["reasons"]), candidate_count)
            if canonical_json(stored_initial) != canonical_json(raw_initial):
                raise ValueError("noncanonical initial enumeration")
        stopped = {i for i, node in enumerate(nodes)
                   if current_input.terminal_contract.stop_on_terminal
                   and classify_terminal(current_input, node.state).allowed}
        for i, node in enumerate(nodes):
            if i in stopped and outgoing[i]:
                raise ValueError("action after terminal stop")
            # An untouched time/depth/graph-budget frontier makes no
            # enabledness or closure claim. Do not restart its search under
            # the Registry writer lock solely to reconfirm UNKNOWN.
            need_enumeration = ((i == 0 and stored_initial is not None)
                or (i not in stopped and (i in expanded or outgoing[i]
                    or set(pending.get(i, ())) - {"max_seconds", "max_states", "max_edges", "max_depth"})))
            if not need_enumeration:
                enumeration = None
                expected_bindings = set()
            elif i == 0 and stored_initial is not None and not stored_initial.complete:
                if stored_initial.reasons == ("max_seconds",):
                    prefix = semantics.enabled_bindings(current_input, node.state,
                        _candidate_limit=stored_initial.candidates_examined)
                    if prefix.candidates_examined != stored_initial.candidates_examined:
                        raise ValueError("stored candidate prefix is longer than the real enumeration")
                    if stored_initial.candidates_examined == 0:
                        # Deadline may interrupt the explicit SETTLE choices
                        # before START enumeration. They keep their input
                        # order and do not allocate a Cartesian pool.
                        if (any(binding.kind != "SETTLE" for binding in stored_initial.bindings)
                                or prefix.bindings[:len(stored_initial.bindings)] != stored_initial.bindings):
                            raise ValueError("stored settlement prefix differs")
                    elif prefix.bindings != stored_initial.bindings:
                        raise ValueError("stored candidate prefix differs")
                    enumeration = stored_initial
                else:
                    enumeration = semantics.enabled_bindings(current_input, node.state)
                    if enumeration != stored_initial:
                        raise ValueError("stored binding cutoff differs")
            else:
                enumeration = semantics.enabled_bindings(current_input, node.state)
            if enumeration is not None:
                enumerations[i] = enumeration
                expected_bindings = (set() if i in stopped else
                    {canonical_json(binding) for binding in enumeration.bindings})
            actual_bindings = [canonical_json(edge.successor.action.binding) for edge in outgoing[i]]
            if len(set(actual_bindings)) != len(actual_bindings) or not set(actual_bindings) <= expected_bindings:
                raise ValueError("edge is not a distinct enabled binding")
            unresolved = any(edge.target is None or edge.successor.unknown_reasons for edge in outgoing[i])
            if i in expanded:
                if (unresolved or set(actual_bindings) != expected_bindings
                        or (i not in stopped and enumeration is not None and not enumeration.complete)):
                    raise ValueError("expanded node lacks exhaustive resolved successors")
            else:
                valid_reasons = {"max_seconds"}
                if count >= policy.max_states:
                    valid_reasons.add("max_states")
                if len(edges) >= policy.max_edges:
                    valid_reasons.add("max_edges")
                if node.depth >= policy.max_depth:
                    valid_reasons.add("max_depth")
                if enumeration is not None and not enumeration.complete:
                    valid_reasons.update(enumeration.reasons or ("binding enumeration incomplete",))
                for edge in outgoing[i]:
                    valid_reasons.update(edge.successor.unknown_reasons)
                    if edge.successor.state is None:
                        valid_reasons.add("unsafe successor unresolved" if edge.successor.safety_violations
                                          else "successor unavailable")
                # A non-unique successor was not stored as an edge. Check only
                # the available modeled bindings when that reason is claimed.
                if "binding has no unique modeled successor" in pending[i] and enumeration is not None:
                    if any(len(semantics.successors(current_input, node.state, binding)) != 1
                           for binding in enumeration.bindings):
                        valid_reasons.add("binding has no unique modeled successor")
                if not set(pending[i]) <= valid_reasons:
                    raise ValueError("frontier reason mismatch")
        initial_bindings = None
        if data["initial_bindings"] is not None:
            initial_bindings = enumerations.get(0)
            if initial_bindings is None or canonical_json(initial_bindings) != canonical_json(data["initial_bindings"]):
                raise ValueError("initial binding enumeration mismatch")
        elif 0 not in stopped and (outgoing[0] or 0 in expanded or "max_seconds" not in pending.get(0, ())):
            raise ValueError("initial binding enumeration missing")
    elapsed = data["elapsed_seconds"]
    if type(elapsed) not in (float, int) or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("invalid elapsed time")
    graph = ExplorationGraph(tuple(nodes), tuple(edges), expanded, frontier, support,
                             initial_bindings, elapsed)
    if canonical_json(dict(canonical_data(graph), complete=graph.complete)) != canonical_json(data):
        raise ValueError("graph fields or completeness mismatch")
    return graph


def verify_report(report: AnalysisReport | Mapping,
                  current_input: AnalysisInput) -> bool:
    """Verify stored exact graph evidence and conclusions without new BFS.

    Work is bounded by stored nodes/edges, explicit occurrence/model choices,
    and the original binding limit plus one exhaustion lookahead. Time-cutoff
    initial prefixes replay only their stored examined candidate count; untouched
    uncertain frontiers start no enumeration. No new semantic clock is used.
    Complete graphs require exhaustive expanded nodes; legitimate prefixes keep
    their UNKNOWN conclusions regardless of the verifier's wall-clock speed.
    """
    from .properties import evaluate_properties

    if not verify_report_binding(report, current_input):
        return False
    try:
        supplied = report.to_dict() if isinstance(report, AnalysisReport) else canonical_data(report)
        graph = _checked_graph(current_input, supplied["graph"])
        expected = AnalysisReport(canonical_json(current_input), input_digest(current_input), graph,
                                  evaluate_properties(current_input, graph)).to_dict()
        return canonical_json(supplied) == canonical_json(expected)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return False


def analyze(analysis_input: AnalysisInput) -> AnalysisReport:
    from .properties import evaluate_properties

    # Freeze the entire semantic input before analysis; reject mutation during
    # analysis rather than binding results to a different snapshot afterward.
    frozen = canonical_json(analysis_input)
    graph = explore(analysis_input)
    results = evaluate_properties(analysis_input, graph)
    if canonical_json(analysis_input) != frozen:
        raise ValueError("analysis input changed during exploration")
    return AnalysisReport(frozen, sha256(frozen.encode("utf-8")).hexdigest(), graph, results)
