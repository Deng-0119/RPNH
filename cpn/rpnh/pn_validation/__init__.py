"""Finite exact PN analysis; reports confer no Registry execution authority."""
from .contracts import (
    ActionBinding, ActionEvidence, ActiveOccurrence, AnalysisInput, AnalysisPolicy,
    AnalysisState, BindingEnumeration, EnvironmentContract, OperationCase,
    OperationModel, ProducedSpec, PropertyResult, SchedulerContract,
    SupportResult, TerminalContract, canonical_data, canonical_json, state_key,
)
from .evidence import (AnalysisReport, ReplayResult, analyze, input_digest,
                       replay_witness, verify_graph, verify_report_binding, verify_report)
from .explorer import ExplorationGraph, Frontier, GraphEdge, GraphNode, explore
from .properties import TerminalClassification, classify_terminal, evaluate_properties
from .projection import build_analysis_input

__all__ = [
    "ActionBinding", "ActionEvidence", "ActiveOccurrence", "AnalysisInput",
    "AnalysisPolicy", "AnalysisState", "BindingEnumeration", "EnvironmentContract",
    "OperationCase", "OperationModel", "ProducedSpec", "PropertyResult",
    "SchedulerContract", "SupportResult", "TerminalContract", "canonical_data",
    "canonical_json", "state_key", "AnalysisReport", "ReplayResult", "analyze",
    "input_digest", "replay_witness", "verify_graph", "verify_report_binding", "verify_report",
    "ExplorationGraph", "Frontier", "GraphEdge", "GraphNode", "explore",
    "TerminalClassification", "classify_terminal", "evaluate_properties",
    "build_analysis_input",
]
