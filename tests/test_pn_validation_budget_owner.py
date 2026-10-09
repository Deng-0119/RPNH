"""Bounded weighted proofs through the actual Registry adoption writer."""
from copy import deepcopy
from dataclasses import replace
from itertools import combinations as real_combinations
import json

import pytest

from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.net_operations import apply_replacement, prepare_replacement
from cpn.rpnh.pn_validation import analyze
from cpn.rpnh.registry.event_store import RegistryConflict
from test_pn_validation_owner_adoption import configured_owner, finite_configuration
from test_static_lease_reads import TEXT
from test_terminal_owner_adoption import current, events


@pytest.mark.parametrize("branch", ["advisory-untouched", "advisory-prefix", "strict-writer"])
def test_weighted_timeout_report_is_bounded_in_real_adoption(tmp_path, monkeypatch, request, branch):
    import cpn.rpnh.pn_validation.explorer as explorer
    import cpn.rpnh.pn_validation.runtime_gate as gate
    import cpn.rpnh.pn_validation.semantics as semantics
    import cpn.rpnh.registry.pn_validation as registry_gate
    configuration = finite_configuration(mode="strict" if branch == "strict-writer" else "advisory")
    configuration = replace(configuration, policy=replace(configuration.policy, max_bindings=1))
    owner = configured_owner(tmp_path, configuration)
    before = current(owner)[2]
    token_count = len(owner._core.event_store.object_rows_by_type("petri_token/v1"))
    adoption_count = len(events(owner, "net_adopted/v1"))
    document = deepcopy(current(owner)[1].compiled.source.to_dict())
    document["name"] = "WeightedBudgetCandidate"
    for port in document["components"][0]["ports"]:
        if port["name"] == "request":
            port["cardinality"] = 15
    plan = prepare_replacement(owner, ModuleDeclaration.from_dict(document),
        owner_inputs={"step.request": tuple({"schema_id": TEXT,
            "value": "supplement " + str(i), "summary": "Finite weighted owner input"}
            for i in range(29))})
    observed = {"analysis_candidates": 0, "writer_candidates": 0, "writer_calls": 0}
    phase = {"value": "analysis"}
    clock = {"now": 0.0}

    def guarded(tokens, weight):
        for group in real_combinations(tokens, weight):
            if len(tokens) == 30 and weight == 15:
                key = phase["value"] + "_candidates"
                observed[key] += 1
                assert observed[key] <= 1, "weighted combinations consumed beyond stored prefix"
                if phase["value"] == "analysis":
                    clock["now"] = 100.0
            yield group

    monkeypatch.setattr(semantics, "combinations", guarded)
    real_preview = gate.analyze_owner_preview

    def early_timeout(owner, preview):
        if branch == "advisory-untouched":
            ticks = iter((0.0, 100.0, 100.0))
            monkeypatch.setattr(explorer, "monotonic", lambda: next(ticks))
        else:
            monkeypatch.setattr(explorer, "monotonic", lambda: clock["now"])
            monkeypatch.setattr(semantics, "monotonic", lambda: clock["now"])
        if branch == "strict-writer":
            # Bypass only the preliminary policy check to exercise the real
            # durable strict gate. Input, report and writer are unchanged.
            from cpn.rpnh.registry.publication import _ref_payload
            anchor, configured = gate.registered_configuration(owner)
            report = analyze(gate.analysis_for_owner(owner, configured, preview=preview)).to_dict()
            evidence = {"configuration_ref": _ref_payload(anchor.as_version_ref()), "report": report}
        else:
            evidence = real_preview(owner, preview)
        assert evidence["report"]["graph"]["frontier"]
        assert all(item["verdict"] == "UNKNOWN" for item in evidence["report"]["properties"])
        phase["value"] = "writer"
        clock["now"] = 0.0
        monkeypatch.setattr(explorer, "monotonic", lambda: 0.0)
        return evidence

    monkeypatch.setattr(gate, "analyze_owner_preview", early_timeout)
    real_validate = registry_gate.validate_pn_owner_adoption

    def in_writer(store, catalog, db, **kwargs):
        assert db.in_transaction and owner._core.event_store._lock._is_owned()
        observed["writer_calls"] += 1
        return real_validate(store, catalog, db, **kwargs)

    monkeypatch.setattr(registry_gate, "validate_pn_owner_adoption", in_writer)
    if branch == "strict-writer":
        with pytest.raises(RegistryConflict, match="PN evidence"):
            apply_replacement(owner, plan, command_id="pn:weighted:edit")
        after = current(owner)[2]
        assert after.net_ref == before.net_ref and after.checkpoint_ref == before.checkpoint_ref
        assert after.epoch == before.epoch and after.next_token_id == before.next_token_id
        assert after.attempts == before.attempts
        assert tuple(t.state for t in after.tokens) == tuple(t.state for t in before.tokens)
        assert len(owner._core.event_store.object_rows_by_type("petri_token/v1")) == token_count
        assert len(events(owner, "net_adopted/v1")) == adoption_count
    else:
        assert apply_replacement(owner, plan, command_id="pn:weighted:edit")["status"] == "ADOPTED"
        assert len(current(owner)[2].tokens) == 30
    expected = 0 if branch == "advisory-untouched" else 1
    assert observed == {"analysis_candidates": expected, "writer_candidates": expected, "writer_calls": 1}
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)
    request.node.user_properties.append(("bounded_writer_branch", json.dumps(observed, sort_keys=True)))
