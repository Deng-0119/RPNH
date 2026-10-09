"""Explicit owner policy and Registry-bound evidence; no executor authority."""
from dataclasses import dataclass
import json

from .contracts import (AnalysisPolicy, EnvironmentContract, OperationCase,
    OperationModel, ProducedSpec, SchedulerContract, TerminalContract,
    canonical_data, canonical_json)
from .projection import build_analysis_input
from .evidence import analyze
from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.resources import (AttemptCounterAuthority, PrivateSystemOrigin,
    PublishResource, ResourceVersionRef, TypedMarkingSnapshot)
from ..registry.publication import _ref_payload


@dataclass(frozen=True, slots=True)
class ValidationConfiguration:
    operation_models: tuple[OperationModel, ...] = ()
    terminal_contract: TerminalContract = TerminalContract()
    environment_contract: EnvironmentContract = EnvironmentContract()
    scheduler_contract: SchedulerContract = SchedulerContract()
    policy: AnalysisPolicy = AnalysisPolicy()

    def to_dict(self):
        return canonical_data(self)


def _identity(value):
    if set(value) != {"kind", "value"}:
        raise ValueError("invalid typed identity")
    return TypedId(value["kind"], value["value"])


def _ref(value):
    if value is None:
        return None
    if set(value) == {"entity_type", "entity_id", "version_id"}:
        return VersionRef(value["entity_type"], _identity(value["entity_id"]),
                          _identity(value["version_id"]))
    if set(value) == {"resource_id", "resource_version_id"}:
        return ResourceVersionRef(_identity(value["resource_id"]),
                                  _identity(value["resource_version_id"]))
    raise ValueError("invalid exact analysis reference")


def configuration_from_dict(document):
    if set(document) != {"operation_models", "terminal_contract", "environment_contract",
                         "scheduler_contract", "policy"}:
        raise ValueError("unknown or missing PN configuration fields")
    models = []
    for item in document["operation_models"]:
        cases = []
        for case in item["cases"]:
            specs = []
            for spec in case["produced"]:
                spec = dict(spec)
                for key in ("resource_ref", "work_resource_ref", "output_place_ref", "output_binding_ref"):
                    spec[key] = _ref(spec[key])
                specs.append(ProducedSpec(**spec))
            cases.append(OperationCase(**dict(case, produced=tuple(specs))))
        models.append(OperationModel(**dict(item, cases=tuple(cases))))
    terminal = dict(document["terminal_contract"])
    for key in ("success_places", "permitted_failure_places", "unfinished_places",
                "released_lease_places", "persistent_places"):
        terminal[key] = tuple(terminal[key])
    policy = dict(document["policy"])
    policy["properties"] = tuple(policy["properties"])
    policy["required_properties"] = tuple(policy["required_properties"])
    if policy["mode"] not in {"advisory", "strict"}:
        raise ValueError("PN policy mode must be advisory or strict")
    return ValidationConfiguration(tuple(models), TerminalContract(**terminal),
        EnvironmentContract(**document["environment_contract"]),
        SchedulerContract(**document["scheduler_contract"]), AnalysisPolicy(**policy))


def policy_allows(configuration, report):
    if configuration.policy.mode == "advisory":
        return True
    results = {item["property_id"]: item["verdict"] for item in report["properties"]}
    return all(results.get(name) == "HOLDS"
               for name in configuration.policy.required_properties)


def publish_receipt(owner, role, data, *, command_id, lineage=(), transaction=None, target_net_ref=None):
    from ..registry.resource_service import _publish_private_system
    from ..registry.schema_catalog import canonical_json as wire_json
    body = {"kind": "receipt", "command_id": command_id,
        "source": _ref_payload(owner.principal_ref),
        "target": str((target_net_ref or owner.publication.net_ref).entity_id), "data": data,
        "lineage": [_ref_payload(ref.as_version_ref()) for ref in lineage]}
    return _publish_private_system(owner._core, owner.identity.task_ref, PublishResource(
        origin=PrivateSystemOrigin(owner.bootstrap_ref), payload=wire_json(body),
        media_type="application/json", content_schema_ref="rpnh/owner_control/v1",
        summary="Finite PN " + role, lifetime_ref=owner.bootstrap_ref,
        derived_from=tuple(lineage), descriptors={"pn_validation_role": role,
            "command_id": command_id}, idempotency_key=command_id + ":pn:" + role),
        transaction=transaction)


def registered_configuration(owner):
    rows = []
    for row in owner._core.event_store.canonical_object_rows(object_type="resource_version/v1"):
        metadata = json.loads(row["metadata_json"])
        if (metadata.get("descriptors", {}).get("pn_validation_role") == "configuration"
                and metadata["task_ref"] == _ref_payload(owner.identity.task_ref)):
            prepared = owner._core.get_version(row["version_id"])
            body = json.loads(owner._core.object_store.read_registered(prepared))
            if body["source"] != _ref_payload(owner.principal_ref):
                raise ValueError("PN configuration belongs to a different owner")
            rows.append((ResourceVersionRef(prepared.logical_id, prepared.version_id), body["data"]))
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("PN configuration must have one immutable owner anchor")
    ref, data = rows[0]
    return ref, configuration_from_dict(data)


def snapshot_binding(*, old_net, candidate_net, old_compiled, candidate_compiled,
                     checkpoint, source_tokens, proposed_tokens, resource_plan,
                     token_mappings=(), ordinary_retirements=(), owner_input_mappings=()):
    """Shared detached input for pre-analysis and commit-snapshot equality."""
    return canonical_data({"old_net": old_net, "candidate_net": candidate_net,
        "old_compiled": old_compiled, "candidate_compiled": candidate_compiled,
        "checkpoint": checkpoint,
        "source_tokens": sorted(source_tokens, key=lambda token: token["token_id"]),
        "proposed_tokens": sorted(proposed_tokens, key=lambda token: token["token_id"]),
        "resource_plan": resource_plan, "token_mappings": token_mappings,
        "ordinary_retirements": ordinary_retirements,
        "owner_input_mappings": owner_input_mappings})


def _registered_body(core, ref):
    return core.get_version(ref.version_id).metadata


def _preview_mapping_documents(preview):
    allocation = preview.allocation
    return {
        "token_mappings": [{"old_token_ref": _ref_payload(m.old_token_ref),
            "new_token_ref": _ref_payload(m.new_token_ref)} for m in allocation.token_mappings],
        "ordinary_retirements": [{"old_token_ref": _ref_payload(m.old_token_ref),
            "source_checkpoint_ref": _ref_payload(preview.source_marking.checkpoint_ref)}
            for m in allocation.ordinary_retirements],
        "owner_input_mappings": [{"owner_resource_ref": _ref_payload(m.owner_resource_ref.as_version_ref()),
            "source_ref": _ref_payload(m.source_ref), "place": m.place,
            "new_token_ref": _ref_payload(m.new_token_ref)} for m in allocation.owner_input_mappings]}


def analysis_for_owner(owner, configuration, *, preview=None):
    from ..registry.module_runtime import hydrate_module_runtime
    from ..registry.owner_mapping import token_state
    executable, structure, marking = hydrate_module_runtime(owner._core)
    old_net = _registered_body(owner._core, executable.net_ref)
    if preview is None:
        net_ref, candidate_structure = executable.net_ref, structure
        proposed = [_registered_body(owner._core, token.token_ref) for token in marking.tokens]
        snapshot = TypedMarkingSnapshot(marking.epoch, marking.next_token_id,
            marking.attempts, tuple(token.state for token in marking.tokens))
        mappings = {}
    else:
        net_ref, candidate_structure = preview.structure.registry_net_ref, preview.structure
        proposed = [json.loads(payload) for _ref_value, payload, _parents in preview.prepared]
        snapshot = TypedMarkingSnapshot(marking.epoch, preview.allocation.next_token_id,
            tuple(AttemptCounterAuthority(**item) for item in preview.allocation.attempts), preview.tokens)
        mappings = _preview_mapping_documents(preview)
    binding = snapshot_binding(old_net=old_net,
        candidate_net=_registered_body(owner._core, net_ref),
        old_compiled=structure.compiled.to_dict(), candidate_compiled=candidate_structure.compiled.to_dict(),
        checkpoint=_registered_body(owner._core, marking.checkpoint_ref),
        source_tokens=[_registered_body(owner._core, token.token_ref) for token in marking.tokens],
        proposed_tokens=proposed, resource_plan=candidate_structure._resource_plan, **mappings)
    return build_analysis_input(candidate_structure.compiled, snapshot,
        candidate_structure._resource_plan, configuration.operation_models,
        configuration.terminal_contract, configuration.environment_contract,
        configuration.scheduler_contract, configuration.policy, net_ref=net_ref,
        registry_binding=binding, configuration=configuration.to_dict())


def initialize_validation(owner, configuration, *, command_id):
    if configuration is None:
        owner.pn_validation_report = {"policy": "advisory", "verdict": "UNKNOWN",
            "reason": "finite operation/terminal models not configured"}
        return
    if not isinstance(configuration, ValidationConfiguration):
        raise TypeError("pn_validation requires an explicit ValidationConfiguration")
    configuration = configuration_from_dict(configuration.to_dict())
    anchor = publish_receipt(owner, "configuration", configuration.to_dict(),
        command_id=command_id, lineage=(owner.publication.declaration_resource_ref,))
    report = analyze(analysis_for_owner(owner, configuration)).to_dict()
    publish_receipt(owner, "initial-report", {"configuration_ref": _ref_payload(anchor.as_version_ref()),
        "report": report}, command_id=command_id, lineage=(anchor, owner.publication.declaration_resource_ref))
    owner.pn_validation_report = report
    if not policy_allows(configuration, report):
        from ..registry.event_store import RegistryConflict
        raise RegistryConflict("PN strict initial gate: required property is not HOLDS")


def analyze_owner_preview(owner, preview):
    configured = registered_configuration(owner)
    if configured is None:
        return None
    anchor, configuration = configured
    report = analyze(analysis_for_owner(owner, configuration, preview=preview)).to_dict()
    owner.pn_validation_report = report
    if not policy_allows(configuration, report):
        from ..registry.event_store import RegistryConflict
        raise RegistryConflict("PN strict owner edit: required property is not HOLDS")
    return {"configuration_ref": _ref_payload(anchor.as_version_ref()), "report": report}


def restore_validation(owner):
    configured = registered_configuration(owner)
    if configured is None:
        owner.pn_validation_report = {"policy": "advisory", "verdict": "UNKNOWN",
            "reason": "finite operation/terminal models not configured"}
        return
    anchor, configuration = configured
    reports = []
    for row in owner._core.event_store.canonical_object_rows(object_type="resource_version/v1"):
        metadata = json.loads(row["metadata_json"])
        if (metadata.get("descriptors", {}).get("pn_validation_role") == "initial-report"
                and metadata["task_ref"] == _ref_payload(owner.identity.task_ref)):
            prepared = owner._core.get_version(row["version_id"])
            body = json.loads(owner._core.object_store.read_registered(prepared))
            if (body["source"] != _ref_payload(owner.principal_ref)
                    or body["data"]["configuration_ref"] != _ref_payload(anchor.as_version_ref())):
                raise ValueError("initial PN evidence differs from its owner configuration")
            reports.append(body["data"]["report"])
    if len(reports) != 1 or not policy_allows(configuration, reports[0]):
        from ..registry.event_store import RegistryConflict
        raise RegistryConflict("PN initial gate has no accepted registered report")
    owner.pn_validation_report = reports[0]
