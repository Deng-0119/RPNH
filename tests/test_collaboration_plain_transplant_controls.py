"""Exact selection, conservative gaps, immutable recovery and proof negatives."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import uuid

import pytest

import test_collaboration_plain_merge as support
from test_collaboration_plain_transplant import fixture, produce_inputs, request, command
from test_collaboration_plain_merge_composition import assembly_request, assert_author_only
from cpn.rpnh.collaboration import (
    AssemblyAuthorV2, PlainModuleMergeAuthor, PlainModuleTransplantAnalyzer, PlainModuleTransplantAuthor,
    SourceQualifiedResourceRef, SourceQualifiedVersionRef, UnresolvedPlainTransplant,
    plain_transplant_schema_data, plain_transplant_assembly_schema_data, plain_merge_result_schema_data,
    graph_assembly_schema_data, validate_closed_revision, validate_assembly_revision,
    read_plain_transplant_analysis, current_branch,
)
from cpn.rpnh.collaboration import plain_transplant as analysis_impl, plain_transplant_result as result_impl
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict, StaleWriterError
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.transaction import RegistryTransaction


def choices(analysis, side):
    return [{"conflict_id": row["conflict_id"], "choice": side, "reason": "Explicit test caller " + side,
             "delete_element_ids": []} for row in analysis.document["conflicts"]]


def test_exact_selection_validation_and_prior_catalogs(fixture):
    inputs, analyzer, _ = fixture
    core = inputs[0]
    left, right, subject = produce_inputs(inputs)
    other = inputs[6]["/components/a/operations/run"] + "/value"
    args = request(inputs, left, right, subject)
    before = support.counts(core)
    for selection in ([], [subject, subject], sorted([subject, other], reverse=True), ["unknown"], [inputs[6]["/"] + "/name"]):
        with pytest.raises(ValueError): analyzer.analyze(**{**args, "selected_subjects": selection})
    with pytest.raises(TypeError): analyzer.analyze(**{k: v for k, v in args.items() if k != "selected_subjects"})
    with pytest.raises(ValueError): analyzer.analyze(**{**args, "base_ref": left.revision.revision_ref})
    with pytest.raises(ValueError): analyzer.analyze(**{**args, "incoming_ref": SourceQualifiedVersionRef("foreign", right.revision.revision_ref.ref)})
    assert support.counts(core) == before
    for new, old in ((plain_transplant_schema_data(), plain_merge_result_schema_data()),
                     (plain_transplant_assembly_schema_data(), graph_assembly_schema_data())):
        assert all(canonical_json(new[0][k]) == canonical_json(v) for k, v in old[0].items())
        assert all(item in new[1] for item in old[1])


def test_explicit_divergent_dispositions_and_no_legacy_eligibility(fixture):
    inputs, analyzer, author = fixture
    core, _, _, ordinary, merge_analyzer, base, ids = inputs
    docs = [deepcopy(base.module.to_dict()) for _ in range(2)]
    for doc, value in zip(docs, ("local", "donor")):
        doc["components"][1]["operations"][0]["config"] = {"value": value}
    left, right = [support.publish(inputs, doc, name) for doc, name in zip(docs, ("left", "right"))]
    subject = ids["/components/b/operations/run"] + "/value"
    analysis = analyzer.analyze(**request(inputs, left, right, subject))
    assert analysis.document["conflicts"]
    for side, disposition in (("local", "kept_local"), ("base", "selected_base"), ("incoming", "imported")):
        value = author.publish(analysis_ref=analysis.analysis_ref, choices=choices(analysis, side), command_id="choice:" + side)
        assert command(core, value)["dispositions"][0]["disposition"] == disposition
        assert len(value.revision.selected_change_refs) == int(side == "incoming")
    before = support.counts(core)
    with pytest.raises(RegistryConflict, match="transplant"):
        ordinary.publish(module=value.module, element_ids=ids, command_id="unsupported-edit", parent_ref=value.revision.revision_ref)
    with pytest.raises(RegistryConflict, match="transplant"):
        merge_analyzer.analyze(local_ref=value.revision.revision_ref, incoming_ref=right.revision.revision_ref, command_id="unsupported-merge")
    assert support.counts(core) == before
    # A sibling already carrying the donor state still has B as its actual base.
    twin = support.publish(inputs, docs[1], "twin")
    equal = analyzer.analyze(**request(inputs, twin, right, subject, "equal-delta"))
    already = author.publish(analysis_ref=equal.analysis_ref, choices=[], command_id="already-local-result")
    assert command(core, already)["dispositions"][0]["disposition"] == "already_local"
    assert not already.revision.selected_change_refs


def test_donor_coupling_must_be_selected_or_explicitly_declined(fixture):
    inputs, analyzer, author = fixture
    core, _, _, _, _, base, ids = inputs
    doc = base.module.to_dict()
    doc["components"][1]["key"] = "test/multiple/v1"
    op = deepcopy(doc["components"][1]["operations"][0]); op["name"] = "again"
    doc["components"][1]["operations"].append(op)
    selected_ids = {**ids, "/components/b/operations/again": "element:" + "e" * 32}
    joint = support.publish(inputs, doc, "joint", selected_ids)
    donor_doc = deepcopy(doc)
    donor_doc["components"][1]["operations"][0]["config"] = {"feedback": 2}
    donor_doc["components"][1]["operations"][1]["config"] = {"expected_feedback": 2}
    donor = support.publish(inputs, donor_doc, "donor", selected_ids, joint.revision.revision_ref)
    subject, omitted = (selected_ids[p] + "/value" for p in ("/components/b/operations/run", "/components/b/operations/again"))
    args = {"local_ref": joint.revision.revision_ref, "incoming_ref": donor.revision.revision_ref,
            "base_ref": joint.revision.revision_ref, "selected_subjects": [subject], "command_id": "coupled"}
    analysis = analyzer.analyze(**args)
    gap = next(row for row in analysis.document["selection_gaps"] if row["reason"] == "donor_coupled_contract")
    assert gap["selected_subjects"] == [subject] and gap["required_subjects"] == [omitted]
    before = support.counts(core)
    with pytest.raises(UnresolvedPlainTransplant) as error:
        author.publish(analysis_ref=analysis.analysis_ref, choices=choices(analysis, "incoming"), command_id="incomplete-import")
    assert {subject, omitted} <= set(error.value.subjects)
    assert support.counts(core) == before
    declined = author.publish(analysis_ref=analysis.analysis_ref, choices=choices(analysis, "local"), command_id="declined")
    assert declined.module.to_dict() == joint.module.to_dict() and not declined.revision.selected_change_refs
    complete = analyzer.analyze(**{**args, "selected_subjects": sorted([subject, omitted]), "command_id": "complete-selection"})
    assert complete.document["selection_gaps"] == []
    imported = author.publish(analysis_ref=complete.analysis_ref, choices=[], command_id="complete-import")
    assert imported.module.to_dict() == donor.module.to_dict() and len(imported.revision.selected_change_refs) == 2


def test_structural_selection_gap_names_exact_missing_subjects(fixture):
    inputs, analyzer, author = fixture
    core, _, _, _, _, base, ids = inputs
    doc = base.module.to_dict(); doc["components"].pop(); doc["entry"].pop("second")
    reduced_ids = {p: i for p, i in ids.items() if not p.startswith("/components/b") and p != "/entry/second"}
    donor = support.publish(inputs, doc, "deleted-region", reduced_ids)
    selected = ids["/components/b"] + "/identity"
    analysis = analyzer.analyze(**request(inputs, base, donor, selected, "incomplete-deletion"))
    assert analysis.document["selection_gaps"]
    missing = {key for row in analysis.document["selection_gaps"] for key in row["required_subjects"]}
    assert ids["/components/b"] + "/identity" in missing
    before = support.counts(core)
    with pytest.raises((UnresolvedPlainTransplant, ValueError)):
        author.publish(analysis_ref=analysis.analysis_ref, choices=choices(analysis, "incoming"), command_id="bad-deletion")
    assert support.counts(core) == before
    # Explicit selection of the complete donor deletion must not silently prune
    # an unselected order atom. Including every changed donor subject is allowed.
    from cpn.rpnh.collaboration._plain_merge_model import normalize, _same, _state
    b, r = normalize(base)["atoms"], normalize(donor)["atoms"]
    subjects = sorted(k for k in b.keys() | r.keys() if not _same(_state(b, k), _state(r, k)))
    full = analyzer.analyze(**{**request(inputs, base, donor, selected, "complete-deletion"), "selected_subjects": subjects})
    result = author.publish(analysis_ref=full.analysis_ref, choices=choices(full, "incoming"), command_id="donor-deletion")
    assert result.module.to_dict() == donor.module.to_dict()
    assert all(row["disposition"] == "imported" for row in command(core, result)["dispositions"])
    assert len(result.revision.selected_change_refs) == len(subjects)


def test_canonical_false_provenance_and_missing_selection_fail_before_revision(fixture, monkeypatch):
    inputs, analyzer, author = fixture
    core = inputs[0]
    left, right, subject = produce_inputs(inputs)
    analysis = analyzer.analyze(**request(inputs, left, right, subject))
    original = result_impl._prepare_at
    for damage in ("subject", "donor", "base", "missing_ref", "marker"):
        before_revisions = len(core.event_store.object_rows_by_type("collaboration_net_revision/v1"))
        observed = []
        def forge(*args, **kwargs):
            values = list(original(*args, **kwargs))
            record, revision = values[:2]
            monkeypatch.setattr(result_impl, "_prepare_at", original)
            spec = next(row for row in record["prepared_materials"] if row["schema"] == result_impl.SELECTED_SCHEMA)
            if damage == "subject": spec["document"]["subject"] = inputs[6]["/components/a/operations/run"] + "/value"
            elif damage == "donor": spec["document"]["incoming_revision_ref"] = left.revision.revision_ref.to_dict()
            elif damage == "base": spec["document"]["base_revision_ref"] = left.revision.revision_ref.to_dict()
            elif damage == "missing_ref":
                record["selected_change_refs"] = []
                values[1] = replace(revision, selected_change_refs=())
            else:
                spec = next(row for row in record["prepared_materials"] if row["role"] == "definition")
                spec["metadata"]["descriptors"] = {"closed_author_command_v1": "canonical forged legacy marker"}
            spec["sha256"] = hashlib.sha256(canonical_json(spec["document"])).hexdigest()
            reference = SourceQualifiedResourceRef.from_dict(spec["resource_ref"], catalog=core.catalog)
            spec["metadata"] = result_impl._metadata(core, author.binding, reference, spec["schema"],
                record["schema_authorities"][spec["schema"]], spec["document"], spec["metadata"]["summary"], spec["metadata"]["descriptors"])
            core.catalog.validate_schema_ref(spec["schema"], spec["document"])
            core.catalog.validate_schema_ref(result_impl.RESULT_COMMAND_SCHEMA, record)
            observed.append(spec["resource_ref"])
            return tuple(values)
        monkeypatch.setattr(result_impl, "_prepare_at", forge)
        with pytest.raises(RegistryConflict):
            author.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="canonical-fake:" + damage)
        assert observed and len(core.event_store.object_rows_by_type("collaboration_net_revision/v1")) == before_revisions
        assert core.event_store.object_row(SourceQualifiedResourceRef.from_dict(observed[0], catalog=core.catalog).ref.resource_version_id) is not None
    assert_author_only(core)


def test_analysis_reconstructs_canonical_fake_and_complete_command_prefix(fixture, monkeypatch):
    inputs, analyzer, _ = fixture
    core = inputs[0]
    left, right, subject = produce_inputs(inputs)
    args = request(inputs, left, right, subject)
    original = analysis_impl._prepare_at
    def forge(*a, **kw):
        doc, record = original(*a, **kw)
        monkeypatch.setattr(analysis_impl, "_prepare_at", original)
        doc["normalized"]["incoming"]["atoms"][subject]["config"] = {"value": "forged canonical donor state"}
        core.catalog.validate_schema_ref(analysis_impl.ANALYSIS_SCHEMA, doc)
        core.catalog.validate_schema_ref(analysis_impl.COMMAND_SCHEMA, record)
        return doc, record
    monkeypatch.setattr(analysis_impl, "_prepare_at", forge)
    with pytest.raises(RegistryConflict, match="reconstruction"):
        analyzer.analyze(**args)
    original_publish = analysis_impl._publish_private_system
    args = {**args, "command_id": "analysis-prefix"}
    def interrupt(core, owner, resource):
        ref = original_publish(core, owner, resource)
        if resource.content_schema_ref == analysis_impl.COMMAND_SCHEMA: raise RuntimeError("durable analysis command")
        return ref
    monkeypatch.setattr(analysis_impl, "_publish_private_system", interrupt)
    with pytest.raises(RuntimeError, match="durable analysis command"): analyzer.analyze(**args)
    monkeypatch.setattr(analysis_impl, "_publish_private_system", original_publish)
    before = support.counts(core)
    other = inputs[6]["/components/a/operations/run"] + "/value"
    with pytest.raises(RegistryConflict): analyzer.analyze(**{**args, "selected_subjects": [other]})
    assert support.counts(core) == before
    exact = analyzer.analyze(**args)
    assert exact.document["request"]["selected_subjects"] == [subject]
    assert read_plain_transplant_analysis(core, exact.analysis_ref, support.registration()).document == exact.document


def test_actual_bytes_propagate_rejection_through_assembly_and_restore(fixture):
    inputs, analyzer, author = fixture
    core, gateway, selected = inputs[:3]
    left, right, subject = produce_inputs(inputs)
    analysis = analyzer.analyze(**request(inputs, left, right, subject))
    result = author.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="byte-result")
    assembly = AssemblyAuthorV2(gateway, selected, analyzer.producer).publish(**assembly_request(result, command_id="byte-assembly"))
    refs = [result.command_ref, *result.revision.selected_change_refs, analysis.analysis_ref, analysis.command_ref,
            result.revision.definition_ref, result.revision.element_mapping_ref,
            result.revision.boundary_mapping_ref, result.revision.host_requirements_ref]
    before = support.counts(core)
    for reference in refs:
        path = core.object_store.path_for_version(reference.ref.resource_version_id)
        original = path.read_bytes(); doc = json.loads(original)
        changed = json.dumps(dict(reversed(list(doc.items()))), ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
        assert changed != original and len(changed) == len(original) and canonical_json(json.loads(changed)) == canonical_json(doc)
        try:
            path.write_bytes(changed)
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                validate_closed_revision(core, result.revision.revision_ref, support.registration())
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                validate_assembly_revision(core, assembly.revision.revision_ref, support.registration())
        finally: path.write_bytes(original)
        assert support.counts(core) == before
    assert validate_assembly_revision(core, assembly.revision.revision_ref, support.registration()).revision == assembly.revision


@pytest.mark.parametrize("cut", ["command", "selected_change", "revision"])
def test_committed_prefix_reopens_exact_selection_and_stale_writer(fixture, monkeypatch, cut):
    inputs, analyzer, author = fixture
    core, gateway = inputs[:2]
    left, right, subject = produce_inputs(inputs)
    analysis = analyzer.analyze(**request(inputs, left, right, subject))
    other = inputs[6]["/components/a/operations/run"] + "/value"
    alternate = analyzer.analyze(**request(inputs, left, right, other, "alternate-analysis"))
    args = {"analysis_ref": analysis.analysis_ref, "choices": [], "command_id": "recover"}
    key = result_impl._key(args["command_id"])
    original = RegistryTransaction.commit
    committed = []
    def interrupt(transaction):
        ref = original(transaction)
        if transaction.event_store is core.event_store and transaction.idempotency_key.startswith(key):
            role = transaction.idempotency_key.removeprefix(key).removeprefix(":") or "revision"
            committed.append(role)
            if role == cut or cut == "selected_change" and role.startswith("selected_change:"):
                assert transaction._closed
                raise RuntimeError("durable cut")
        return ref
    monkeypatch.setattr(RegistryTransaction, "commit", interrupt)
    with pytest.raises(RuntimeError, match="durable cut"): author.publish(**args)
    monkeypatch.setattr(RegistryTransaction, "commit", original)
    assert committed and committed[0] == "command"
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    new_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    replay = PlainModuleTransplantAuthor(new_gateway, support.registration(), analyzer.producer)
    frozen = support.counts(reopened)
    with pytest.raises(StaleWriterError): author.publish(**args)
    with pytest.raises(RegistryConflict): replay.publish(**{**args, "analysis_ref": alternate.analysis_ref})
    assert support.counts(reopened) == frozen
    result = replay.publish(**args)
    assert result.revision.parent_revision_refs == (left.revision.revision_ref,)
    assert command(reopened, result)["selected_subjects"] == [subject]
    assert len(result.revision.selected_change_refs) == 1
    reader = _RegistryCore(reopened.run_dir, create=False, read_only=True, catalog=reopened.catalog)
    assert validate_closed_revision(reader, result.revision.revision_ref, support.registration()).revision == result.revision
    before = support.counts(reopened)
    assert replay.publish(**args).revision == result.revision and support.counts(reopened) == before
    assert_author_only(reopened)


def test_real_merge_ancestry_and_each_branch_stale_axis(fixture):
    inputs, analyzer, author = fixture
    core, gateway, selected, _, merge_analyzer, base, ids = inputs
    docs = [deepcopy(base.module.to_dict()) for _ in range(2)]
    docs[0]["name"] = "MergedBase"
    docs[1]["components"][1]["operations"][0]["config"] = {"value": "historical merge"}
    a, b = [support.publish(inputs, doc, name) for doc, name in zip(docs, ("merge-left", "merge-right"))]
    analysis = merge_analyzer.analyze(**support.request(inputs, a, b, command_id="real-merge-analysis"))
    merged = PlainModuleMergeAuthor(gateway, selected, analyzer.producer).publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="real-M")
    local_doc, donor_doc = deepcopy(merged.module.to_dict()), deepcopy(merged.module.to_dict())
    local_doc["name"] = "LocalAfterM"
    donor_doc["components"][1]["operations"][0]["config"] = {"value": "selected after merge"}
    left = support.publish(inputs, local_doc, "after-M-left", parent=merged.revision.revision_ref)
    right = support.publish(inputs, donor_doc, "after-M-right", parent=merged.revision.revision_ref)
    subject = ids["/components/b/operations/run"] + "/value"
    selected_analysis = analyzer.analyze(local_ref=left.revision.revision_ref, incoming_ref=right.revision.revision_ref,
        base_ref=merged.revision.revision_ref, selected_subjects=[subject], command_id="from-real-M")
    assert selected_analysis.document["base_revision_ref"] == merged.revision.revision_ref.to_dict()
    result = author.publish(analysis_ref=selected_analysis.analysis_ref, choices=[], command_id="after-M-T")
    root = gateway.create_author_branch(head_revision_ref=merged.revision.revision_ref, command_id="branch-M")
    branch = gateway.advance_author_branch(expected_branch_version_ref=root.branch_ref,
        expected_head_revision_ref=merged.revision.revision_ref, expected_stream_head=root.sequence,
        next_revision_ref=left.revision.revision_ref, command_id="branch-L")
    good = dict(expected_branch_version_ref=branch.branch_ref, expected_head_revision_ref=left.revision.revision_ref,
                expected_stream_head=branch.sequence, next_revision_ref=result.revision.revision_ref)
    for field, value in (("expected_branch_version_ref", root.branch_ref),
                         ("expected_head_revision_ref", right.revision.revision_ref),
                         ("expected_stream_head", branch.sequence + 1)):
        before = support.counts(core)
        with pytest.raises(RegistryConflict): gateway.advance_author_branch(**{**good, field: value}, command_id="stale:" + field)
        assert support.counts(core) == before
        assert current_branch(core, branch.branch_ref.ref.entity_id) == branch
    next_branch = gateway.advance_author_branch(**good, command_id="advance-T")
    assert current_branch(core, branch.branch_ref.ref.entity_id) == next_branch
    assert validate_closed_revision(core, result.revision.revision_ref, support.registration()).module.name == "LocalAfterM"


def test_partial_donor_terminal_addition_reports_exact_order_and_can_decline(fixture):
    inputs, analyzer, author = fixture
    core, _, _, _, _, base, ids = inputs
    doc = base.module.to_dict()
    alternative = deepcopy(doc["terminal"])
    doc["terminal_alternatives"] = [alternative]
    extra = "element:" + "d" * 32
    donor = support.publish(inputs, doc, "donor-terminal", {**ids, "/terminal_alternatives/0": extra})
    subjects = [extra + suffix for suffix in ("/identity", "/name", "/value")]
    args = {"local_ref": base.revision.revision_ref, "incoming_ref": donor.revision.revision_ref,
            "base_ref": base.revision.revision_ref, "selected_subjects": subjects, "command_id": "partial-terminal"}
    analysis = analyzer.analyze(**args)
    key = ids["/"] + "/terminal_alternatives_order"
    gap = next(row for row in analysis.document["selection_gaps"] if row["reason"] == "terminal_order_primary")
    assert gap["required_subjects"] == [key]
    before = support.counts(core)
    with pytest.raises(UnresolvedPlainTransplant) as error:
        author.publish(analysis_ref=analysis.analysis_ref, choices=choices(analysis, "incoming"), command_id="bad-terminal")
    assert key in error.value.subjects and support.counts(core) == before
    declined = author.publish(analysis_ref=analysis.analysis_ref, choices=choices(analysis, "local"), command_id="decline-terminal")
    assert declined.module.to_dict() == base.module.to_dict()
    complete = analyzer.analyze(**{**args, "selected_subjects": sorted([*subjects, key]), "command_id": "complete-terminal"})
    result = author.publish(analysis_ref=complete.analysis_ref, choices=choices(complete, "incoming"), command_id="terminal-result")
    assert result.module.to_dict() == donor.module.to_dict()


def test_explicit_synthetic_delete_has_no_imported_change_refs(fixture):
    from cpn.rpnh.collaboration.materials import _elements
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.collaboration._plain_merge_model import normalize, _same, _state
    inputs, analyzer, author = fixture
    core, _, _, _, _, base, ids = inputs
    doc = base.module.to_dict()
    extra = deepcopy(doc["components"][1]); extra["name"] = "new"
    extra["ports"] = extra["ports"][1:]; extra["operations"][0]["inputs"] = []
    doc["components"].append(extra)
    selected_ids = {p: ids.get(p, "element:" + uuid.uuid5(uuid.NAMESPACE_URL, p).hex)
                    for p in _elements(ModuleDeclaration.from_dict(doc))}
    local_doc, donor_doc = deepcopy(doc), deepcopy(doc)
    local_doc["components"][2]["operations"][0]["config"] = {"value": "local"}
    donor_doc["components"][2]["operations"][0]["config"] = {"value": "donor"}
    left = support.publish(inputs, local_doc, "synthetic-left", selected_ids)
    right = support.publish(inputs, donor_doc, "synthetic-right", selected_ids)
    b, r = normalize(base)["atoms"], normalize(right)["atoms"]
    subjects = sorted(k for k in b.keys() | r.keys() if not _same(_state(b, k), _state(r, k)))
    analysis = analyzer.analyze(**{**request(inputs, left, right, subjects[0], "synthetic-analysis"), "selected_subjects": subjects})
    decisions = [{"conflict_id": row["conflict_id"], "choice": "delete", "reason": "Caller explicitly discards the concurrent new component",
                  "delete_element_ids": [selected_ids["/components/new"]]} for row in analysis.document["conflicts"]]
    result = author.publish(analysis_ref=analysis.analysis_ref, choices=decisions, command_id="synthetic-delete")
    assert result.module.to_dict() == base.module.to_dict()
    assert result.revision.selected_change_refs == ()
    dispositions = command(core, result)["dispositions"]
    assert any(row["disposition"] == "deleted_by_choice" for row in dispositions)
    assert all(row["disposition"] in {"deleted_by_choice", "selected_base"} for row in dispositions)


def test_type_sensitive_donor_delta_and_same_analysis_command_mismatch(fixture):
    inputs, analyzer, author = fixture
    core, _, _, _, _, base, ids = inputs
    base_doc = base.module.to_dict(); base_doc["components"][1]["operations"][0]["config"] = {"value": True}
    typed_base = support.publish(inputs, base_doc, "typed-base")
    local_doc = deepcopy(base_doc); local_doc["name"] = "TypedLocal"
    left = support.publish(inputs, local_doc, "typed-left", parent=typed_base.revision.revision_ref)
    heads = []
    for label, number in (("int", 1), ("float", 1.0)):
        doc = deepcopy(base_doc); doc["components"][1]["operations"][0]["config"] = {"value": number}
        heads.append(support.publish(inputs, doc, "typed-" + label, parent=typed_base.revision.revision_ref))
    subject = ids["/components/b/operations/run"] + "/value"
    args = {"local_ref": left.revision.revision_ref, "incoming_ref": heads[0].revision.revision_ref,
            "base_ref": typed_base.revision.revision_ref, "selected_subjects": [subject], "command_id": "type-exact-analysis"}
    analysis = analyzer.analyze(**args)
    before = support.counts(core)
    with pytest.raises(RegistryConflict): analyzer.analyze(**{**args, "incoming_ref": heads[1].revision.revision_ref})
    assert support.counts(core) == before
    result = author.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="type-exact-result")
    assert type(result.module.components[1].operations[0].config["value"]) is int
    assert command(core, result)["dispositions"][0]["disposition"] == "imported"


def test_first_command_locks_actual_equal_content_schema_authority(fixture, monkeypatch):
    from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
    inputs, analyzer, author = fixture
    core, gateway = inputs[:2]
    left, right, subject = produce_inputs(inputs)
    analysis = analyzer.analyze(**request(inputs, left, right, subject))
    schema = result_impl.SELECTED_SCHEMA
    original_authority = author.schemas[schema]
    path = core.object_store.path_for_version(original_authority.resource_version_id)
    original_bytes = path.read_bytes()
    alternative = result_impl._publish_private_system(core, gateway._task_ref, PublishResource(
        origin=PrivateSystemOrigin(gateway._bootstrap_ref), payload=original_bytes,
        media_type="application/schema+json", content_schema_ref="registry_v1/registry_type_catalog/v1",
        summary="Equal bytes alternate selected-change schema authority", lifetime_ref=gateway._bootstrap_ref,
        descriptors={"host_registration_kind": "schema", "registered_key": schema}, idempotency_key="alternate-selected-schema"))
    assert alternative != original_authority
    publish = result_impl._publish_private_system
    def cut(core, owner, resource):
        ref = publish(core, owner, resource)
        if resource.content_schema_ref == result_impl.RESULT_COMMAND_SCHEMA: raise RuntimeError("schema command cut")
        return ref
    monkeypatch.setattr(result_impl, "_publish_private_system", cut)
    args = {"analysis_ref": analysis.analysis_ref, "choices": [], "command_id": "authority-result"}
    with pytest.raises(RuntimeError, match="schema command cut"): author.publish(**args)
    monkeypatch.setattr(result_impl, "_publish_private_system", publish)
    before = support.counts(core)
    try:
        author.schemas[schema] = alternative
        with pytest.raises(RegistryConflict): author.publish(**args)
        assert support.counts(core) == before
    finally: author.schemas[schema] = original_authority
    result = author.publish(**args)
    record = command(core, result)
    ref = result.revision.selected_change_refs[0]
    selected_spec = next(row for row in record["prepared_materials"] if row["resource_ref"] == ref.to_dict())
    assert selected_spec["metadata"]["content_schema_authority_ref"] == record["schema_authorities"][schema]
    assert record["schema_authorities"][schema]["resource_version_id"] == str(original_authority.resource_version_id)
    changed = json.dumps(dict(reversed(list(json.loads(original_bytes).items()))), ensure_ascii=True, separators=(",", ":")).encode()
    assert changed != original_bytes and len(changed) == len(original_bytes)
    before = support.counts(core)
    try:
        path.write_bytes(changed)
        with pytest.raises((RegistryConflict, ObjectIntegrityError)): validate_closed_revision(core, result.revision.revision_ref, support.registration())
        assert support.counts(core) == before
    finally: path.write_bytes(original_bytes)
    assert validate_closed_revision(core, result.revision.revision_ref, support.registration()).revision == result.revision
