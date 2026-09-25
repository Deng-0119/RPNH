"""Registered Module entry authority -> normal initial token/checkpoint facts."""
from __future__ import annotations

import json
from typing import Mapping

from ..executable_net import load_compiled_net
from ._registry import _RegistryCore
from .checkpoints import publish_initial_checkpoint
from .event_store import validate_registered_net_closure
from .models import VersionRef
from .module_nets import ModuleNetPublication
from .module_resources import entry_resource_bundle
from .publication import _stable_id, _resource_from_payload, _version_from_payload
from .resources import ResourceVersionRef
from .schema_catalog import canonical_json
from .strict_contracts import _registered, ref_payload, content_schema_ref_payload


def publish_module_initial_marking(core: _RegistryCore, publication: ModuleNetPublication, *,
                                   entry_inputs: Mapping[str, ResourceVersionRef],
                                   idempotency_key: str) -> VersionRef:
    """Allocate explicit owner entries and the registered declaration's fresh M0.

    No fake activation/operation/firing, ordinary retirement or terminal can
    manufacture these inputs. Resource lineage and the exact registered wire
    are verified before publication; normal Core checkpoint gates remain used.
    """
    if not isinstance(publication, ModuleNetPublication):
        raise TypeError("initial marking requires an actual Module publication receipt")
    net = validate_registered_net_closure(core.event_store, core.catalog, publication.net_ref)
    if net["team_design_root_ref"] != ref_payload(publication.root_ref):
        raise ValueError("Module receipt root differs from exact closure")
    declaration = core.get_version(publication.declaration_resource_ref.resource_version_id)
    if (declaration.logical_id != publication.declaration_resource_ref.resource_id
            or net["team_net_declaration_resource_ref"]
            != content_schema_ref_payload(publication.declaration_resource_ref)):
        raise ValueError("Module marking requires exact registered declaration resource")
    compiled = load_compiled_net(json.loads(core.object_store.read_registered(declaration)))
    ports = {port.name: port for port in compiled.ports}
    required = {key for key, name in compiled.symbolic.entry.items() if ports[name].minimum > 0}
    if not required <= set(entry_inputs) <= set(compiled.symbolic.entry):
        raise ValueError("initial marking inputs differ from Module entry keys")
    ports = {port.name: port for port in compiled.ports}
    places = {place.name: place for place in compiled.symbolic.places}
    _, root = _registered(core, publication.root_ref, "team_design_root/v1")
    # The registered HOST templates select fresh settled workspace genesis
    # authority. Carry those exact refs into M0; no component library or role
    # is consulted, and no prior checkpoint is rewritten.
    workspace_heads = {}
    for binding_ref in publication.binding_refs.values():
        _, binding = _registered(core, binding_ref, "operation_binding/v1")
        if binding["workspace_binding_ref"] is None:
            continue
        _, template = _registered(core, _version_from_payload(binding["workspace_binding_ref"]),
                                  "workspace_binding/v1")
        head = _version_from_payload(template["base_revision_ref"])
        _, revision = _registered(core, head, "workspace_revision/v1")
        if (template["binding_kind"] != "lineage_template"
                or template["operation_binding_ref"] != ref_payload(binding_ref)
                or template["workspace_lineage_ref"] != ref_payload(head)
                or revision["workspace_revision_ref"] != ref_payload(head)
                or revision["task_ref"] != root["task_ref"]
                or revision["run_ref"] != root["run_ref"]
                or revision["net_instance_ref"] != ref_payload(publication.net_ref)
                or revision["disposition"] != "genesis" or revision["settled"] is not True):
            raise ValueError("initial workspace differs from exact fresh registered genesis/template")
        workspace_heads[head.entity_id] = head
    counts = {}
    tokens = []
    def append_token(place, resource=None, colour=None, identity=None):
        ordinal = len(tokens)
        ref = VersionRef("petri_token/v1",
            _stable_id("petri_token", publication.net_ref.version_id, ordinal),
            _stable_id("petri_token_version", publication.net_ref.version_id, ordinal))
        counts[place] = counts.get(place, 0) + 1
        capacity = places[place].capacity
        if capacity is not None and counts[place] > capacity:
            raise ValueError("explicit fresh marking exceeds declared place capacity")
        token = {"petri_token_ref": ref_payload(ref), "net_instance_ref": ref_payload(publication.net_ref),
            "token_id": ordinal, "place": place, "epoch": 0, "producer": None,
            "consumer": None, "resource_ref": None if resource is None else content_schema_ref_payload(resource),
            "work_resource_ref": None, "kind": None, "consumed_by": None,
            "override_warning": None, "verdict": colour, "continuation": None,
            "lease_identity_ref": None if identity is None else ref_payload(identity), "lease_claims": []}
        tokens.append((ref, token))
    for entry, bundle in sorted(entry_inputs.items()):
        port = ports[compiled.symbolic.entry[entry]]
        resources = entry_resource_bundle(bundle)
        if not port.minimum <= len(resources) <= port.maximum:
            raise ValueError("initial input bundle violates declared quantity")
        for resource in resources:
            if not isinstance(resource, ResourceVersionRef):
                raise TypeError("Module owner input requires an exact registered resource")
            _, metadata = _registered(core, resource.as_version_ref(), "resource_version/v1")
            port = ports[compiled.symbolic.entry[entry]]
            if (metadata["task_ref"] != root["task_ref"]
                    or metadata["content_schema_ref"] != port.schema
                    or ref_payload(resource.as_version_ref()) not in root["resource_refs"]):
                raise ValueError("owner input differs from exact admitted task/schema/resource membership")
            source = metadata["content_schema_authority_ref"]
            # The net closure admission already verified the exact schema source;
            # revalidate actual bytes against that registered source, not prose.
            from .content_schemas import hydrate_registered_content_schema
            schema_ref = (_resource_from_payload(source) if "resource_id" in source
                          else _version_from_payload(source))
            def read_schema(ref):
                prepared = core.get_version(ref.resource_version_id)
                if prepared.logical_id != ref.resource_id:
                    raise ValueError("owner input schema ref differs")
                return core.object_store.read_registered(prepared)
            schema = hydrate_registered_content_schema(core, schema_ref, schema_id=port.schema,
                                                        fresh_reader=read_schema)
            from jsonschema import Draft7Validator
            prepared = core.get_version(resource.resource_version_id)
            document = json.loads(read_schema(schema.resource_ref)) if schema.resource_ref is not None else json.loads(
                core.catalog.schema_path(port.schema).read_bytes())
            Draft7Validator(document).validate(json.loads(core.object_store.read_registered(prepared)))
            append_token(port.place, resource)
    pools = {pool.lease_pool_place: pool for pool in publication.resource_plan.lease_pools}
    for place in compiled.symbolic.places:
        if place.name in pools:
            pool = pools[place.name]
            identities = pool.initial_resource_refs + pool.initial_logical_slot_refs
            declared_count = sum(token.count for token in place.initial_tokens)
            if place.initial_tokens and declared_count != len(identities):
                raise ValueError("declared lease M0 differs from exact registered identities")
            for exact in identities:
                identity = _version_from_payload({"entity_type": exact.entity_type,
                    "logical_id": exact.logical_id, "version_id": exact.version_id})
                _registered(core, identity)
                resource = (ResourceVersionRef(identity.entity_id, identity.version_id)
                    if identity.entity_type == "resource_version/v1" else None)
                append_token(place.name, resource, identity=identity)
            continue
        for initial in place.initial_tokens:
            literal_resource = None
            if initial.value is not None:
                # The owner explicitly authored this value in the registered
                # whole declaration. Register bytes normally before minting
                # occurrences; it is not an unsettled operation product.
                schema_id = initial.schema or place.schema
                candidates = []
                for value in root["resource_refs"]:
                    if value["entity_type"] != "resource_version/v1":
                        continue
                    ref = _version_from_payload(value)
                    prepared = core.get_version(ref.version_id)
                    if prepared.metadata.get("media_type") == "application/schema+json":
                        document = json.loads(core.object_store.read_registered(prepared))
                        if document.get("$id") == schema_id:
                            candidates.append((ref, document))
                if len(candidates) != 1:
                    raise ValueError("authored initial value lacks exact registered schema source")
                schema_source, document = candidates[0]
                from jsonschema import Draft7Validator
                Draft7Validator(document).validate(initial.value)
                from .resource_service import _publish_private_system
                from .resources import PrivateSystemOrigin, PublishResource
                task_ref = _version_from_payload(root["task_ref"])
                bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
                literal_resource = _publish_private_system(core, task_ref, PublishResource(
                    origin=PrivateSystemOrigin(bootstrap), payload=canonical_json(initial.value),
                    media_type="application/json", content_schema_ref=schema_id,
                    content_schema_authority_ref=ResourceVersionRef(schema_source.entity_id, schema_source.version_id),
                    summary=f"Owner-declared initial input {place.name}", lifetime_ref=bootstrap,
                    derived_from=(publication.declaration_resource_ref,),
                    idempotency_key=f"{idempotency_key}:literal:{place.name}:{len(tokens)}"))
            for _ in range(initial.count):
                append_token(place.name, resource=literal_resource, colour=initial.colour)
    tx = core.begin(idempotency_key=f"{idempotency_key}:tokens", net_instance_id=publication.net_ref.entity_id)
    for ref, token in tokens:
        tx.prewrite(object_type="petri_token/v1", logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(token), metadata=token, media_type="application/json",
            schema_ref="registry_v1/petri_token/v1")
    tx.commit()
    checkpoint_ref = VersionRef("marking_checkpoint/v1",
        _stable_id("marking_checkpoint", idempotency_key),
        _stable_id("marking_checkpoint_version", idempotency_key))
    return publish_initial_checkpoint(core, net_ref=publication.net_ref, root_ref=publication.root_ref,
        checkpoint_ref=checkpoint_ref, token_refs=tuple(ref for ref, _ in tokens),
        workspace_revision_refs=tuple(workspace_heads[key] for key in sorted(workspace_heads, key=str)),
        idempotency_key=idempotency_key)


__all__ = ("publish_module_initial_marking",)
