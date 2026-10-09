"""Freeze the enumerable declaration subset, not an H7 execution inventory.

No provider/profile/private configuration file is opened. No HOST factory,
entry-point selector, executor or tool is invoked. Module preparation uses the
original compiler and its explicitly trusted registered component lowerers.
Such lowerers retain the existing HOST trust boundary, not a Python sandbox.

These bytes intentionally cannot be passed to register_child_intent. Complete
installed implementation/configuration/opaque credential-binding inventories
need an additional registered material contract; a profile name cannot supply
that missing proof. Native issuance/reservation/execution remains unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .bound_child_lowering import with_bound_origin
from .compiler import compile_module
from .module import ModuleDeclaration
from .registration import Registration
from .registry.parent_child import ParentChildUnsupported, _target, digest
from .registry.runtime_binding_contracts import freeze_candidate_document, _copy_record_ref


def _bytes(value):
    return freeze_candidate_document(value).encode("ascii")


@dataclass(frozen=True, slots=True)
class FrozenBoundChildDeclarations:
    """Detached exact declaration bytes; never a complete launch material DTO."""
    child_kind: str
    payloads: tuple[tuple[str, bytes], ...]

    def __post_init__(self):
        if (self.child_kind not in ("module", "agent_task")
                or type(self.payloads) is not tuple
                or any(type(row) is not tuple or len(row) != 2
                       or type(row[0]) is not str or type(row[1]) is not bytes
                       for row in self.payloads)
                or tuple(sorted(self.payloads)) != self.payloads
                or len(dict(self.payloads)) != len(self.payloads)):
            raise TypeError("declaration inventory requires unique sorted immutable bytes")

    @property
    def inventory(self):
        return [{"name": name, "size": len(value), "sha256": digest(value)}
                for name, value in self.payloads]

    @property
    def declaration_digest(self):
        return digest(_bytes({"child_kind": self.child_kind, "inventory": self.inventory}))

    def assert_unchanged(self, current):
        if type(current) is not type(self) or current != self:
            raise ValueError("bound child declarations changed after freeze")

    def require_execution_materials(self):
        raise ParentChildUnsupported(
            "declarations are incomplete: registered installed implementation, public "
            "configuration and opaque secret-binding inventory contract is unavailable")


def _module_materials(module, registration):
    if type(module) is not ModuleDeclaration or type(registration) is not Registration:
        raise TypeError("declaration preparation requires the original Module and Registration")
    # Detach author data before invoking its selected registered lowerers.
    source = _bytes(module.to_dict())
    module = ModuleDeclaration.from_dict(json.loads(source))
    before = _bytes({"declarations": list(registration.declarations())})
    bound = with_bound_origin(module)
    compiled = compile_module(bound, registration)
    if _bytes({"declarations": list(registration.declarations())}) != before:
        raise ValueError("HOST Registration mutated during declaration-only lowering")
    return {
        "application_declaration": source,
        "module": _bytes(bound.to_dict()),
        "lowered_net": _bytes(compiled.to_dict()),
        "registration": _bytes(compiled.registrations),
        "budgets": _bytes({"budgets": bound.to_dict()["budgets"],
                          "budget_buckets": bound.to_dict()["budget_buckets"]}),
    }


def freeze_bound_module_declarations(module, registration):
    """Freeze exact referenced Registration/schema bodies and actual lowering.

    Caller supplies the original trusted, already selected Registration. This
    function does not load a HostProfile or infer installed-code completeness.
    Arbitrary schema/config/ref-shaped business data is retained as inert data.
    """
    return FrozenBoundChildDeclarations("module", tuple(sorted(
        _module_materials(module, registration).items())))


def freeze_bound_agent_declarations(spec, *, parent, request_ref, root_binding,
                                    control_root, target_root, slot_id):
    """Normalize the existing plugin-free single-agent declaration at final T.

    This intentionally stops before execution-selection loading. It preserves
    AgentTaskSpec's original relative-path serializer, including legal sibling
    paths. It reads no referenced file. Path resolution is the original Spec's
    behavior; it is not an OS-independent lexical path proof or reservation.
    No caller path is silently rewritten, before or after freezing.
    """
    from .agent_tasks import AgentTaskSpec, agent_task_registration, build_agent_task_module
    if type(spec) is not AgentTaskSpec:
        raise TypeError("agent declaration preparation requires AgentTaskSpec")
    if (spec.workflow_graph is not None or len(spec.stages) != 1
            or spec.plugin_configuration is not None or spec.plugin_catalog_digest is not None
            or spec.managed_bindings or spec.managed_tool_policy is not None
            or spec.tool_program_policy is not None or spec.execution_profiles):
        raise ParentChildUnsupported("agent declaration slice supports one unmanaged plugin-free stage")
    ref = _copy_record_ref(request_ref, "resource_version/v1")
    parent = json.loads(_bytes(parent))
    # This slice uses the same one static slot as the original H7 request.
    # Its caller must supply that explicit identity, not a generated fallback.
    import re
    if type(slot_id) is not str or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", slot_id) is None:
        raise ValueError("explicit canonical static slot is required")
    target = _target(parent, slot_id, root_binding, str(control_root))
    target_root = Path(target_root)
    if not target_root.is_absolute():
        raise ValueError("trusted target root must be absolute")
    expected = (target_root / target["relative_path"]).resolve()
    if spec.run_dir != expected or spec.owner_socket_path != expected / "owner.sock":
        raise ValueError("AgentTaskSpec transport paths differ from the already derived target")
    document_root = Path(target["document_root"])
    worker = spec.as_worker_document(document_root=document_root)
    if AgentTaskSpec.from_worker_document(worker, document_root=document_root) != spec:
        raise ValueError("AgentTaskSpec is not stable at its final sealed document root")
    module = build_agent_task_module(spec.stages, max_attempts_per_stage=spec.max_attempts_per_stage)
    materials = _module_materials(module, agent_task_registration())
    materials["normalized_worker_document"] = _bytes(worker)
    from .registry.publication import _ref_payload
    materials["target_context"] = _bytes({"parent": parent, "request_ref": _ref_payload(ref), "target": target})
    materials["inputs"] = _bytes({"prompt": spec.prompt, "owner_statement": spec.owner_statement,
                                "max_parallel_nodes": spec.max_parallel_nodes})
    return FrozenBoundChildDeclarations("agent_task", tuple(sorted(materials.items())))


__all__ = ("FrozenBoundChildDeclarations", "freeze_bound_module_declarations",
           "freeze_bound_agent_declarations")
