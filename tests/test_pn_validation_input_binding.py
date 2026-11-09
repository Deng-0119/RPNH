"""Full PN input/proof binding with the root digest retained as diagnostics."""
from copy import deepcopy
from dataclasses import fields, replace
import json

import pytest

from cpn.rpnh.pn_validation import analyze, input_digest, verify_report, verify_report_binding
from cpn.rpnh.pn_validation.contracts import AnalysisInput, canonical_data, canonical_json
from test_pn_validation_semantics import make_input


@pytest.fixture(scope="module")
def bound_report():
    analysis = make_input()
    return analysis, analyze(analysis)


def change_diagnostic(document, case):
    if case == "missing":
        document.pop("input_digest")
    else:
        document["input_digest"] = {
            "wrong": "not-the-recorded-input-digest",
            "integer": 7,
            "boolean": True,
            "null": None,
            "structured": {"old_diagnostic": [True, "unbound"]},
        }[case]


@pytest.mark.parametrize("case", ["missing", "wrong", "integer", "boolean", "null", "structured"])
def test_retired_root_digest_does_not_decide_binding_or_proof(bound_report, case):
    analysis, report = bound_report
    document = json.loads(json.dumps(report.to_dict(), indent=2))
    change_diagnostic(document, case)
    before = deepcopy(document)
    assert verify_report_binding(document, analysis)
    assert verify_report(document, analysis)
    assert document == before
    if case != "missing":
        changed_report = replace(report, input_digest=deepcopy(document["input_digest"]))
        assert verify_report_binding(changed_report, analysis)
        assert verify_report(changed_report, analysis)


def test_verifiers_do_not_call_report_digest_but_producer_contract_remains(bound_report, monkeypatch):
    import cpn.rpnh.pn_validation.evidence as evidence
    analysis, report = bound_report
    diagnostic = input_digest(analysis)
    assert report.input_digest == diagnostic
    assert report.to_dict()["input_digest"] == diagnostic
    assert report.to_public_dict()["input_digest"] == diagnostic
    assert [f.name for f in fields(type(report))] == [
        "input_json", "input_digest", "graph", "properties", "schema_version",
        "semantics_version", "algorithm_version",
    ]

    def forbidden(*args, **kwargs):
        raise AssertionError("report verification must not compute a diagnostic SHA")

    monkeypatch.setattr(evidence, "input_digest", forbidden)
    monkeypatch.setattr(evidence, "sha256", forbidden)
    for value in (report, report.to_dict(), replace(report, input_digest="wrong")):
        assert verify_report_binding(value, analysis)
        assert verify_report(value, analysis)


def test_all_fourteen_input_fields_remain_bound_without_digest(bound_report):
    analysis, report = bound_report
    names = [f.name for f in fields(AnalysisInput)]
    assert len(names) == 14
    for name in names:
        document = report.to_dict()
        document.pop("input_digest")
        document["input"][name] = {"changed_input_field": name}
        assert not verify_report_binding(document, analysis), name
        assert not verify_report(document, analysis), name


def test_all_other_report_constraints_and_nested_fields_remain(bound_report):
    analysis, report = bound_report
    for case in ("schema_version", "semantics_version", "algorithm_version",
                 "algorithm_config", "assumed_contracts", "graph", "properties",
                 "unexpected_root", "nested_digest"):
        # A wire round trip detaches the repeated input/config/contract objects.
        document = json.loads(json.dumps(report.to_dict()))
        document.pop("input_digest")
        if case in ("schema_version", "semantics_version", "algorithm_version"):
            document[case] = "unsupported"
        elif case == "algorithm_config":
            document[case]["max_states"] += 1
        elif case == "assumed_contracts":
            document[case]["scheduler_contract"]["fairness"] = "changed"
        elif case == "graph":
            document[case]["edges"] = []
        elif case == "properties":
            document[case][0]["verdict"] = "VIOLATED"
        elif case == "unexpected_root":
            document["unexpected"] = True
        else:
            document["graph"]["input_digest"] = "not-a-retired-root-field"
        assert not verify_report(document, analysis), case
    assert not verify_report_binding(report.to_public_dict(), analysis)
    assert not verify_report(report.to_public_dict(), analysis)


def test_current_input_utf8_boundary_and_source_string_identity_remain(bound_report):
    analysis, report = bound_report
    invalid = replace(analysis, source_context_json='{"value":"\ud800"}')
    document = report.to_dict()
    document.pop("input_digest")
    document["input"] = canonical_data(invalid)
    assert not verify_report_binding(document, invalid)
    assert not verify_report(document, invalid)
    # A literal JSON escape is a valid UTF-8 source-context string, not a raw surrogate.
    escaped = replace(analysis, source_context_json='{"value":"\\ud800"}')
    document["input"] = canonical_data(escaped)
    assert verify_report_binding(document, escaped)
    assert verify_report(document, escaped)
    assert not verify_report_binding(document, analysis)
    assert canonical_json(document["input"]) != canonical_json(canonical_data(analysis))


def test_full_report_still_requires_closed_data_in_diagnostic_value(bound_report):
    analysis, report = bound_report
    for value in (object(), float("nan"), float("inf")):
        document = report.to_dict()
        document["input_digest"] = value
        assert not verify_report(document, analysis)
        assert not verify_report(replace(report, input_digest=value), analysis)


@pytest.mark.parametrize("case", ["missing", "wrong", "null"])
def test_owner_adoption_and_latest_checkpoint_resume_keep_normal_bindings(tmp_path, monkeypatch, case):
    import cpn.rpnh.pn_validation.runtime_gate as gate
    from cpn.rpnh.run import resume_run
    from test_pn_validation_owner_adoption import configured_owner, finite_configuration
    from test_static_lease_reads import _registration
    from test_terminal_owner_adoption import adopt, current

    owner = configured_owner(tmp_path, finite_configuration())
    initial_report = deepcopy(owner.pn_validation_report)
    configuration_before = gate.registered_configuration(owner)
    original = gate.analyze_owner_preview
    changed_evidence = []

    def changed(current_owner, preview):
        evidence = deepcopy(original(current_owner, preview))
        change_diagnostic(evidence["report"], case)
        changed_evidence.append(deepcopy(evidence))
        return evidence

    monkeypatch.setattr(gate, "analyze_owner_preview", changed)
    result = adopt(owner, "PNControlB")
    assert result["status"] == "ADOPTED" and len(changed_evidence) == 1
    rows = owner._core.event_store.object_rows_by_type("resource_version/v1")
    adopted = [row for row in rows if str(row["transaction_id"]) == result["transaction_id"]
               and json.loads(row["metadata_json"])["descriptors"].get("owner_control_kind") == "result"]
    assert len(adopted) == 1
    body = json.loads(owner._core.object_store.read_registered(
        owner._core.get_version(adopted[0]["version_id"])))
    assert body["data"]["pn_validation"] == changed_evidence[0]
    before = current(owner)[2]
    owner.record_owner_stop(idempotency_key="pn:diagnostic:stop")
    resumed = resume_run(_registration(), run_dir=owner._core.run_dir,
                         model_condition="offline PN projection")
    after = current(resumed)[2]
    assert after.net_ref == before.net_ref and after.checkpoint_ref == before.checkpoint_ref
    assert after.epoch == before.epoch and after.next_token_id == before.next_token_id
    assert after.attempts == before.attempts
    assert tuple(t.state for t in after.tokens) == tuple(t.state for t in before.tokens)
    assert gate.registered_configuration(resumed) == configuration_before
    # Existing restore reads the initial report; it does not replay adopted proofs.
    assert resumed.pn_validation_report == initial_report
    assert resumed._core.event_store.actual_model_call_counts() == (0, 0)


@pytest.mark.parametrize("case", ["configuration_ref", "properties"])
def test_owner_gate_rejects_non_diagnostic_changes_atomically(tmp_path, monkeypatch, case):
    import cpn.rpnh.pn_validation.runtime_gate as gate
    from cpn.rpnh.registry.event_store import RegistryConflict
    from cpn.rpnh.registry.publication import _ref_payload
    from test_pn_validation_owner_adoption import configured_owner, finite_configuration
    from test_terminal_owner_adoption import adopt, current, events

    owner = configured_owner(tmp_path, finite_configuration())
    before = current(owner)[2]
    token_count = len(owner._core.event_store.object_rows_by_type("petri_token/v1"))
    adoption_count = len(events(owner, "net_adopted/v1"))
    original = gate.analyze_owner_preview

    def changed(current_owner, preview):
        evidence = deepcopy(original(current_owner, preview))
        change_diagnostic(evidence["report"], "wrong")
        if case == "configuration_ref":
            evidence[case] = _ref_payload(current_owner.publication.declaration_resource_ref.as_version_ref())
        else:
            evidence["report"][case][0]["verdict"] = "VIOLATED"
        return evidence

    monkeypatch.setattr(gate, "analyze_owner_preview", changed)
    with pytest.raises(RegistryConflict, match="PN evidence"):
        adopt(owner, "PNControlB")
    after = current(owner)[2]
    assert after.net_ref == before.net_ref and after.checkpoint_ref == before.checkpoint_ref
    assert after.epoch == before.epoch and after.next_token_id == before.next_token_id
    assert after.attempts == before.attempts
    assert tuple(t.state for t in after.tokens) == tuple(t.state for t in before.tokens)
    assert len(owner._core.event_store.object_rows_by_type("petri_token/v1")) == token_count
    assert len(events(owner, "net_adopted/v1")) == adoption_count
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)
