"""Every durable transform prefix is locked, resumable and owner-fenced."""
from copy import deepcopy
import hashlib
import json

import pytest

import test_collaboration_plain_merge as support
from test_collaboration_plain_transform import fixture, split_request
from cpn.rpnh.collaboration import PlainModuleTransformAuthor, SourceQualifiedResourceRef, validate_closed_revision
from cpn.rpnh.collaboration import plain_transform as implementation
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict, StaleWriterError
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import canonical_json, canonical_text
from cpn.rpnh.registry.transaction import RegistryTransaction

CUTS = ("command", *implementation.ROLES, "revision")


class DurableCut(RuntimeError):
    pass


@pytest.mark.parametrize("cut", CUTS)
def test_every_durable_transform_prefix_reopens_and_locks_all_refs(fixture, monkeypatch, cut):
    inputs, author, _ = fixture
    core, gateway, _, _, _, base, _ = inputs
    args = split_request(base)
    key = implementation._key(args["command_id"])
    roles = {key + ":" + role: role for role in CUTS[:-1]}
    roles[key] = "revision"
    before, observed, payloads = support.counts(core), [], {}
    original = RegistryTransaction.commit
    def interrupt_after_commit(transaction):
        result = original(transaction)
        if transaction.event_store is core.event_store and transaction.idempotency_key in roles:
            role = roles[transaction.idempotency_key]
            assert role == CUTS[len(observed)] and transaction._closed
            assert len(transaction._objects) == 1
            prepared = transaction._objects[0]
            row = core.event_store.object_row(prepared.version_id)
            assert row is not None and row["transaction_id"] == str(transaction.transaction_id)
            raw = core.object_store.path_for_version(prepared.version_id).read_bytes()
            payloads[role] = (prepared.version_id, raw)
            observed.append(role)
            if role == cut:
                raise DurableCut(role)
        return result
    monkeypatch.setattr(RegistryTransaction, "commit", interrupt_after_commit)
    with pytest.raises(DurableCut, match=cut):
        author.publish(**args)
    monkeypatch.setattr(RegistryTransaction, "commit", original)
    assert observed == list(CUTS[:CUTS.index(cut) + 1])
    prefix = support.counts(core)
    assert prefix[0] - before[0] == len(observed)
    assert len(core.event_store.object_rows_by_type("collaboration_net_revision/v1")) == (2 if cut == "revision" else 1)
    command = json.loads(payloads["command"][1])
    assert command["request"]["parent_ref"] == base.revision.revision_ref.to_dict()
    assert command["request"]["transform_groups"] == args["transform_groups"]
    pinned = {row["ref"]["version_id"] for row in command["input_pins"]}
    assert str(base.revision.revision_ref.ref.version_id) in pinned
    for field in ("definition_ref", "element_mapping_ref", "boundary_mapping_ref", "host_requirements_ref"):
        assert str(getattr(base.revision, field).ref.resource_version_id) in pinned
    for spec in command["prepared_materials"]:
        assert spec["sha256"] == hashlib.sha256(canonical_json(spec["document"])).hexdigest()
        assert spec["metadata"]["content_schema_authority_ref"] == command["schema_authorities"][spec["schema"]]
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    replay = PlainModuleTransformAuthor(next_gateway, support.registration(), author.producer)
    assert support.counts(reopened) == prefix
    with pytest.raises(StaleWriterError):
        author.publish(**args)
    changed_module = args["module"].to_dict(); changed_module["name"] = "ChangedReplay"
    with pytest.raises(RegistryConflict, match="conflict"):
        replay.publish(**{**args, "module": ModuleDeclaration.from_dict(changed_module)})
    assert support.counts(reopened) == prefix
    for version, raw in payloads.values():
        assert reopened.object_store.path_for_version(version).read_bytes() == raw
    value = replay.publish(**args)
    assert value.revision.revision_ref.to_dict() == command["result_revision_ref"]
    assert value.revision.parent_revision_refs == (base.revision.revision_ref,)
    for spec in command["prepared_materials"]:
        ref = SourceQualifiedResourceRef.from_dict(spec["resource_ref"], catalog=core.catalog)
        assert reopened.object_store.path_for_version(ref.ref.resource_version_id).read_bytes() == canonical_json(spec["document"])
    assert support.counts(reopened)[0] - before[0] == 7
    reader = _RegistryCore(reopened.run_dir, create=False, read_only=True, catalog=reopened.catalog)
    assert validate_closed_revision(reader, value.revision.revision_ref, support.registration()).transformation == value.transformation
    frozen = support.counts(reopened)
    assert replay.publish(**args).revision == value.revision
    assert support.counts(reopened) == frozen
    print("TRANSFORM_DURABLE_PREFIX=" + json.dumps({"cut": cut, "committed_roles": observed,
        "locked_input_count": len(pinned), "output_roles": [row["role"] for row in command["prepared_materials"]]}, sort_keys=True))


def test_first_command_rejects_changed_mapping_ids_source_and_equal_schema_authority(fixture, monkeypatch):
    inputs, author, _ = fixture
    core, gateway, _, ordinary, _, base, ids = inputs
    alternate = ordinary.publish(module=base.module, element_ids=ids, parent_ref=base.revision.revision_ref, command_id="alternate-parent")
    schema = implementation.MAP_SCHEMA
    selected = author.schemas[schema]
    raw = core.object_store.path_for_version(selected.resource_version_id).read_bytes()
    equal_schema = implementation._publish_private_system(core, gateway._task_ref, PublishResource(
        origin=PrivateSystemOrigin(gateway._bootstrap_ref), payload=raw, media_type="application/schema+json",
        content_schema_ref="registry_v1/registry_type_catalog/v1", summary="Equal schema alternative",
        lifetime_ref=gateway._bootstrap_ref, descriptors={"host_registration_kind": "schema", "registered_key": schema},
        idempotency_key="fixture:equal-transform-schema"))
    original = implementation._publish_private_system
    def cut(core, owner, resource):
        result = original(core, owner, resource)
        if resource.content_schema_ref == implementation.COMMAND_SCHEMA:
            raise DurableCut("command")
        return result
    args = split_request(base)
    monkeypatch.setattr(implementation, "_publish_private_system", cut)
    with pytest.raises(DurableCut):
        author.publish(**args)
    monkeypatch.setattr(implementation, "_publish_private_system", original)
    frozen = support.counts(core)
    changed = split_request(alternate)
    with pytest.raises(RegistryConflict, match="conflict"):
        author.publish(**changed)
    changed = split_request(base, command_id="different-identities")
    changed["command_id"] = args["command_id"]
    with pytest.raises(RegistryConflict, match="conflict"):
        author.publish(**changed)
    # Exchange the two source port identities: a valid same-kind but different caller claim.
    changed = deepcopy(args)
    port_groups = [g for g in changed["transform_groups"] if g["source_element_ids"][0] in
                   (ids["/components/b/ports/request"], ids["/components/b/ports/result"])]
    port_groups[0]["source_element_ids"], port_groups[1]["source_element_ids"] = port_groups[1]["source_element_ids"], port_groups[0]["source_element_ids"]
    changed["transform_groups"].sort(key=canonical_text)
    with pytest.raises(RegistryConflict, match="conflict"):
        author.publish(**changed)
    author.schemas[schema] = equal_schema
    try:
        with pytest.raises(RegistryConflict, match="conflict"):
            author.publish(**args)
    finally:
        author.schemas[schema] = selected
    assert support.counts(core) == frozen
    value = author.publish(**args)
    assert value.transformation["transform_groups"] == args["transform_groups"]
