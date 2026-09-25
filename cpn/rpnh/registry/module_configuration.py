"""Exact generic run/process configuration authority for Module execution."""
from __future__ import annotations

from ._registry import _RegistryCore
from .bootstrap import NativeRunIdentity
from .event_store import verified_adoption_head, verified_checkpoint_head
from .models import VersionRef, TypedRelation
from .module_nets import ModuleNetPublication
from .publication import _stable_id, _current_process_configuration_refs_v1
from .resources import ResourceVersionRef
from .schema_catalog import canonical_json
from .strict_contracts import _registered, ref_payload, content_schema_ref_payload


def publish_module_configuration(core: _RegistryCore, publication: ModuleNetPublication, *,
                                 identity: NativeRunIdentity, immutable_input_ref: ResourceVersionRef,
                                 mutable_stage_ref: ResourceVersionRef, recovery_manifest_ref: VersionRef,
                                 model_condition: str, idempotency_key: str) -> VersionRef:
    """Bind actual registered source versions for this sole writer entry.

    Model identity is an explicit owner condition, not inferred from executor or
    provider. This function does not call a model or fabricate model accounting.
    """
    if not isinstance(core, _RegistryCore) or core.read_only:
        raise TypeError("run configuration requires the execution owner's Registry")
    if not isinstance(publication, ModuleNetPublication) or not isinstance(identity, NativeRunIdentity):
        raise TypeError("run configuration requires exact Module and run identities")
    if not isinstance(model_condition, str) or not model_condition:
        raise ValueError("run condition must be explicit owner data")
    if verified_adoption_head(core.event_store, core.catalog, core.task_id) != publication.net_ref:
        raise ValueError("Module configuration differs from adopted net head")
    checkpoint = verified_checkpoint_head(core.event_store, core.catalog, core.task_id, publication.net_ref)
    for resource in (immutable_input_ref, mutable_stage_ref):
        if not isinstance(resource, ResourceVersionRef):
            raise TypeError("configuration inputs require exact registered resources")
        _, metadata = _registered(core, resource.as_version_ref(), "resource_version/v1")
        if metadata["task_ref"] != ref_payload(identity.task_ref):
            raise ValueError("run configuration resource belongs to another task")
        prepared = core.get_version(resource.resource_version_id)
        core.object_store.read_registered(prepared)
    if core.recovery_manifest_ref() != recovery_manifest_ref:
        raise ValueError("run configuration differs from exact startup budget authority")
    previous = None
    if core.event_store.canonical_object_rows(object_type="run_execution_authority/v1"):
        from .run_authority import current_run_execution_authority
        from .resource_service import _ResourceServiceKernel
        previous, current = current_run_execution_authority(core, _ResourceServiceKernel(core),
            expected_model_condition=model_condition)
        if current["run_ref"] != ref_payload(identity.run_ref):
            raise ValueError("configuration differs from the existing exact run lineage")
    authority_ref = VersionRef("run_execution_authority/v1",
        previous.entity_id if previous is not None else _stable_id("run_execution_authority", idempotency_key),
        _stable_id("run_execution_authority_version", idempotency_key))
    prepared = core.get_version(publication.declaration_resource_ref.resource_version_id)
    metadata = {"run_execution_authority_ref": ref_payload(authority_ref), "run_ref": ref_payload(identity.run_ref),
        "task_ref": ref_payload(identity.task_ref), "declaration_ref": ref_payload(publication.declaration_resource_ref.as_version_ref()),
        "declaration_schema_ref": prepared.metadata["content_schema_ref"], "latest_checkpoint_ref": ref_payload(checkpoint),
        "model_condition": model_condition, "status": "running", "terminal_evidence_ref": None}
    tx = core.begin(idempotency_key=idempotency_key)
    tx.prewrite(object_type="run_execution_authority/v1", logical_id=authority_ref.entity_id, version_id=authority_ref.version_id,
        payload=canonical_json(metadata), metadata=metadata, media_type="application/json",
        schema_ref="registry_v1/run_execution_authority/v1")
    if previous is not None:
        tx.relate(TypedRelation(_stable_id("relation", idempotency_key, "previous-authority"),
            "derived_from", authority_ref, previous), system_owned=True)
    for label, ref in (("immutable_genesis", immutable_input_ref.as_version_ref()),
                       ("mutable_stage", mutable_stage_ref.as_version_ref()),
                       ("recovery_manifest", recovery_manifest_ref)):
        tx.relate(TypedRelation(_stable_id("relation", idempotency_key, label), "derived_from", authority_ref, ref,
                               metadata={"authority_role": label}), system_owned=True)
    tx.commit()
    pointer = {"immutable_genesis_ref": content_schema_ref_payload(immutable_input_ref),
        "mutable_stage_ref": content_schema_ref_payload(mutable_stage_ref),
        "recovery_manifest_ref": ref_payload(recovery_manifest_ref), "run_execution_authority_ref": ref_payload(authority_ref)}
    key = f"execution_process_configuration:writer-{core.writer_epoch}"
    value = canonical_json(pointer).decode("utf-8")
    if core.event_store.get_or_create_meta(key, value) != value:
        raise ValueError("writer entry already selected another exact process configuration")
    if _current_process_configuration_refs_v1(core) != (immutable_input_ref, mutable_stage_ref, recovery_manifest_ref, authority_ref):
        raise ValueError("process configuration pointer differs from published authority")
    return authority_ref


__all__ = ("publish_module_configuration",)
