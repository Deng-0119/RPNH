"""Real D/E/copy histories and explicit ordinary Assembly author consumers."""
from copy import deepcopy
import json
import uuid

import pytest

import test_collaboration_open_regions as support
from cpn.rpnh.collaboration import (
    AssemblyAuthorV5, AssemblyMemberV5, AssemblyCompletion, AssemblyConnection,
    OpenRegionDerivedAuthor, ValidatedOpenDerivedRevision, ValidatedAdaptedRevision,
    ValidatedClosedRevision, current_branch, read_branch_version, read_assembly_revision,
    validate_closed_revision, validate_assembly_revision, open_region_derived_assembly_schema_data,
)
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.schema_catalog import canonical_json


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(support, "open_region_assembly_schema_data", open_region_derived_assembly_schema_data)
    inputs = support.fixture.__wrapped__(tmp_path)
    opened = support._publish(inputs)
    closer, request = support._closure_request(inputs, opened)
    adapted = closer.publish(request=request, command_id="close:BC")
    author = OpenRegionDerivedAuthor(inputs[1], inputs[4], inputs[2].producer)
    return inputs, opened, adapted, author


def ids(value):
    return {row["locator"]: row["element_id"] for row in value.element_map["elements"]}


def edit(value, name):
    module = deepcopy(value.module.to_dict())
    module["name"] = name
    return {"operation": "edit", "authority_mode": "ordinary_new_definition",
        "parent_revision_ref": value.revision.revision_ref.to_dict(), "copy_source_ref": None,
        "module": module, "element_ids": ids(value), "copy_sources": {}}


def copy_root(value):
    copied_ids = {locator: "element:" + uuid.uuid4().hex for locator in ids(value)}
    return {"operation": "copy_root", "authority_mode": "ordinary_new_definition",
        "parent_revision_ref": None, "copy_source_ref": value.revision.revision_ref.to_dict(),
        "module": value.module.to_dict(), "element_ids": copied_ids,
        "copy_sources": {copied_ids[locator]: identity for locator, identity in ids(value).items()}}


def assembly_request(left, right, command_id="assembly:ordinary"):
    members = [AssemblyMemberV5("member:" + n * 32, name, value.revision.revision_ref,
        "ordinary_new_definition", value.command_ref, value.historical_origins_ref)
        for n, name, value in (("a", "Edited", left), ("b", "Independent copy", right))]
    exit_id = next(row["element_id"] for row in left.element_map["elements"] if row["kind"] == "exit")
    entry_id = next(row["element_id"] for row in right.element_map["elements"] if row["kind"] == "entry")
    return {"name": "Ordinary_definitions", "members": members,
        "connections": [AssemblyConnection(members[0].member_id, exit_id, members[1].member_id, entry_id)],
        "completion": AssemblyCompletion(members[1].member_id, ids(right)["/terminal"]),
        "budget_policy": "shared_exact", "deployment_intent": "same_run_candidate", "command_id": command_id}


def no_run(core):
    assert core.event_store.object_rows_by_type("net_instance/v1") == ()
    assert core.event_store.list_events_by_type(("net_adopted/v1", "marking_checkpoint_committed/v1",
        "firing_started/v1", "execution_instance_created/v1")) == ()


def test_real_derived_chain_copies_branch_and_actual_v5_assembly(fixture, monkeypatch):
    inputs, opened, adapted, author = fixture
    core, gateway, closed, _, registration, source, _ = inputs
    first_request = edit(adapted, "Ordinary_E")
    first = author.publish(request=first_request, command_id="derived:E")
    second = author.publish(request=edit(first, "Ordinary_E2"), command_id="derived:E2")
    copies = [author.publish(request=copy_root(value), command_id="copy:" + name)
              for name, value in (("D", adapted), ("E", first))]
    assert first.revision.parent_revision_refs == (adapted.revision.revision_ref,)
    assert second.revision.parent_revision_refs == (first.revision.revision_ref,)
    assert first.revision.revision_ref.ref.entity_id == adapted.revision.revision_ref.ref.entity_id
    assert {row["revision_ref"]["ref"]["version_id"] for row in second.historical_origins["origins"]} == {
        str(adapted.revision.revision_ref.ref.version_id), str(first.revision.revision_ref.ref.version_id)}
    assert all(row["relationship"] == "ancestor" for row in second.historical_origins["origins"])
    for value, original in zip(copies, (adapted, first), strict=True):
        assert value.revision.parent_revision_refs == ()
        assert value.revision.revision_ref.ref.entity_id != original.revision.revision_ref.ref.entity_id
        assert value.module.to_dict() == original.module.to_dict()
        assert set(ids(value).values()).isdisjoint(ids(original).values())
        assert len(value.historical_origins["copy_origins"]) == len(ids(original))
        assert all(row["copied_from"] is None for row in value.element_map["elements"])
        assert all(row["relationship"] == "copied_from" for row in value.historical_origins["origins"])
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    for value in (first, second, *copies):
        reread = validate_closed_revision(reader, value.revision.revision_ref, registration)
        assert type(reread) is ValidatedOpenDerivedRevision
        assert reread.current_adaptation is None and reread.derivation == "historical_only"
        assert reread.historical_origins == value.historical_origins
        assert canonical_json(reread.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    branch = gateway.create_author_branch(head_revision_ref=adapted.revision.revision_ref, command_id="branch:D")
    at_first = gateway.advance_author_branch(expected_branch_version_ref=branch.branch_ref,
        expected_head_revision_ref=adapted.revision.revision_ref, expected_stream_head=branch.sequence,
        next_revision_ref=first.revision.revision_ref, command_id="branch:E")
    valid = {"expected_branch_version_ref": at_first.branch_ref, "expected_head_revision_ref": first.revision.revision_ref,
        "expected_stream_head": at_first.sequence, "next_revision_ref": second.revision.revision_ref}
    before = support._counts(core)
    for key, value in (("expected_branch_version_ref", branch.branch_ref),
                       ("expected_head_revision_ref", adapted.revision.revision_ref), ("expected_stream_head", 100)):
        with pytest.raises(RegistryConflict):
            gateway.advance_author_branch(**{**valid, key: value}, command_id="branch:bad:" + key)
        assert support._counts(core) == before
    at_second = gateway.advance_author_branch(**valid, command_id="branch:E2")
    before = support._counts(core)
    assert gateway.advance_author_branch(expected_branch_version_ref=branch.branch_ref,
        expected_head_revision_ref=adapted.revision.revision_ref, expected_stream_head=branch.sequence,
        next_revision_ref=first.revision.revision_ref, command_id="branch:E") == at_first
    assert current_branch(core, branch.branch_ref.ref.entity_id) == at_second
    assert support._counts(core) == before
    for step, expected in ((branch, adapted), (at_first, first), (at_second, second)):
        historical = read_branch_version(reader, step.branch_ref)
        full = validate_closed_revision(reader, historical.head_revision_ref, registration)
        assert full.revision == expected.revision
    assembly = AssemblyAuthorV5(gateway, registration, closed.producer)
    request = assembly_request(second, copies[1])
    value = assembly.publish(**request)
    assert type(value.generated) is ValidatedClosedRevision
    assert value.revision.revision_ref.ref.entity_type == "collaboration_assembly_revision/v5"
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
    before = support._counts(core)
    assert assembly.publish(**request).revision == value.revision
    assert author.publish(request=first_request, command_id="derived:E").revision == first.revision
    assert support._counts(core) == before
    no_run(core)


def test_explicit_claim_copy_contract_and_legacy_gates(fixture):
    from dataclasses import replace
    from cpn.rpnh.collaboration import (AssemblyAuthor, AssemblyAuthorV2, AssemblyAuthorV3, AssemblyAuthorV4,
        AssemblyMember, AssemblyMemberV2, AssemblyMemberV3, AssemblyMemberV4, PlainModuleMergeAnalyzer)
    inputs, _, adapted, author = fixture
    core, gateway, closed, _, registration, source, _ = inputs
    request = edit(adapted, "ExplicitOrdinary")
    before = support._counts(core)
    invalid = [dict(request, authority_mode=None), dict(request, authority_mode="retain_adaptation"),
        {key: value for key, value in request.items() if key != "authority_mode"},
        dict(request, parent_revision_ref=source.revision.revision_ref.to_dict()),
        dict(request, copy_source_ref=adapted.revision.revision_ref.to_dict())]
    for index, bad in enumerate(invalid):
        with pytest.raises((ValueError, RegistryConflict)):
            author.publish(request=bad, command_id="invalid:request:" + str(index))
        assert support._counts(core) == before
    copied = copy_root(adapted)
    changed = deepcopy(copied); changed["module"]["name"] = "ChangedCopy"
    missing = deepcopy(copied); missing["copy_sources"].pop(next(iter(missing["copy_sources"])))
    same_ids = deepcopy(copied); same_ids["element_ids"] = ids(adapted)
    reversed_map = deepcopy(copied)
    keys = list(reversed_map["copy_sources"])
    reversed_map["copy_sources"][keys[0]], reversed_map["copy_sources"][keys[-1]] = (
        reversed_map["copy_sources"][keys[-1]], reversed_map["copy_sources"][keys[0]])
    for index, bad in enumerate((changed, missing, same_ids, reversed_map)):
        with pytest.raises((ValueError, RegistryConflict)):
            author.publish(request=bad, command_id="invalid:copy:" + str(index))
        assert support._counts(core) == before
    value = author.publish(request=request, command_id="derived:claims")
    before = support._counts(core)
    with pytest.raises(RegistryConflict, match="legacy author"):
        closed.publish(module=value.module, element_ids=ids(value), parent_ref=value.revision.revision_ref,
                       command_id="legacy:E")
    assert support._counts(core) == before
    identity = "member:" + "a" * 32
    options = dict(name="Legacy", connections=[], completion=AssemblyCompletion(identity, ids(value)["/terminal"]),
        budget_policy="shared_exact", deployment_intent="same_run_candidate")
    for cls, member_cls in ((AssemblyAuthor, AssemblyMember), (AssemblyAuthorV2, AssemblyMemberV2),
                            (AssemblyAuthorV3, AssemblyMemberV3)):
        legacy = cls(gateway, registration, closed.producer)
        before = support._counts(core)
        with pytest.raises(RegistryConflict, match="legacy Assembly"):
            legacy.publish(**options, members=[member_cls(identity, "E", value.revision.revision_ref)], command_id=cls.__name__)
        assert support._counts(core) == before
    v4 = AssemblyAuthorV4(gateway, registration, closed.producer)
    before = support._counts(core)
    for claim, adaptation, intent in (("plain_closed_v1", None, None),
                                     ("adapted_result_current", adapted.adaptation_ref, adapted.intent_ref)):
        with pytest.raises(RegistryConflict, match="claim_not_proved"):
            v4.publish(**options, members=[AssemblyMemberV4(identity, "E", value.revision.revision_ref,
                claim, adaptation, intent)], command_id="v4:" + claim)
        assert support._counts(core) == before
    merger = PlainModuleMergeAnalyzer(gateway, registration, closed.producer)
    before = support._counts(core)
    with pytest.raises(RegistryConflict, match="legacy merge"):
        merger.analyze(local_ref=value.revision.revision_ref, incoming_ref=adapted.revision.revision_ref,
                       command_id="legacy:merge")
    assert support._counts(core) == before
    v5 = AssemblyAuthorV5(gateway, registration, closed.producer)
    member = AssemblyMemberV5(identity, "E", value.revision.revision_ref, "ordinary_new_definition",
                              value.command_ref, value.historical_origins_ref)
    before = support._counts(core)
    for bad in (replace(member, derived_command_ref=adapted.command_ref),
                replace(member, historical_origins_ref=adapted.origin_map_ref),
                replace(member, claim="plain_closed_v1", derived_command_ref=None, historical_origins_ref=None),
                replace(member, revision_ref=adapted.revision.revision_ref)):
        with pytest.raises(RegistryConflict, match="claim_not_proved"):
            v5.publish(**options, members=[bad], command_id="v5:bad")
        assert support._counts(core) == before
    no_run(core)


def test_ancestor_copy_and_missing_history_bytes_reject_full_chain_and_assembly(fixture):
    from cpn.rpnh.collaboration import SourceQualifiedResourceRef
    from cpn.rpnh.collaboration.open_region_derived import COMMAND_SCHEMA, HISTORY_SCHEMA
    from cpn.rpnh.registry.object_store import ObjectIntegrityError
    inputs, opened, adapted, author = fixture
    core, gateway, closed, _, registration, source, _ = inputs
    first = author.publish(request=edit(adapted, "First"), command_id="derived:damage:E")
    second = author.publish(request=edit(first, "Second"), command_id="derived:damage:E2")
    copied = author.publish(request=copy_root(first), command_id="derived:damage:copy")
    assembly = AssemblyAuthorV5(gateway, registration, closed.producer).publish(**assembly_request(second, copied))
    before = support._counts(core)
    # These are actual authoritative resource files, not substitute readers.
    for ref in (adapted.command_ref, opened.provenance_ref, source.revision.definition_ref,
                first.command_ref, copied.historical_origins_ref, second.historical_origins_ref,
                SourceQualifiedResourceRef(author.binding["source_id"], author.schemas[COMMAND_SCHEMA]),
                SourceQualifiedResourceRef(author.binding["source_id"], author.schemas[HISTORY_SCHEMA])):
        path = core.object_store.path_for_version(ref.ref.resource_version_id)
        raw = path.read_bytes()
        try:
            path.write_bytes(b"{}" if ref in (copied.historical_origins_ref, second.historical_origins_ref)
                             else raw[:-1] + (b" " if raw[-1:] != b" " else b"x"))
            target = copied if ref == copied.historical_origins_ref else second
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                validate_closed_revision(core, target.revision.revision_ref, registration)
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                validate_assembly_revision(core, assembly.revision.revision_ref, registration)
            assert support._counts(core) == before
        finally:
            path.write_bytes(raw)
    assert validate_closed_revision(core, copied.revision.revision_ref, registration).historical_origins == copied.historical_origins
    assert validate_assembly_revision(core, assembly.revision.revision_ref, registration).revision == assembly.revision
    no_run(core)


def test_interrupted_derived_prefix_and_final_cut_resume_exact_original(fixture, monkeypatch):
    from cpn.rpnh.collaboration import open_region
    from cpn.rpnh.registry.object_store import ObjectIntegrityError
    from cpn.rpnh.registry.event_store import StaleWriterError
    from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
    inputs, _, adapted, author = fixture
    core, gateway, closed, _, registration, _, _ = inputs
    request = edit(adapted, "Original")
    publish = open_region._publish_private_system
    def cut_after_command(core, owner, resource):
        result = publish(core, owner, resource)
        if resource.idempotency_key.endswith(":command"):
            raise RuntimeError("derived complete command cut")
        return result
    before = support._counts(core)
    with monkeypatch.context() as patch:
        patch.setattr(open_region, "_publish_private_system", cut_after_command)
        with pytest.raises(RuntimeError, match="complete command cut"):
            author.publish(request=request, command_id="derived:recover")
    frozen = support._counts(core)
    assert frozen[0] == before[0] + 1
    changed = deepcopy(request); changed["module"]["name"] = "Different"
    with pytest.raises(RegistryConflict, match="command_conflict"):
        author.publish(request=changed, command_id="derived:recover")
    assert support._counts(core) == frozen
    path = core.object_store.path_for_version(adapted.command_ref.ref.resource_version_id)
    raw = path.read_bytes()
    def cut_dependency(core, owner, resource):
        result = publish(core, owner, resource)
        if resource.idempotency_key.endswith(":historical_origins"):
            path.write_bytes(b"{}")
        return result
    try:
        with monkeypatch.context() as patch:
            patch.setattr(open_region, "_publish_private_system", cut_dependency)
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                author.publish(request=request, command_id="derived:recover")
    finally:
        path.write_bytes(raw)
    assert len(core.event_store.object_rows_by_type("collaboration_net_revision/v1")) == 3
    frozen = support._counts(core)
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    next_registration = support._registration()
    next_author = OpenRegionDerivedAuthor(next_gateway, next_registration, closed.producer)
    with pytest.raises(StaleWriterError):
        author.publish(request=request, command_id="derived:recover")
    with pytest.raises(RegistryConflict, match="command_conflict"):
        next_author.publish(request=changed, command_id="derived:recover")
    assert support._counts(reopened) == frozen
    value = next_author.publish(request=request, command_id="derived:recover")
    assert support._counts(reopened) == (frozen[0] + 1, frozen[1] + 2)
    assert validate_closed_revision(reopened, value.revision.revision_ref, next_registration).historical_origins == value.historical_origins
    no_run(reopened)


def test_canonical_missing_origin_and_old_marker_cannot_downgrade(fixture):
    from cpn.rpnh.collaboration import open_region_derived as derived
    from cpn.rpnh.collaboration.open_region import _authorities_at, _publish_command
    from cpn.rpnh.collaboration.plain_merge_result import _metadata
    from cpn.rpnh.collaboration._open_region_inventory import signature
    from cpn.rpnh.registry.schema_catalog import canonical_text
    inputs, _, adapted, author = fixture
    core, _, _, _, registration, _, _ = inputs
    first = author.publish(request=edit(adapted, "Ancestor"), command_id="canonical:ancestor")
    for variant in ("missing_origin", "old_marker_edit", "old_marker_copy", "self_copy"):
        command_id = "canonical:" + variant
        request = copy_root(first) if variant.endswith("copy") else edit(first, "CanonicalCounterfeit")
        with core.event_store.connect() as db:
            db.execute("BEGIN")
            command, record, *_ = derived._prepare_at(db, core, registration, author.binding, author.producer,
                command_id, request, _authorities_at(db, core, author.schemas), set())
        role = "historical_origins" if variant == "missing_origin" else "definition"
        spec = next(row for row in command["prepared_materials"] if row["role"] == role)
        if variant == "missing_origin":
            assert len(spec["document"]["origins"]) == 2
            spec["document"]["origins"] = spec["document"]["origins"][:1]
        elif variant == "self_copy":
            command["request"]["copy_source_ref"] = record.revision_ref.to_dict()
        else:
            spec["metadata"]["descriptors"] = {"closed_author_command_v1":
                canonical_text(derived._material_ref(core, author.binding, derived._key(command_id) + ":command").to_dict())}
        reference = derived._resource(core, spec["resource_ref"])
        spec["metadata"] = _metadata(core, author.binding, reference, spec["schema"],
            command["schema_authorities"][spec["schema"]], spec["document"], spec["metadata"]["summary"],
            spec["metadata"]["descriptors"])
        spec.update(signature(spec["document"]))
        # Deliberately bypass the semantic publication callback only to persist
        # a canonical negative fixture; no validated value is fabricated.
        _publish_command(author, command, record, key=derived._key(command_id), marker=derived.MARKER,
            schema=derived.COMMAND_SCHEMA, final_validate=lambda *_: None)
        before = support._counts(core)
        with pytest.raises(RegistryConflict, match="command_conflict|legacy author|immutable complete command|cycle"):
            validate_closed_revision(core, record.revision_ref, registration)
        assert support._counts(core) == before
    no_run(core)


def test_v5_final_dependency_cut_and_strict_generated_pair(fixture, monkeypatch):
    from dataclasses import replace
    from cpn.rpnh.collaboration import assembly_v5 as implementation
    from cpn.rpnh.registry.object_store import ObjectIntegrityError
    from cpn.rpnh.registry.event_store import StaleWriterError
    from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
    inputs, _, adapted, author = fixture
    core, gateway, closed, _, registration, _, _ = inputs
    first = author.publish(request=edit(adapted, "Member"), command_id="derived:assembly-recovery")
    copied = author.publish(request=copy_root(first), command_id="copy:assembly-recovery")
    assembly = AssemblyAuthorV5(gateway, registration, closed.producer)
    request = assembly_request(first, copied, "assembly:recovery")
    path = core.object_store.path_for_version(first.command_ref.ref.resource_version_id)
    raw = path.read_bytes()
    publish = implementation.AssemblyAuthorV5._publish_document
    def damaged(author, key, schema, document, **kwargs):
        result = publish(author, key, schema, document, **kwargs)
        if schema == implementation.LOWERING_V5_SCHEMA:
            path.write_bytes(b"{}")
        return result
    try:
        with monkeypatch.context() as patch:
            patch.setattr(implementation.AssemblyAuthorV5, "_publish_document", damaged)
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                assembly.publish(**request)
    finally:
        path.write_bytes(raw)
    assert core.event_store.object_rows_by_type("collaboration_assembly_revision/v5") == ()
    frozen = support._counts(core)
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    next_registration = support._registration()
    next_author = AssemblyAuthorV5(next_gateway, next_registration, closed.producer)
    assert support._counts(reopened) == frozen
    with pytest.raises(StaleWriterError):
        assembly.publish(**request)
    with pytest.raises(RegistryConflict, match="conflicts"):
        next_author.publish(**{**request, "name": "ChangedAssembly"})
    assert support._counts(reopened) == frozen
    actual = next_author.publish(**request)
    assert support._counts(reopened) == (frozen[0] + 1, frozen[1] + 2)
    with reopened.event_store.connect() as db:
        db.execute("BEGIN")
        with pytest.raises(RegistryConflict, match="exact pair"):
            implementation._validate_materials_at(db, reopened,
                replace(actual.revision, generated_revision_ref=first.revision.revision_ref), next_registration,
                implementation._binding_at(db, reopened), None)
    before = support._counts(reopened)
    assert next_author.publish(**request).revision == actual.revision
    assert support._counts(reopened) == before
    no_run(reopened)


def test_new_catalog_preserves_frozen_v4_and_older_schema_types():
    from cpn.rpnh.collaboration import open_region_assembly_schema_data, open_region_derived_schema_data
    prior, prior_types, prior_paths = open_region_assembly_schema_data()
    derived, derived_types, _ = open_region_derived_schema_data()
    actual, actual_types, paths = open_region_derived_assembly_schema_data()
    assert set(derived) - set(prior) == {"rpnh/collaboration/open_region_derived_author_command/v1",
                                      "rpnh/collaboration/open_region_historical_origins/v1"}
    assert derived_types == prior_types
    assert set(actual) - set(derived) == {"registry_v1/collaboration_assembly_revision/v5",
        "rpnh/collaboration/assembly_plan/v5", "rpnh/collaboration/assembly_lowering_map/v5",
        "rpnh/collaboration/assembly_author_command/v5"}
    by_type = {item.name: item for item in actual_types}
    assert all(by_type[item.name] == item for item in prior_types)
    for key, body in prior.items():
        assert canonical_json(actual[key]) == canonical_json(body)
        assert paths[key].read_bytes() == prior_paths[key].read_bytes()
