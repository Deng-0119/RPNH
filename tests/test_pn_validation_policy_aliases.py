"""Shared canonical property IDs for finite reports and strict owner policy."""
import json

from cpn.rpnh.pn_validation import AnalysisPolicy, analyze
from cpn.rpnh.pn_validation.runtime_gate import (
    ValidationConfiguration, configuration_from_dict, policy_allows,
)
from test_pn_validation_semantics import make_input


COMPLETION_IDS = (
    "terminal_classification", "proper_completion",
    "possible_successful_completion", "allowed_completion_from_every_state",
)
CYCLE_ID = "inevitable_completion_without_fairness"


def alias_policy():
    return AnalysisPolicy(mode="strict", properties=("cycle", "local_progress"),
        required_properties=("completion", "cycle", "proper_completion", "completion"))


def test_completion_and_cycle_aliases_allow_strict_success():
    policy = alias_policy()
    assert policy.properties == (CYCLE_ID, "local_progress")
    assert policy.required_properties == (*COMPLETION_IDS, CYCLE_ID)
    analysis = make_input(policy=policy)
    report = analyze(analysis)
    results = {item.property_id: item.verdict for item in report.properties}
    assert report.graph.complete
    assert tuple(results) == (CYCLE_ID, "local_progress", *COMPLETION_IDS)
    assert all(results[name] == "HOLDS" for name in policy.required_properties)
    assert results["local_progress"] == "UNKNOWN"
    encoded = report.to_dict()
    assert encoded["algorithm_config"]["required_properties"] == [*COMPLETION_IDS, CYCLE_ID]
    assert policy_allows(ValidationConfiguration(policy=policy), encoded)


def test_aliases_keep_unmodeled_data_unknown_and_strict_rejects():
    policy = alias_policy()
    analysis = make_input(output_channel="data", policy=policy)
    report = analyze(analysis)
    results = {item.property_id: item.verdict for item in report.properties}
    assert not report.graph.support.supported
    assert tuple(results) == (CYCLE_ID, "local_progress", *COMPLETION_IDS)
    assert all(verdict == "UNKNOWN" for verdict in results.values())
    assert not policy_allows(ValidationConfiguration(policy=policy), report.to_dict())


def test_configuration_parser_roundtrip_uses_canonical_required_ids():
    document = ValidationConfiguration().to_dict()
    document["policy"].update(mode="strict",
        properties=["cycle", "completion", "proper_completion", "cycle", "unmodeled_property"],
        required_properties=["completion", "cycle", "proper_completion", "completion", "unmodeled_property"])
    original = json.dumps(document)
    configuration = configuration_from_dict(json.loads(original))
    assert configuration.policy.properties == (CYCLE_ID, *COMPLETION_IDS, "unmodeled_property")
    assert configuration.policy.required_properties == (*COMPLETION_IDS, CYCLE_ID, "unmodeled_property")
    encoded = configuration.to_dict()
    assert encoded["policy"]["required_properties"] == [*COMPLETION_IDS, CYCLE_ID, "unmodeled_property"]
    assert configuration_from_dict(json.loads(json.dumps(encoded))) == configuration
    assert json.dumps(document) == original
