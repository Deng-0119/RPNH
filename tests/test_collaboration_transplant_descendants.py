"""Real selective-transplant histories and explicit ordinary V7 consumers."""
from copy import deepcopy
import json
import uuid

import pytest

import test_collaboration_plain_merge as support
import test_collaboration_plain_transplant as transplant
from test_collaboration_plain_merge_composition import assert_author_only
from cpn.rpnh.collaboration import (
    AssemblyAuthorV7, AssemblyMemberV7, AssemblyCompletion, AssemblyConnection,
    PlainModuleTransplantAnalyzer, PlainModuleTransplantAuthor, PlainTransplantDerivedAuthor,
    ValidatedPlainTransplantDerivedRevision, ValidatedClosedRevision,
    current_branch, read_branch_version, read_assembly_revision,
    validate_closed_revision, validate_assembly_revision,
    plain_transplant_derived_schema_data, plain_transplant_derived_assembly_schema_data,
    open_region_derived_assembly_schema_data,
)
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.schema_catalog import canonical_json


def all_catalogs():
    schemas, types, paths = plain_transplant_derived_assembly_schema_data()
    older, older_types, older_paths = open_region_derived_assembly_schema_data()
    by_type = {item.name: item for item in types}
    for key, document in older.items():
        assert key not in schemas or canonical_json(schemas[key]) == canonical_json(document)
        schemas[key], paths[key] = document, older_paths[key]
    for item in older_types:
        assert item.name not in by_type or by_type[item.name] == item
        by_type[item.name] = item
    return schemas, tuple(by_type.values()), paths


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(support, "plain_merge_schema_data", all_catalogs)
    inputs = support.fixture.__wrapped__(tmp_path, monkeypatch)
    analyzer = PlainModuleTransplantAnalyzer(inputs[1], inputs[2], inputs[4].producer)
    publisher = PlainModuleTransplantAuthor(inputs[1], inputs[2], inputs[4].producer)
    left, right, subject = transplant.produce_inputs(inputs)
    analysis = analyzer.analyze(**transplant.request(inputs, left, right, subject))
    selected = publisher.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="transplant:T")
    author = PlainTransplantDerivedAuthor(inputs[1], inputs[2], inputs[4].producer)
    return inputs, selected, author, left, right, analysis, subject


def ids(value):
    return {row["locator"]: row["element_id"] for row in value.element_map["elements"]}


def edit(value, name):
    module = deepcopy(value.module.to_dict())
    module["name"] = name
    module["components"][1]["operations"][0]["config"] = {"value": name}
    return {"operation": "edit", "authority_mode": "ordinary_new_definition",
        "parent_revision_ref": value.revision.revision_ref.to_dict(), "copy_source_ref": None,
        "module": module, "element_ids": ids(value), "copy_sources": {}}


def copy_root(value):
    copied = {locator: "element:" + uuid.uuid4().hex for locator in ids(value)}
    return {"operation": "copy_root", "authority_mode": "ordinary_new_definition",
        "parent_revision_ref": None, "copy_source_ref": value.revision.revision_ref.to_dict(),
        "module": value.module.to_dict(), "element_ids": copied,
        "copy_sources": {copied[locator]: identity for locator, identity in ids(value).items()}}


def assembly_request(left, right, command_id="assembly:transplant-ordinary"):
    members = [AssemblyMemberV7("member:" + n * 32, name, value.revision.revision_ref,
        "ordinary_new_definition", value.command_ref, value.historical_origins_ref)
        for n, name, value in (("a", "Current definition", left), ("b", "Independent copy", right))]
    return {"name": "TransplantOrdinaryDefinitions", "members": members,
        "connections": [AssemblyConnection(members[0].member_id, ids(left)["/exit/result"],
                                           members[1].member_id, ids(right)["/entry/request"])],
        "completion": AssemblyCompletion(members[1].member_id, ids(right)["/terminal"]),
        "budget_policy": "shared_exact", "deployment_intent": "same_run_candidate", "command_id": command_id}


def test_real_transplant_descendants_copies_branch_and_actual_v7_assembly(fixture, monkeypatch):
    inputs, selected, author, left, right, analysis, subject = fixture
    core, gateway, registration = inputs[:3]
    first_request = edit(selected, "Ordinary_E")
    first = author.publish(request=first_request, command_id="derived:E")
    second = author.publish(request=edit(first, "Ordinary_E2"), command_id="derived:E2")
    copies = [author.publish(request=copy_root(value), command_id="copy:" + name)
              for name, value in (("T", selected), ("E", first))]
    edited_copy = author.publish(request=edit(copies[1], "IndependentEdited"), command_id="copy:edited")
    assert first.revision.parent_revision_refs == (selected.revision.revision_ref,)
    assert second.revision.parent_revision_refs == (first.revision.revision_ref,)
    assert selected.revision.parent_revision_refs == (left.revision.revision_ref,)
    assert first.revision.revision_ref.ref.entity_id == selected.revision.revision_ref.ref.entity_id
    assert first.module.components[1].operations[0].config == {"value": "Ordinary_E"}
    assert first.revision.selected_change_refs == second.revision.selected_change_refs == ()
    assert len(selected.revision.selected_change_refs) == 1
    original = transplant.command(core, selected)
    assert original["selected_subjects"] == [subject]
    assert original["incoming_revision_ref"] == right.revision.revision_ref.to_dict()
    assert original["base_revision_ref"] == inputs[5].revision.revision_ref.to_dict()
    assert original["dispositions"][0]["disposition"] == "imported"
    rows = second.historical_origins["origins"]
    assert {row["revision_ref"]["ref"]["version_id"] for row in rows} == {
        str(selected.revision.revision_ref.ref.version_id), str(first.revision.revision_ref.ref.version_id)}
    origin = next(row for row in rows if row["proof_kind"] == "plain_transplant_v1")
    assert origin["command_ref"] == selected.command_ref.to_dict()
    assert origin["analysis_ref"] == analysis.analysis_ref.to_dict()
    assert origin["resolution_ref"] == selected.resolution_ref.to_dict()
    assert origin["selected_change_refs"] == [ref.to_dict() for ref in selected.revision.selected_change_refs]
    assert all(row["relationship"] == "ancestor" for row in rows)
    for value, original_value in zip(copies, (selected, first), strict=True):
        assert value.revision.parent_revision_refs == () and value.revision.selected_change_refs == ()
        assert value.revision.revision_ref.ref.entity_id != original_value.revision.revision_ref.ref.entity_id
        assert value.module.to_dict() == original_value.module.to_dict()
        assert set(ids(value).values()).isdisjoint(ids(original_value).values())
        assert len(value.historical_origins["copy_origins"]) == len(ids(original_value))
        assert all(row["copied_from"] is None for row in value.element_map["elements"])
        assert all(row["relationship"] == "copied_from" for row in value.historical_origins["origins"])
    assert edited_copy.revision.parent_revision_refs == (copies[1].revision.revision_ref,)
    assert edited_copy.revision.revision_ref.ref.entity_id == copies[1].revision.revision_ref.ref.entity_id
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    for value in (first, second, *copies, edited_copy):
        reread = validate_closed_revision(reader, value.revision.revision_ref, registration)
        assert type(reread) is ValidatedPlainTransplantDerivedRevision
        assert reread.current_transplant is None and reread.derivation == "historical_only"
        assert reread.historical_origins == value.historical_origins
        assert canonical_json(reread.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    branch = gateway.create_author_branch(head_revision_ref=selected.revision.revision_ref, command_id="branch:T")
    at_first = gateway.advance_author_branch(expected_branch_version_ref=branch.branch_ref,
        expected_head_revision_ref=selected.revision.revision_ref, expected_stream_head=branch.sequence,
        next_revision_ref=first.revision.revision_ref, command_id="branch:E")
    valid = {"expected_branch_version_ref": at_first.branch_ref, "expected_head_revision_ref": first.revision.revision_ref,
        "expected_stream_head": at_first.sequence, "next_revision_ref": second.revision.revision_ref}
    before = support.counts(core)
    for key, value in (("expected_branch_version_ref", branch.branch_ref),
                       ("expected_head_revision_ref", selected.revision.revision_ref), ("expected_stream_head", 100)):
        with pytest.raises(RegistryConflict):
            gateway.advance_author_branch(**{**valid, key: value}, command_id="branch:bad:" + key)
        assert support.counts(core) == before
    at_second = gateway.advance_author_branch(**valid, command_id="branch:E2")
    before = support.counts(core)
    assert gateway.advance_author_branch(expected_branch_version_ref=branch.branch_ref,
        expected_head_revision_ref=selected.revision.revision_ref, expected_stream_head=branch.sequence,
        next_revision_ref=first.revision.revision_ref, command_id="branch:E") == at_first
    assert current_branch(core, branch.branch_ref.ref.entity_id) == at_second
    assert support.counts(core) == before
    for step, expected in ((branch, selected), (at_first, first), (at_second, second)):
        historical = read_branch_version(reader, step.branch_ref)
        assert validate_closed_revision(reader, historical.head_revision_ref, registration).revision == expected.revision
    assembly = AssemblyAuthorV7(gateway, registration, author.producer)
    request = assembly_request(second, copies[1])
    value = assembly.publish(**request)
    assert type(value.generated) is ValidatedClosedRevision
    assert value.revision.revision_ref.ref.entity_type == "collaboration_assembly_revision/v7"
    assert len(value.lowering_map["member_proofs"]) == 2
    assert all(row["resolution"]["historical_origin_refs"] for row in value.lowering_map["member_proofs"])
    assert len(value.lowering_map["fragment_origins"]) == 4
    assert {row["element_id"] for row in value.lowering_map["origins"]} == set(ids(second).values()) | set(ids(copies[1]).values())
    original_connect, calls = reader.event_store.connect, []
    def connect(*args, **kwargs):
        calls.append("cut")
        return original_connect(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(reader.event_store, "connect", connect)
        reopened = validate_assembly_revision(reader, value.revision.revision_ref, registration)
    assert calls == ["cut"]
    assert reopened.lowering_map == value.lowering_map
    assert read_assembly_revision(reader, value.revision.revision_ref) == value.revision
    assert type(validate_closed_revision(reader, value.revision.generated_revision_ref, registration)) is ValidatedClosedRevision
    before = support.counts(core)
    assert assembly.publish(**request).revision == value.revision
    assert author.publish(request=first_request, command_id="derived:E").revision == first.revision
    assert current_branch(core, branch.branch_ref.ref.entity_id) == at_second
    assert support.counts(core) == before
    assert_author_only(core)


def test_explicit_request_copy_mapping_and_legacy_family_gates(fixture):
    from dataclasses import replace
    from cpn.rpnh.collaboration import (AssemblyAuthor, AssemblyAuthorV2, AssemblyAuthorV3,
        AssemblyAuthorV4, AssemblyAuthorV5, AssemblyMember, AssemblyMemberV2, AssemblyMemberV3,
        AssemblyMemberV4, AssemblyMemberV5, OpenRegionDerivedAuthor)
    inputs, selected, author, *_ = fixture
    core, gateway, registration, ordinary, merger = inputs[:5]
    request = edit(selected, "ExplicitOrdinary")
    before = support.counts(core)
    invalid = [dict(request, authority_mode=None), dict(request, authority_mode="retain_transplant"),
        {key: value for key, value in request.items() if key != "authority_mode"},
        dict(request, element_ids={}), {key: value for key, value in request.items() if key != "copy_sources"},
        dict(request, parent_revision_ref=inputs[5].revision.revision_ref.to_dict()),
        dict(request, copy_source_ref=selected.revision.revision_ref.to_dict())]
    for index, bad in enumerate(invalid):
        with pytest.raises((ValueError, RegistryConflict)):
            author.publish(request=bad, command_id="invalid:request:" + str(index))
        assert support.counts(core) == before
    copied = copy_root(selected)
    changed = deepcopy(copied); changed["module"]["name"] = "ChangedCopy"
    missing = deepcopy(copied); missing["copy_sources"].pop(next(iter(missing["copy_sources"])))
    same_ids = deepcopy(copied); same_ids["element_ids"] = ids(selected)
    reversed_map = deepcopy(copied); keys = list(reversed_map["copy_sources"])
    reversed_map["copy_sources"][keys[0]], reversed_map["copy_sources"][keys[-1]] = (
        reversed_map["copy_sources"][keys[-1]], reversed_map["copy_sources"][keys[0]])
    foreign = deepcopy(copied); foreign["copy_source_ref"]["source_id"] = "foreign-source"
    for index, bad in enumerate((changed, missing, same_ids, reversed_map, foreign)):
        with pytest.raises((ValueError, RegistryConflict)):
            author.publish(request=bad, command_id="invalid:copy:" + str(index))
        assert support.counts(core) == before
    value = author.publish(request=request, command_id="derived:claims")
    before = support.counts(core)
    with pytest.raises(RegistryConflict, match="legacy author"):
        ordinary.publish(module=value.module, element_ids=ids(value), parent_ref=value.revision.revision_ref,
                         command_id="legacy:E")
    with pytest.raises(RegistryConflict, match="legacy merge"):
        merger.analyze(local_ref=value.revision.revision_ref, incoming_ref=selected.revision.revision_ref,
                       command_id="legacy:merge")
    assert support.counts(core) == before
    p2 = OpenRegionDerivedAuthor(gateway, registration, author.producer)
    before = support.counts(core)
    with pytest.raises(RegistryConflict, match="unsupported_contract"):
        p2.publish(request=edit(value, "WrongFamily"), command_id="p2:E")
    assert support.counts(core) == before
    identity = "member:" + "a" * 32
    options = dict(name="Legacy", connections=[], completion=AssemblyCompletion(identity, ids(value)["/terminal"]),
                   budget_policy="shared_exact", deployment_intent="same_run_candidate")
    for cls, member_cls in ((AssemblyAuthor, AssemblyMember), (AssemblyAuthorV2, AssemblyMemberV2),
                            (AssemblyAuthorV3, AssemblyMemberV3)):
        legacy = cls(gateway, registration, author.producer)
        before = support.counts(core)
        with pytest.raises(RegistryConflict, match="legacy Assembly"):
            legacy.publish(**options, members=[member_cls(identity, "E", value.revision.revision_ref)], command_id=cls.__name__)
        assert support.counts(core) == before
    for cls, member in ((AssemblyAuthorV4, AssemblyMemberV4(identity, "E", value.revision.revision_ref,
                            "plain_closed_v1", None, None)),
                       (AssemblyAuthorV5, AssemblyMemberV5(identity, "E", value.revision.revision_ref,
                            "ordinary_new_definition", value.command_ref, value.historical_origins_ref))):
        legacy = cls(gateway, registration, author.producer)
        before = support.counts(core)
        with pytest.raises(RegistryConflict, match="claim_not_proved"):
            legacy.publish(**options, members=[member], command_id=cls.__name__)
        assert support.counts(core) == before
    assembly = AssemblyAuthorV7(gateway, registration, author.producer)
    member = AssemblyMemberV7(identity, "E", value.revision.revision_ref, "ordinary_new_definition",
                              value.command_ref, value.historical_origins_ref)
    before = support.counts(core)
    for bad in (replace(member, derived_command_ref=selected.command_ref),
                replace(member, historical_origins_ref=selected.resolution_ref),
                replace(member, claim="plain_closed_v1", derived_command_ref=None, historical_origins_ref=None),
                replace(member, revision_ref=selected.revision.revision_ref)):
        with pytest.raises(RegistryConflict, match="claim_not_proved"):
            assembly.publish(**options, members=[bad], command_id="v7:bad")
        assert support.counts(core) == before
    with pytest.raises(TypeError, match="exact collaboration_assembly_revision/v7 ref"):
        assembly.publish(**options, members=[member], command_id="v7:foreign-version",
                         parent_ref=selected.revision.revision_ref)
    assert support.counts(core) == before
    assert_author_only(core)


def test_original_transplant_and_descendant_history_bytes_are_strongly_consumed(fixture):
    from cpn.rpnh.collaboration import SourceQualifiedResourceRef
    from cpn.rpnh.collaboration.plain_transplant_derived import COMMAND_SCHEMA, HISTORY_SCHEMA
    from cpn.rpnh.registry.object_store import ObjectIntegrityError
    inputs, selected, author, left, right, *_ = fixture
    core, gateway, registration = inputs[:3]
    first = author.publish(request=edit(selected, "First"), command_id="damage:E")
    second = author.publish(request=edit(first, "Second"), command_id="damage:E2")
    copied = author.publish(request=copy_root(first), command_id="damage:copy")
    assembly = AssemblyAuthorV7(gateway, registration, author.producer).publish(**assembly_request(second, copied))
    before = support.counts(core)
    for ref in (selected.command_ref, selected.analysis_ref, selected.resolution_ref,
                *selected.revision.selected_change_refs, inputs[5].revision.definition_ref,
                left.revision.definition_ref, right.revision.definition_ref, first.command_ref,
                second.historical_origins_ref, copied.historical_origins_ref,
                SourceQualifiedResourceRef(author.binding["source_id"], author.schemas[COMMAND_SCHEMA]),
                SourceQualifiedResourceRef(author.binding["source_id"], author.schemas[HISTORY_SCHEMA])):
        path = core.object_store.path_for_version(ref.ref.resource_version_id)
        raw = path.read_bytes()
        try:
            path.write_bytes(b"{}")
            target = copied if ref == copied.historical_origins_ref else second
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                validate_closed_revision(core, target.revision.revision_ref, registration)
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                validate_assembly_revision(core, assembly.revision.revision_ref, registration)
            assert support.counts(core) == before
        finally:
            path.write_bytes(raw)
    assert validate_closed_revision(core, copied.revision.revision_ref, registration).historical_origins == copied.historical_origins
    assert validate_assembly_revision(core, assembly.revision.revision_ref, registration).revision == assembly.revision
    assert_author_only(core)


def test_first_command_and_final_dependency_cut_resume_only_exact_original(fixture, monkeypatch):
    from cpn.rpnh.collaboration import open_region
    from cpn.rpnh.registry.object_store import ObjectIntegrityError
    from cpn.rpnh.registry.event_store import StaleWriterError
    from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
    inputs, selected, author, *_ = fixture
    core, gateway, registration = inputs[:3]
    ancestor = author.publish(request=edit(selected, "RecoveryAncestor"), command_id="derived:recovery-ancestor")
    request = edit(ancestor, "Original")
    publish = open_region._publish_private_system
    def cut_after_command(core, owner, resource):
        result = publish(core, owner, resource)
        if resource.idempotency_key.endswith(":command"):
            raise RuntimeError("derived complete command cut")
        return result
    before = support.counts(core)
    with monkeypatch.context() as patch:
        patch.setattr(open_region, "_publish_private_system", cut_after_command)
        with pytest.raises(RuntimeError, match="complete command cut"):
            author.publish(request=request, command_id="derived:recover")
    frozen = support.counts(core)
    assert frozen[0] == before[0] + 1
    changed = deepcopy(request); changed["module"]["name"] = "Different"
    changed_ids = deepcopy(request); changed_ids["element_ids"]["/"] = "element:" + uuid.uuid4().hex
    changed_source = deepcopy(request); changed_source["parent_revision_ref"] = selected.revision.revision_ref.to_dict()
    for bad in (changed, changed_ids, changed_source):
        with pytest.raises(RegistryConflict, match="command_conflict"):
            author.publish(request=bad, command_id="derived:recover")
        assert support.counts(core) == frozen
    path = core.object_store.path_for_version(selected.command_ref.ref.resource_version_id)
    raw = path.read_bytes()
    def cut_dependency(core, owner, resource):
        result = publish(core, owner, resource)
        if resource.idempotency_key.endswith(":historical_origins"):
            path.write_bytes(b"{}")
        return result
    revisions = len(core.event_store.object_rows_by_type("collaboration_net_revision/v1"))
    try:
        with monkeypatch.context() as patch:
            patch.setattr(open_region, "_publish_private_system", cut_dependency)
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                author.publish(request=request, command_id="derived:recover")
    finally:
        path.write_bytes(raw)
    assert len(core.event_store.object_rows_by_type("collaboration_net_revision/v1")) == revisions
    frozen = support.counts(core)
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    next_author = PlainTransplantDerivedAuthor(next_gateway, support.registration(), author.producer)
    with pytest.raises(StaleWriterError):
        author.publish(request=request, command_id="derived:recover")
    with pytest.raises(RegistryConflict, match="command_conflict"):
        next_author.publish(request=changed, command_id="derived:recover")
    assert support.counts(reopened) == frozen
    value = next_author.publish(request=request, command_id="derived:recover")
    assert support.counts(reopened) == (frozen[0] + 1, frozen[1] + 2)
    assert validate_closed_revision(reopened, value.revision.revision_ref, support.registration()).historical_origins == value.historical_origins
    frozen = support.counts(reopened)
    assert next_author.publish(request=request, command_id="derived:recover").revision == value.revision
    assert support.counts(reopened) == frozen
    assert_author_only(reopened)


def test_canonical_missing_history_false_selection_and_marker_downgrades_fail(fixture):
    from cpn.rpnh.collaboration import plain_transplant_derived as derived
    from cpn.rpnh.collaboration.open_region import _authorities_at, _publish_command
    from cpn.rpnh.collaboration.plain_merge_result import _metadata
    from cpn.rpnh.collaboration._open_region_inventory import signature
    from cpn.rpnh.registry.schema_catalog import canonical_text
    inputs, selected, author, *_ = fixture
    core, _, registration = inputs[:3]
    first = author.publish(request=edit(selected, "Ancestor"), command_id="canonical:ancestor")
    for variant in ("missing_origin", "false_selected", "false_relation", "old_marker_edit",
                    "old_marker_copy", "unknown_marker", "dual_marker", "self_copy"):
        command_id = "canonical:" + variant
        request = copy_root(first) if variant.endswith("copy") else edit(first, "Counterfeit")
        with core.event_store.connect() as db:
            db.execute("BEGIN")
            command, record, *_ = derived._prepare_at(db, core, registration, author.binding, author.producer,
                command_id, request, _authorities_at(db, core, author.schemas), set())
        historical = variant in {"missing_origin", "false_selected", "false_relation"}
        spec = next(row for row in command["prepared_materials"]
                    if row["role"] == ("historical_origins" if historical else "definition"))
        if historical:
            origin = next(row for row in spec["document"]["origins"] if row["proof_kind"] == "plain_transplant_v1")
            if variant == "missing_origin":
                spec["document"]["origins"].remove(origin)
            elif variant == "false_selected":
                origin["selected_change_refs"] = []
            else:
                origin["relationship"] = "copied_from"
        elif variant == "self_copy":
            command["request"]["copy_source_ref"] = record.revision_ref.to_dict()
        else:
            marker = canonical_text(derived._material_ref(core, author.binding, derived._key(command_id) + ":command").to_dict())
            if variant == "dual_marker":
                spec["metadata"]["descriptors"]["closed_author_command_v1"] = marker
            else:
                name = "unknown_author_command_v1" if variant == "unknown_marker" else "closed_author_command_v1"
                spec["metadata"]["descriptors"] = {name: marker}
        reference = derived._resource(core, spec["resource_ref"])
        spec["metadata"] = _metadata(core, author.binding, reference, spec["schema"],
            command["schema_authorities"][spec["schema"]], spec["document"], spec["metadata"]["summary"],
            spec["metadata"]["descriptors"])
        spec.update(signature(spec["document"]))
        # Persist a complete canonical negative fixture, bypassing only this
        # test's publication callback. The real public reader must reject it.
        _publish_command(author, command, record, key=derived._key(command_id), marker=derived.MARKER,
                         schema=derived.COMMAND_SCHEMA, final_validate=lambda *_: None)
        before = support.counts(core)
        with pytest.raises(RegistryConflict, match="command_conflict|legacy author|immutable complete command|cycle|markers"):
            validate_closed_revision(core, record.revision_ref, registration)
        assert support.counts(core) == before
    assert_author_only(core)


def test_v7_final_dependency_cut_and_strict_generated_pair(fixture, monkeypatch):
    from dataclasses import replace
    from cpn.rpnh.collaboration import assembly_v7 as implementation
    from cpn.rpnh.registry.object_store import ObjectIntegrityError
    from cpn.rpnh.registry.event_store import StaleWriterError
    from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
    inputs, selected, author, *_ = fixture
    core, gateway, registration = inputs[:3]
    first = author.publish(request=edit(selected, "Member"), command_id="derived:assembly-recovery")
    copied = author.publish(request=copy_root(first), command_id="copy:assembly-recovery")
    assembly = AssemblyAuthorV7(gateway, registration, author.producer)
    request = assembly_request(first, copied, "assembly:recovery")
    path = core.object_store.path_for_version(selected.command_ref.ref.resource_version_id)
    raw = path.read_bytes()
    publish = implementation.AssemblyAuthorV7._publish_document
    def damaged(author, key, schema, document, **kwargs):
        result = publish(author, key, schema, document, **kwargs)
        if schema == implementation.LOWERING_V7_SCHEMA:
            path.write_bytes(b"{}")
        return result
    try:
        with monkeypatch.context() as patch:
            patch.setattr(implementation.AssemblyAuthorV7, "_publish_document", damaged)
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                assembly.publish(**request)
    finally:
        path.write_bytes(raw)
    assert core.event_store.object_rows_by_type("collaboration_assembly_revision/v7") == ()
    frozen = support.counts(core)
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    next_registration = support.registration()
    next_author = AssemblyAuthorV7(next_gateway, next_registration, author.producer)
    assert support.counts(reopened) == frozen
    with pytest.raises(StaleWriterError):
        assembly.publish(**request)
    with pytest.raises(RegistryConflict, match="conflicts"):
        next_author.publish(**{**request, "name": "ChangedAssembly"})
    assert support.counts(reopened) == frozen
    actual = next_author.publish(**request)
    assert support.counts(reopened) == (frozen[0] + 1, frozen[1] + 2)
    for invalid in (selected, first):
        with reopened.event_store.connect() as db:
            db.execute("BEGIN")
            with pytest.raises(RegistryConflict, match="exact pair"):
                implementation._validate_materials_at(db, reopened,
                    replace(actual.revision, generated_revision_ref=invalid.revision.revision_ref), next_registration,
                    implementation._binding_at(db, reopened), None)
    assert type(actual.generated) is ValidatedClosedRevision
    before = support.counts(reopened)
    assert next_author.publish(**request).revision == actual.revision
    assert support.counts(reopened) == before
    assert_author_only(reopened)


def test_explicit_catalog_versions_preserve_all_old_documents_and_types():
    from cpn.rpnh.collaboration import plain_transplant_schema_data, plain_transplant_assembly_schema_data
    from cpn.rpnh.collaboration.assembly_v7 import resolver_recipe
    prior, prior_types, prior_paths = plain_transplant_schema_data()
    derived, derived_types, _ = plain_transplant_derived_schema_data()
    actual, actual_types, paths = plain_transplant_derived_assembly_schema_data()
    assert set(derived) - set(prior) == {"rpnh/collaboration/plain_transplant_derived_author_command/v1",
                                      "rpnh/collaboration/plain_transplant_historical_origins/v1"}
    assert derived_types == prior_types
    assembly_prior, assembly_types, assembly_paths = plain_transplant_assembly_schema_data()
    assert set(actual) - set(derived) - set(assembly_prior) == {
        "registry_v1/collaboration_assembly_revision/v7", "rpnh/collaboration/assembly_plan/v7",
        "rpnh/collaboration/assembly_lowering_map/v7", "rpnh/collaboration/assembly_author_command/v7"}
    by_type = {item.name: item for item in actual_types}
    for schemas, types, expected_paths in ((prior, prior_types, prior_paths),
                                         (assembly_prior, assembly_types, assembly_paths)):
        assert all(by_type[item.name] == item for item in types)
        for key, body in schemas.items():
            assert canonical_json(actual[key]) == canonical_json(body)
            assert paths[key].read_bytes() == expected_paths[key].read_bytes()
    assert resolver_recipe()["contract"] == "rpnh/collaboration/direct_transplant_derived_member_resolver/v1"
    assert "collaboration_assembly_revision/v6" not in by_type
    assert not any(key.endswith("/v6") for key in actual)
