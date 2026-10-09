"""Finite PN evidence checked inside the owner-adoption commit snapshot."""
import json

from ..pn_validation.contracts import canonical_data
from ..pn_validation.evidence import verify_report
from ..pn_validation.projection import build_analysis_input
from ..pn_validation.runtime_gate import (configuration_from_dict, policy_allows,
                                         snapshot_binding)
from ..executable_net import load_compiled_net
from .event_store import RegistryConflict, _CANONICAL_EVENT_SQL, _exact_object_metadata
from .models import PreparedObject, VersionRef
from .identities import TypedId
from .object_store import ObjectStore
from .publication import _resource_from_payload, _version_from_payload, _ref_payload
from .resources import AttemptCounterAuthority, TypedMarkingSnapshot
from .schema_catalog import canonical_json
from .owner_mapping import token_state


def _document(store, catalog, db, ref):
    row = db.execute("SELECT * FROM objects WHERE version_id=?", (str(ref.version_id),)).fetchone()
    if row is None:
        raise RegistryConflict("PN evidence refers to an absent ordinary object")
    _exact_object_metadata(store, ref, expected_type=ref.entity_type, db=db)
    prepared = PreparedObject(row["object_type"], TypedId.parse(row["logical_id"]),
        TypedId.parse(row["version_id"]), row["size"], row["media_type"], row["schema_ref"],
        None if row["producer_invocation_id"] is None else TypedId.parse(row["producer_invocation_id"]),
        row["storage_locator"], json.loads(row["metadata_json"]))
    return json.loads(ObjectStore(store.path.parent / "objects", catalog, read_only=True)
                      .read_registered(prepared))


def _resource_plan(store, catalog, db, compiled, candidate, net, root):
    """Collect only declaration closure material, then use the shared pure projector."""
    from ._module_resource_projection import project_module_resource_plan
    metadata, schemas, creation = {}, {}, {}
    pending = [net, root]

    def refs(value):
        if isinstance(value, dict):
            if set(value) == {"entity_type", "logical_id", "version_id"}:
                yield _version_from_payload(value)
            elif set(value) == {"resource_id", "resource_version_id"}:
                yield _resource_from_payload(value).as_version_ref()
            else:
                for item in value.values():
                    yield from refs(item)
        elif isinstance(value, list):
            for item in value:
                yield from refs(item)

    while pending:
        for ref in refs(pending.pop()):
            if ref in metadata:
                continue
            body = _exact_object_metadata(store, ref, expected_type=ref.entity_type, db=db)
            metadata[ref] = body
            pending.append(body)
    for resource in net["module_resource_bindings"]["owner_resource_inputs"].values():
        body = metadata[_resource_from_payload(resource).as_version_ref()]
        authority = body["content_schema_authority_ref"]
        if authority.get("entity_type") == "registry_type_catalog/v1":
            continue
        ref = _resource_from_payload(authority).as_version_ref()
        document = _document(store, catalog, db, ref)
        schemas[ref] = (document["$id"], document)
    root_ref = _version_from_payload(net["team_design_root_ref"])
    for slot in net["module_resource_bindings"]["slot_refs"].values():
        body = metadata[_version_from_payload(slot)]
        if body["team_design_root_ref"] != _ref_payload(root_ref):
            ref = _version_from_payload(body["authored_index_ref"])
            creation[ref] = load_compiled_net(_document(store, catalog, db, ref))
    return project_module_resource_plan(compiled, candidate, net, root_ref, root,
        _resource_from_payload(net["team_net_declaration_resource_ref"]),
        exact_metadata=metadata, owner_schema_documents=schemas, creation_compiled=creation)


def validate_pn_owner_adoption(store, catalog, db, *, pending, objects,
                               checkpoint, old, old_net, net, root, exact, pn_evidence):
    """An immutable explicit owner policy requires bound SAMEtransaction evidence."""
    configurations = []
    rows = db.execute("SELECT o.* FROM objects o JOIN events e ON e.event_id=o.published_event_id "
        "WHERE o.object_type='resource_version/v1' AND " + _CANONICAL_EVENT_SQL).fetchall()
    for row in rows:
        metadata = json.loads(row["metadata_json"])
        if (metadata.get("descriptors", {}).get("pn_validation_role") == "configuration"
                and metadata["task_ref"] == root["task_ref"]):
            ref = VersionRef("resource_version/v1", TypedId.parse(row["logical_id"]),
                             TypedId.parse(row["version_id"]))
            configurations.append((ref, metadata, _document(store, catalog, db, ref)))
    if not configurations and pn_evidence is None:
        return  # Historical unconfigured compatibility; never a PN HOLDS claim.
    if len(configurations) != 1 or pn_evidence is None:
        raise RegistryConflict("PN adoption requires one owner configuration and bound result evidence")
    anchor, anchor_metadata, anchor_body = configurations[0]
    if (anchor_body["kind"] != "receipt" or anchor_body["source"] != root["owner_principal_ref"]
            or anchor_metadata["content_schema_ref"] != "rpnh/owner_control/v1"
            or anchor_metadata["origin_kind"] != "private_system"):
        raise RegistryConflict("PN policy lacks ordinary owner configuration origin")
    configuration = configuration_from_dict(anchor_body["data"])
    candidate = _version_from_payload(pending.payload["net_instance_ref"])
    if pn_evidence["configuration_ref"] != _ref_payload(anchor):
        raise RegistryConflict("PN evidence differs from its exact immutable owner configuration")
    declaration = _resource_from_payload(net["team_net_declaration_resource_ref"])
    old_declaration = _resource_from_payload(old_net["team_net_declaration_resource_ref"])
    compiled = load_compiled_net(_document(store, catalog, db, declaration.as_version_ref()))
    old_compiled = load_compiled_net(_document(store, catalog, db, old_declaration.as_version_ref()))
    plan = _resource_plan(store, catalog, db, compiled, candidate, net, root)
    source_tokens = [exact(value, "petri_token/v1")[1] for value in old["token_refs"]]
    proposed = [exact(value, "petri_token/v1", staged=True)[1] for value in checkpoint["token_refs"]]
    binding = snapshot_binding(old_net=old_net, candidate_net=net,
        old_compiled=old_compiled.to_dict(), candidate_compiled=compiled.to_dict(),
        checkpoint=old, source_tokens=source_tokens, proposed_tokens=proposed,
        resource_plan=plan, token_mappings=pending.payload["token_mappings"],
        ordinary_retirements=pending.payload["ordinary_retirements"],
        owner_input_mappings=pending.payload["owner_input_mappings"])
    snapshot = TypedMarkingSnapshot(checkpoint["epoch"], checkpoint["next_token_id"],
        tuple(AttemptCounterAuthority(**item) for item in checkpoint["attempts"]),
        tuple(token_state(_version_from_payload(item["petri_token_ref"]), item)
              for item in sorted(proposed, key=lambda token: token["token_id"])))
    current_input = build_analysis_input(compiled, snapshot, plan, configuration.operation_models,
        configuration.terminal_contract, configuration.environment_contract,
        configuration.scheduler_contract, configuration.policy, net_ref=candidate,
        registry_binding=binding, configuration=configuration.to_dict())
    report = pn_evidence["report"]
    if not verify_report(report, current_input) or not policy_allows(configuration, report):
        raise RegistryConflict("PN evidence is stale, changed, incomplete, or rejected by owner policy")
