"""Actual Registry preview/adoption boundaries; no executor or model requests."""
from dataclasses import replace

import pytest

from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.owner_mapping import allocate_owner_mapping
from test_terminal_owner_adoption import make_owner, adopt, current, events


def test_owner_preview_no_writes_and_atomic_exact_allocation(tmp_path, monkeypatch):
    import cpn.rpnh.registry.owner_mapping as mapping
    owner = make_owner(tmp_path)
    real_preview = mapping.preview_owner_mapping
    checked = []

    def inspected(core, publication, marking, decision, **kwargs):
        head = core.event_store.max_ordinal()
        paths = set(core.object_store.root.rglob("*"))
        preview = real_preview(core, publication, marking, decision, **kwargs)
        assert core.event_store.max_ordinal() == head
        assert set(core.object_store.root.rglob("*")) == paths
        assert preview.source_marking == marking
        assert preview.allocation.token_refs == tuple(t.token_ref for t in preview.tokens)
        assert all(not core.event_store.object_row(t.token_ref.version_id) for t in preview.tokens)
        checked.append(preview)
        return preview

    monkeypatch.setattr(mapping, "preview_owner_mapping", inspected)
    result = adopt(owner, "B")
    assert result["status"] == "ADOPTED"
    assert checked
    after = current(owner)[2]
    assert after.next_token_id == checked[0].allocation.next_token_id
    assert tuple(t.state for t in after.tokens) == checked[0].tokens
    for t in after.tokens:
        row = owner._core.event_store.object_row(t.token_ref.version_id)
        assert str(row["transaction_id"]) == result["transaction_id"]
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)


def test_adoption_failure_leaves_no_registered_candidate_tokens(tmp_path, monkeypatch):
    import cpn.rpnh.owner_edits as edits
    owner = make_owner(tmp_path)
    before = current(owner)[2]
    token_count = len(owner._core.event_store.object_rows_by_type("petri_token/v1"))
    adoption_count = len(events(owner, "net_adopted/v1"))
    def fail(*args, **kwargs):
        raise RuntimeError("injected adoption failure after mapping prewrite")
    monkeypatch.setattr(edits, "stage_owner_adoption", fail)
    with pytest.raises(RuntimeError, match="injected adoption failure"):
        adopt(owner, "B")
    assert len(owner._core.event_store.object_rows_by_type("petri_token/v1")) == token_count
    after = current(owner)[2]
    assert after.net_ref == before.net_ref and after.checkpoint_ref == before.checkpoint_ref
    assert after.epoch == before.epoch and after.next_token_id == before.next_token_id
    assert after.attempts == before.attempts
    assert tuple(t.state for t in after.tokens) == tuple(t.state for t in before.tokens)
    assert len(events(owner, "net_adopted/v1")) == adoption_count
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)


def configured_owner(tmp_path, configuration):
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.rpnh.registry.schema_catalog import canonical_json
    from cpn.rpnh.run import OwnerInput, start_run
    from test_static_lease_reads import TEXT, _registration, _simple_module
    document = _simple_module("PNControl").to_dict()
    for port in document["components"][0]["ports"]:
        port["channel"] = "control"
    task = OwnerInput(TEXT, canonical_json("task"), "PN offline owner input")
    return start_run(ModuleDeclaration.from_dict(document), _registration(),
        run_dir=tmp_path / "run", task_input=task, entry_inputs={"request": task},
        budgets=ModuleBudgetDeclaration(tuple(document["budget_buckets"]), (TEXT,), 8, 0, 8, 0),
        model_condition="offline PN projection", owner_statement="Finite owner PN policy",
        command_id="pn:fresh", pn_validation=configuration)


def finite_configuration(*, mode="strict", modeled=True):
    from cpn.rpnh.pn_validation import AnalysisPolicy, OperationModel, OperationCase, ProducedSpec, TerminalContract
    from cpn.rpnh.pn_validation.runtime_gate import ValidationConfiguration
    models = (OperationModel("step.run", "v1", (OperationCase("complete", "complete", (ProducedSpec("step.result"),)),)),) if modeled else ()
    return ValidationConfiguration(operation_models=models,
        terminal_contract=TerminalContract(success_places=("step.result",), unfinished_places=("step.request",), stop_on_terminal=True),
        policy=AnalysisPolicy(mode=mode, max_states=8, max_edges=8, max_depth=4,
            properties=("safety", "possible_successful_completion"), required_properties=("safety", "possible_successful_completion")))


def test_initial_strict_missing_model_blocks_before_admission(tmp_path):
    with pytest.raises(RegistryConflict, match="strict initial gate"):
        configured_owner(tmp_path, finite_configuration(mode="strict", modeled=False))
    from cpn.rpnh.registry._registry import _RegistryCore
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True)
    assert not core.event_store.object_rows_by_type("transition_firing/v1")
    assert core.event_store.actual_model_call_counts() == (0, 0)


def test_advisory_unknown_is_recorded_and_legal_owner_edit_has_bound_evidence(tmp_path):
    owner = configured_owner(tmp_path, finite_configuration(mode="advisory", modeled=False))
    assert {item["verdict"] for item in owner.pn_validation_report["properties"]} == {"UNKNOWN"}
    result = adopt(owner, "PNControlB")
    assert result["status"] == "ADOPTED"
    rows = owner._core.event_store.object_rows_by_type("resource_version/v1")
    import json
    adopted = [r for r in rows if str(r["transaction_id"]) == result["transaction_id"]
        and json.loads(r["metadata_json"])["descriptors"].get("owner_control_kind") == "result"]
    assert len(adopted) == 1
    body = json.loads(owner._core.object_store.read_registered(owner._core.get_version(adopted[0]["version_id"])))
    assert {item["verdict"] for item in body["data"]["pn_validation"]["report"]["properties"]} == {"UNKNOWN"}
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)


def test_strict_legal_initial_and_owner_mapping_pass_actual_registry_gate(tmp_path):
    owner = configured_owner(tmp_path, finite_configuration())
    assert all(item["verdict"] == "HOLDS" for item in owner.pn_validation_report["properties"])
    assert adopt(owner, "PNControlB")["status"] == "ADOPTED"
    assert all(item["verdict"] == "HOLDS" for item in owner.pn_validation_report["properties"])
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)


@pytest.mark.parametrize("field", ["checkpoint", "net", "contract", "token"])
def test_changed_pn_report_rejected_atomically(tmp_path, monkeypatch, field):
    from copy import deepcopy
    import json
    import cpn.rpnh.pn_validation.runtime_gate as gate
    owner = configured_owner(tmp_path, finite_configuration())
    before = current(owner)[2]
    count = len(owner._core.event_store.object_rows_by_type("petri_token/v1"))
    real = gate.analyze_owner_preview
    def changed(owner, preview):
        evidence = deepcopy(real(owner, preview))
        material = evidence["report"]["input"]
        if field == "contract":
            material["terminal_contract"]["revision"] = "changed"
        elif field == "token":
            material["state"]["base"]["tokens"][0]["consumer"] = "wrong-consumer"
        else:
            context = json.loads(material["source_context_json"])
            target = context["registry_binding"]["checkpoint"] if field == "checkpoint" else context["registry_binding"]["candidate_net"]
            target["epoch" if field == "checkpoint" else "net_instance_version_id"] = -1 if field == "checkpoint" else "changed"
            material["source_context_json"] = json.dumps(context, sort_keys=True)
        return evidence
    monkeypatch.setattr(gate, "analyze_owner_preview", changed)
    with pytest.raises(RegistryConflict, match="PN evidence"):
        adopt(owner, "PNControlB")
    after = current(owner)[2]
    assert after.net_ref == before.net_ref and after.checkpoint_ref == before.checkpoint_ref
    assert tuple(t.state for t in after.tokens) == tuple(t.state for t in before.tokens)
    assert len(owner._core.event_store.object_rows_by_type("petri_token/v1")) == count


def test_strict_configuration_cannot_be_bypassed_by_omitting_edit_evidence(tmp_path, monkeypatch):
    import cpn.rpnh.pn_validation.runtime_gate as gate
    owner = configured_owner(tmp_path, finite_configuration())
    before = current(owner)[2]
    count = len(owner._core.event_store.object_rows_by_type("petri_token/v1"))
    monkeypatch.setattr(gate, "analyze_owner_preview", lambda *args: None)
    with pytest.raises(RegistryConflict, match="PN adoption requires"):
        adopt(owner, "PNControlB")
    assert current(owner)[2].checkpoint_ref == before.checkpoint_ref
    assert len(owner._core.event_store.object_rows_by_type("petri_token/v1")) == count


def test_exact_preview_token_substitution_rejected_before_registration(tmp_path, monkeypatch):
    import cpn.rpnh.registry.owner_mapping as mapping
    from cpn.rpnh.registry.models import VersionRef
    from cpn.rpnh.registry.identities import new_id
    owner = make_owner(tmp_path)
    count = len(owner._core.event_store.object_rows_by_type("petri_token/v1"))
    before = current(owner)[2]
    real = mapping.preview_owner_mapping
    def changed(*args, **kwargs):
        preview = real(*args, **kwargs)
        if "_token_refs" in kwargs:
            return preview
        substitute = VersionRef("petri_token/v1", new_id("petri_token"), new_id("petri_token_version"))
        return replace(preview, tokens=(replace(preview.tokens[0], token_ref=substitute), *preview.tokens[1:]))
    monkeypatch.setattr(mapping, "preview_owner_mapping", changed)
    with pytest.raises(RegistryConflict, match="exact current transformation"):
        adopt(owner, "B")
    assert current(owner)[2].checkpoint_ref == before.checkpoint_ref
    assert len(owner._core.event_store.object_rows_by_type("petri_token/v1")) == count


def test_strict_inevitable_completion_rejects_unsafe_alternate_outcome(tmp_path):
    from cpn.components.basic import CONFIG_SCHEMA_ID, lower_operation
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.petri_contracts import InitialTokenDeclaration
    from cpn.rpnh.pn_validation import AnalysisPolicy, OperationModel, OperationCase, ProducedSpec, TerminalContract
    from cpn.rpnh.pn_validation.runtime_gate import ValidationConfiguration
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.rpnh.registry.schema_catalog import canonical_json
    from cpn.rpnh.run import OwnerInput, start_run
    from test_static_lease_reads import TEXT, _registration, _simple_module
    registration = _registration()
    def lower(config, context):
        fragment = lower_operation(config, context)
        return replace(fragment, places=tuple(replace(place, capacity=1,
            initial_tokens=(InitialTokenDeclaration(),)) if place.name == "blocked" else place
            for place in fragment.places))
    registration.register_component("finite_capacity", lower,
        identity={"implementation_id": "test.finite_capacity", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    document = _simple_module("UnsafeAlternate").to_dict()
    component = document["components"][0]
    component["key"] = "finite_capacity"
    for port in component["ports"]:
        port["channel"] = "control"
    component["ports"].append({"name": "blocked", "direction": "output", "schema": TEXT, "channel": "control"})
    operation = component["operations"][0]
    operation["outputs"].append("blocked")
    operation["outcomes"].append({"name": "overflow", "products": [{"port": "blocked"}]})
    configuration = ValidationConfiguration(
        operation_models=(OperationModel("step.run", "v1", (
            OperationCase("complete", "complete", (ProducedSpec("step.result"),)),
            OperationCase("overflow", "overflow", (ProducedSpec("step.blocked"),)))),),
        terminal_contract=TerminalContract(success_places=("step.result",),
            unfinished_places=("step.request",), persistent_places=("step.blocked",), stop_on_terminal=True),
        policy=AnalysisPolicy(mode="strict", max_states=8, max_edges=8, max_depth=4,
            properties=("possible_successful_completion", "inevitable_completion_without_fairness"),
            required_properties=("inevitable_completion_without_fairness",)))
    task = OwnerInput(TEXT, canonical_json("task"), "PN offline input")
    with pytest.raises(RegistryConflict, match="strict initial gate"):
        start_run(ModuleDeclaration.from_dict(document), registration,
            run_dir=tmp_path / "run", task_input=task, entry_inputs={"request": task},
            budgets=ModuleBudgetDeclaration(tuple(document["budget_buckets"]), (TEXT,), 8, 0, 8, 0),
            model_condition="offline PN projection", owner_statement="Finite completion policy",
            command_id="pn:fresh", pn_validation=configuration)
    from cpn.rpnh.registry._registry import _RegistryCore
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True)
    assert not core.event_store.object_rows_by_type("transition_firing/v1")
    assert core.event_store.actual_model_call_counts() == (0, 0)


@pytest.mark.parametrize("mode", ["advisory", "strict"])
def test_wallclock_cutoff_unknown_obeys_owner_policy(tmp_path, monkeypatch, mode):
    import cpn.rpnh.pn_validation.explorer as explorer
    import cpn.rpnh.pn_validation.runtime_gate as gate
    owner = configured_owner(tmp_path, finite_configuration(mode=mode))
    before = current(owner)[2]
    token_count = len(owner._core.event_store.object_rows_by_type("petri_token/v1"))
    real = gate.analyze_owner_preview
    def cutoff_then_faster_verification(owner, preview):
        calls = 0
        def clock():
            nonlocal calls
            calls += 1
            return 0.0 if calls == 1 else 100.0
        monkeypatch.setattr(explorer, "monotonic", clock)
        evidence = real(owner, preview)
        assert all(item["verdict"] == "UNKNOWN" for item in evidence["report"]["properties"])
        # Proof checking must preserve this valid prefix even if a new wallclock
        # would let a full exploration finish on the identical Registry input.
        monkeypatch.setattr(explorer, "monotonic", lambda: 0.0)
        return evidence
    monkeypatch.setattr(gate, "analyze_owner_preview", cutoff_then_faster_verification)
    if mode == "advisory":
        assert adopt(owner, "PNControlB")["status"] == "ADOPTED"
    else:
        with pytest.raises(RegistryConflict, match="strict owner edit"):
            adopt(owner, "PNControlB")
        assert current(owner)[2].checkpoint_ref == before.checkpoint_ref
        assert len(owner._core.event_store.object_rows_by_type("petri_token/v1")) == token_count
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)
