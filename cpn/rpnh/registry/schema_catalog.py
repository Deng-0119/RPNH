"""Mechanical Registry catalog and explicit HOST schema/type registration.

The catalog is an explicit interface inventory.  Historical schema files may
remain on disk as inert evidence, but they are not selectable, scanned, or
accepted implicitly. Optional component/workflow inventories belong to HOSTs.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft7Validator
from cpn.rpnh._schema_validation import check_draft7_schema
from jsonschema.exceptions import ValidationError
from jsonschema.validators import extend


class SchemaGovernanceError(RuntimeError):
    """The registered RPNH schema interface is missing, stale, or violated."""


def _json_equality_key(value: Any) -> Any:
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        return ("number", Decimal(str(value)).normalize())
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, list):
        return ("array", tuple(_json_equality_key(item) for item in value))
    if isinstance(value, Mapping):
        return (
            "object",
            tuple(sorted(
                (str(key), _json_equality_key(item))
                for key, item in value.items()
            )),
        )
    return ("unsupported", type(value).__qualname__, repr(value))


def _linear_unique_items(
    validator: Any,
    enabled: Any,
    instance: Any,
    schema: Any,
):
    del validator, schema
    if not enabled or not isinstance(instance, list):
        return
    seen = set()
    for item in instance:
        key = _json_equality_key(item)
        if key in seen:
            yield ValidationError(f"{instance!r} has non-unique elements")
            return
        seen.add(key)


_LinearUniqueDraft7Validator = extend(
    Draft7Validator,
    {"uniqueItems": _linear_unique_items},
)


def canonical_json(value: Any) -> bytes:
    """Serialize a JSON value with stable transport ordering."""

    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def canonical_text(value: Any) -> str:
    """Expose complete canonical JSON material without reducing it to a digest."""

    return canonical_json(value).decode("ascii")


CURRENT_OBJECT_TYPES = (
    "agent/v1",
    "agent_loop_resource_grant_authority/v1",
    "bootstrap_command/v1",
    "capability_grant/v1",
    "checkpoint_repair/v1",
    "delivery_boundary_receipt/v1",
    "executable_transition_binding/v1",
    "execution_environment_identity/v1",
    "external_kb_package/v1",
    "external_resource_index/v1",
    "final_result_index/v1",
    "firing_admission/v1",
    "firing_allocation/v1",
    "firing_completion/v2",
    "firing_external_kb_read/v2",
    "firing_timing/v1",
    "invocation/v1",
    "marking_checkpoint/v1",
    "marking_delta/v1",
    "llm_call_spec/v2",
    "llm_call_spec/v3",
    "llm_invocation_attempt/v1",
    "llm_invocation_spec/v1",
    "logical_artifact_slot/v1",
    "main_child_registry_link/v1",
    "main_thread/v1",
    "main_turn/v1",
    "main_turn_execution_receipt/v1",
    "native_genesis_manifest/v1",
    "native_run_identity/v1",
    "net_instance/v1",
    "node_declaration/v1",
    "operation_binding/v1",
    "operation_execution_lease/v1",
    "operation_result/v1",
    "operation_spec/v1",
    "output_binding/v1",
    "petri_token/v1",
    "plan_version/v1",
    "principal/v1",
    "provider_attempt_spec/v1",
    "provider_payload_materialization_receipt/v1",
    "registered_host_llm_attempt/v1",
    "registered_operation_resource_wait/v1",
    "registered_run_inputs/v1",
    "registry_type_catalog/v1",
    "resource_address_binding/v1",
    "resource_delivery/v1",
    "resource_release_witness/v1",
    "resource_access_lifecycle/v1",
    "resource_cycle_evidence/v1",
    "resource_version/v1",
    "route_deposit/v1",
    "run_execution_authority/v1",
    "run_terminal_evidence/v1",
    "semantic_prompt_authority/v1",
    "task/v1",
    "task_branch/v1",
    "task_recovery_manifest/v1",
    "task_round/v1",
    "team_design_root/v1",
    "transition_firing/v1",
    "transition_firing/v2",
    "user_authority_decision/v1",
    "workspace_access_set/v1",
    "workspace_binding/v1",
    "workspace_revision/v1",
    "workspace_write_intent/v1",
)

CURRENT_EVENT_TYPES = (
    "agent_turn_recorded/v2",
    "capability_activated/v1",
    "capability_allowed/v1",
    "capability_denied/v1",
    "capability_issued/v1",
    "capability_revoked/v1",
    "checkpoint_repair_committed/v1",
    "firing_admitted/v1",
    "firing_external_kb_read_recorded/v2",
    "firing_resource_transaction_committed/v1",
    "invocation_started/v1",
    "llm_call_owner_interrupted/v1",
    "marking_checkpoint_committed/v1",
    "live_firing_resource_extension/v1",
    "net_adopted/v1",
    "observed_read/v1",
    "object_version_published/v1",
    "operation_dispatch_reserved/v1",
    "operation_execution_started/v1",
    "operation_terminal_ready/v1",
    "petri_firing_resource_accessed/v1",
    "llm_invocation_attempt_reserved/v1",
    "llm_invocation_failed/v1",
    "llm_invocation_interrupted/v1",
    "llm_invocation_owner_interrupted/v1",
    "llm_invocation_succeeded/v1",
    "llm_invocation_succeeded/v2",
    "llm_response_registered/v1",
    "llm_response_registered/v2",
    "llm_call_registered_host_closed/v1",
    "module_assembly_recorded/v1",
    "provider_attempt_reserved/v1",
    "provider_attempt_dispatch_started/v2",
    "provider_attempt_owner_interrupted/v1",
    "provider_attempt_submission_permitted/v2",
    "provider_attempt_submission_observed/v1",
    "provider_attempt_host_closed/v1",
    "provider_payload_materialization_recorded/v1",
    "relation_published/v1",
    "registered_operation_completion_recorded/v1",
    "registered_host_llm_attempt_closed/v1",
    "registered_host_llm_attempt_reserved/v1",
    "resource_address_bound/v1",
    "resource_address_unbound/v1",
    "resource_delivery_acknowledged/v1",
    "resource_delivery_failed/v1",
    "resource_delivery_prepared/v1",
    "resource_delivery_unknown/v1",
    "resource_release_authorized/v1",
    "structural_growth_adopted/v1",
    "transaction_aborted/v1",
    "transaction_committed/v1",
    "transition_firing_started/v1",
    "transition_firing_settled/v1",
)

CURRENT_RELATION_TYPES = (
    "produced_by",
    "contributed_by",
    "derived_from",
    "read_by",
    "declared_by",
    "reviewed_by",
    "supersedes",
    "granted_by",
    "pinned_import_of",
    "uses_tool_catalog",
    "uses_semantic_prompt",
    "caused_by_tool_result",
    "firing_of_node",
    "firing_in_net",
    "invocation_of_firing",
    "invocation_in_activation",
    "sponsored_under_activation",
    "invocation_as_principal",
    "invocation_uses_agent",
    "invocation_uses_binding",
    "invocation_governed_by_limits",
    "invocation_uses_lease",
    "terminal_result_of_invocation",
    "settled_disposition_documented_by",
    "call_of_invocation",
    "bound_to_backend",
    "uses_transport_contract",
    "attempt_of_call",
    "attempt_consumes_resource",
    "attempt_uses_delivery",
    "governed_by_call_policy",
    "retry_of_attempt",
    "attempt_produced_response",
    "actual_model_call_recorded_against_limit",
)

CURRENT_CONTENT_SCHEMA_REFS = (
    "registry_v1/fact_event_envelope/v1",
    "registry_v1/llm_input_target/v1",
    "registry_v1/mechanical_transition_receipt/v1",
    "registry_v1/object_envelope/v1",
    "registry_v1/resource_wait_policy/v1",
    "registry_v1/typed_relation/v1",
    "rpnh/executable_net/v1",
    "rpnh/module_declaration/v1",
    "rpnh/owner_control/v1",
    "rpnh/resource_access_contract/v1",
    "runtime/llm_request_envelope/v1",
    "runtime/llm_response_envelope/v1",
)

CURRENT_SCHEMA_REFS = tuple(sorted({
    *(f"registry_v1/{name}" for name in CURRENT_OBJECT_TYPES),
    *(f"registry_v1/{name}" for name in CURRENT_EVENT_TYPES),
    *CURRENT_CONTENT_SCHEMA_REFS,
}))

# Protection is by exact reserved mechanical identity, never by namespace or
# role spelling. Agent identity/sponsorship, turn accounting, grant authority,
# checkpoint and provider execution contracts remain mechanical definitions.
PROTECTED_SCHEMA_REFS = frozenset(CURRENT_SCHEMA_REFS)
SCHEMA_ID_PATTERN = r"^[a-z][a-z0-9_]*(?:/[a-z][a-z0-9_]*)+/v[1-9][0-9]*$"
_SCHEMA_ID = re.compile(SCHEMA_ID_PATTERN)


@dataclass(frozen=True, slots=True)
class TypeDefinition:
    name: str
    category: str
    owner: str
    schema_ref: str
    criticality: str | None
    permission: str
    retention: str
    recovery_rule: str
    integrity_rule: str


@dataclass(frozen=True, slots=True, order=True)
class SchemaFileDefinition:
    schema_id: str
    path: str


class SchemaCatalog:
    """One validator authority for mechanical and explicitly supplied schemas."""

    def __init__(
        self, *, schemas: Mapping[str, Mapping[str, Any]] | None = None,
        types: Iterable[TypeDefinition] = (),
        schema_paths: Mapping[str, Path | str] | None = None,
    ) -> None:
        self._definitions: dict[str, TypeDefinition] = {}
        self._validators: dict[str, Any] = {}
        self._registered_schemas: dict[str, dict[str, Any]] = {}
        self._registered_paths: dict[str, Path] = {}
        self._registered_sources: dict[str, str] = {}
        for name in CURRENT_OBJECT_TYPES:
            self.register_type(TypeDefinition(
                name=name,
                category="object",
                owner="registry",
                schema_ref=f"registry_v1/{name}",
                criticality=None,
                permission="task-scoped",
                retention="run-fact",
                recovery_rule="registry-reference-replay",
                integrity_rule="exact-schema-and-reference-validation",
            ))
        for name in CURRENT_EVENT_TYPES:
            self.register_type(TypeDefinition(
                name=name,
                category="event",
                owner="registry",
                schema_ref=f"registry_v1/{name}",
                criticality="authoritative",
                permission="writer-only",
                retention="permanent",
                recovery_rule="fail-closed",
                integrity_rule="exact-schema-and-reference-validation",
            ))
        for name in CURRENT_RELATION_TYPES:
            self.register_type(TypeDefinition(
                name=name,
                category="relation",
                owner="registry",
                schema_ref="registry_v1/typed_relation/v1",
                criticality=None,
                permission="task-scoped",
                retention="permanent",
                recovery_rule="rebuild-forward-reverse",
                integrity_rule="typed-reference-validation",
            ))
        self._schema_index = self._build_schema_index()
        self._verify_repository_index()
        paths = dict(schema_paths or {})
        if set(paths).difference(schemas or {}):
            raise SchemaGovernanceError("schema source path has no explicit document")
        for schema_id, schema in (schemas or {}).items():
            self.register_schema(schema_id, schema, source_path=paths.get(schema_id))
        for definition in types:
            self.register_type(definition)

    @staticmethod
    def _schema_root() -> Path:
        root = Path(__file__).resolve(strict=True).parents[2] / "schemas"
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise SchemaGovernanceError("current Registry schema root is unavailable")
        return root

    @classmethod
    def _mechanical_schema_path(cls, schema_ref: str) -> Path:
        if schema_ref not in CURRENT_SCHEMA_REFS:
            raise SchemaGovernanceError(
                f"schema is not in the registered RPNH catalog: {schema_ref!r}"
            )
        namespace, _, relative = schema_ref.partition("/")
        if not namespace or not relative:
            raise SchemaGovernanceError(
                f"current schema identity is malformed: {schema_ref!r}")
        candidate = (
            cls._schema_root() / namespace /
            f"{relative.replace('/', '.')}.schema.json")
        if candidate.is_symlink():
            raise SchemaGovernanceError(
                f"current Registry schema symlink is forbidden: {candidate}"
            )
        return candidate.resolve(strict=True)

    def schema_path(self, schema_ref: str) -> Path:
        """Return only an exact mechanical or explicitly registered source file."""
        if schema_ref in CURRENT_SCHEMA_REFS:
            return self._mechanical_schema_path(schema_ref)
        try:
            return self._registered_paths[schema_ref]
        except KeyError as exc:
            raise SchemaGovernanceError(
                f"schema has no explicitly registered file source: {schema_ref!r}"
            ) from exc

    @classmethod
    def _build_schema_index(cls) -> tuple[SchemaFileDefinition, ...]:
        records: list[SchemaFileDefinition] = []
        for schema_ref in CURRENT_SCHEMA_REFS:
            path = cls._mechanical_schema_path(schema_ref)
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                check_draft7_schema(document)
            except Exception as exc:
                raise SchemaGovernanceError(
                    f"current Registry schema is invalid: {schema_ref}"
                ) from exc
            if document.get("$id") != schema_ref:
                raise SchemaGovernanceError(
                    f"current Registry schema id differs from its catalog path: {schema_ref}"
                )
            records.append(SchemaFileDefinition(
                schema_id=schema_ref,
                path=path.relative_to(cls._schema_root()).as_posix(),
            ))
        return tuple(sorted(records, key=lambda item: (item.path, item.schema_id)))

    def _verify_repository_index(self) -> None:
        index_path = self._schema_root() / "INDEX.json"
        try:
            document = json.loads(index_path.read_text(encoding="utf-8"))
            entries = document["schemas"]
        except Exception as exc:
            raise SchemaGovernanceError("CPN schema index is missing or invalid") from exc
        if not isinstance(entries, list):
            raise SchemaGovernanceError("CPN schema index schemas must be a list")
        indexed = tuple(
            (entry.get("schema_id"), entry.get("path"))
            for entry in entries
            if isinstance(entry, Mapping)
            and entry.get("schema_id") in CURRENT_SCHEMA_REFS
        )
        expected = tuple((item.schema_id, item.path) for item in self._schema_index)
        if indexed != expected:
            raise SchemaGovernanceError(
                "CPN schema index differs from the registered RPNH catalog"
            )

    def register_type(self, definition: TypeDefinition) -> None:
        if (definition.schema_ref not in CURRENT_SCHEMA_REFS
                and definition.schema_ref not in self._registered_schemas):
            raise SchemaGovernanceError(
                f"type uses a non-current schema: {definition.schema_ref!r}"
            )
        prior = self._definitions.get(definition.name)
        if prior is not None and prior != definition:
            raise SchemaGovernanceError(f"type {definition.name!r} is immutable")
        self._definitions[definition.name] = definition

    def register_schema(
        self, schema_id: str, schema: Mapping[str, Any], *,
        source_path: Path | str | None = None,
    ) -> None:
        """Admit noncore JSON documents; optional file provenance is explicit."""
        if (not isinstance(schema_id, str)
                or _SCHEMA_ID.fullmatch(schema_id) is None
                or schema_id in PROTECTED_SCHEMA_REFS):
            raise SchemaGovernanceError("mechanical/current schema identities are protected")
        if not isinstance(schema, Mapping):
            raise SchemaGovernanceError("registered schema must be an object")
        try:
            document = json.loads(json.dumps(dict(schema), allow_nan=False))
            if (document.get("$id") != schema_id
                    or document.get("$schema")
                    != "http://json-schema.org/draft-07/schema#"):
                raise ValueError("schema identity/dialect mismatch")
            check_draft7_schema(document)
        except Exception as exc:
            raise SchemaGovernanceError("registered application schema is invalid") from exc
        path: Path | None = None
        source = canonical_text(document)
        if source_path is not None:
            path = Path(source_path).resolve(strict=True)
            source = path.read_text(encoding="utf-8")
            if json.loads(source) != document:
                raise SchemaGovernanceError("registered schema source differs from document")
        prior = self._registered_schemas.get(schema_id)
        if prior is not None:
            if prior != document:
                raise SchemaGovernanceError("registered schema identity is immutable")
            if path is not None and self._registered_paths.get(schema_id) != path:
                raise SchemaGovernanceError("registered schema source identity is immutable")
            return
        self._registered_schemas[schema_id] = document
        self._registered_sources[schema_id] = source
        if path is not None:
            self._registered_paths[schema_id] = path
        self._validators[schema_id] = _LinearUniqueDraft7Validator(document)

    def registered_schemas(self) -> dict[str, dict[str, Any]]:
        """Exact pure-data documents for the schema-resource persistence gateway."""
        return json.loads(json.dumps(self._registered_schemas))

    def require(
        self,
        name: str,
        *,
        category: str,
        criticality: str | None = None,
    ) -> TypeDefinition:
        definition = self._definitions.get(name)
        if definition is None or definition.category != category:
            raise SchemaGovernanceError(f"unregistered current {category} type: {name!r}")
        if criticality is not None and definition.criticality != criticality:
            raise SchemaGovernanceError(
                f"criticality mismatch for {name!r}: "
                f"{criticality!r} != {definition.criticality!r}"
            )
        return definition

    def _validator(self, schema_ref: str) -> Any:
        if (schema_ref not in CURRENT_SCHEMA_REFS
                and schema_ref not in self._registered_schemas):
            raise SchemaGovernanceError(
                f"schema is not in the registered RPNH catalog: {schema_ref!r}"
            )
        validator = self._validators.get(schema_ref)
        if validator is not None:
            return validator
        try:
            document = json.loads(self.schema_path(schema_ref).read_text(encoding="utf-8"))
            check_draft7_schema(document)
        except Exception as exc:
            raise SchemaGovernanceError(
                f"invalid current Registry schema {schema_ref!r}"
            ) from exc
        validator = _LinearUniqueDraft7Validator(document)
        self._validators[schema_ref] = validator
        return validator

    @staticmethod
    def _format_validation_error(error: Any) -> str:
        path = ".".join(str(part) for part in error.absolute_path) or "<root>"
        return f"{path}: {error.message}"

    def validate_schema_ref(self, schema_ref: str, instance: Any) -> None:
        candidate = dict(instance) if isinstance(instance, Mapping) else instance
        errors = sorted(
            self._validator(schema_ref).iter_errors(candidate),
            key=lambda item: (list(item.absolute_path), item.message),
        )
        if errors:
            details = "; ".join(
                self._format_validation_error(item) for item in errors[:8]
            )
            raise SchemaGovernanceError(
                f"instance violates current schema {schema_ref!r}: {details}"
            )

    def validate_instance(
        self,
        name: str,
        *,
        category: str,
        instance: Mapping[str, Any],
        criticality: str | None = None,
    ) -> None:
        definition = self.require(
            name,
            category=category,
            criticality=criticality,
        )
        self.validate_schema_ref(definition.schema_ref, instance)

    def validate_event_payload(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        criticality: str | None = "authoritative",
    ) -> None:
        if payload is None:
            raise SchemaGovernanceError("event payload is required")
        self.validate_instance(
            event_type,
            category="event",
            criticality=criticality,
            instance=payload,
        )

    def validate_fact_envelope(self, envelope: Mapping[str, Any]) -> None:
        """Validate one persisted event envelope against the current contract."""

        self.validate_schema_ref(
            "registry_v1/fact_event_envelope/v1", envelope)

    def definitions(self) -> tuple[TypeDefinition, ...]:
        return tuple(sorted(
            self._definitions.values(),
            key=lambda item: (item.category, item.name),
        ))

    def schema_index(self) -> tuple[SchemaFileDefinition, ...]:
        records = list(self._schema_index)
        for schema_id in self._registered_schemas:
            path = self._registered_paths.get(schema_id)
            if path is not None:
                try:
                    source_path = path.relative_to(self._schema_root()).as_posix()
                except ValueError:
                    source_path = path.as_posix()
            else:
                # Document-only HOST registration has no file provenance.
                source_path = ""
            records.append(SchemaFileDefinition(schema_id, source_path))
        return tuple(sorted(records, key=lambda item: (item.path, item.schema_id)))

    def bundle(self) -> dict[str, Any]:
        """Return the complete current catalog without digest authority."""

        schemas: dict[str, dict[str, Any]] = {}
        for record in self.schema_index():
            if record.schema_id in self._registered_sources:
                source = self._registered_sources[record.schema_id]
            else:
                source = self.schema_path(record.schema_id).read_text(encoding="utf-8")
            schemas[record.schema_id] = {
                "schema_id": record.schema_id,
                "path": record.path,
                "source": source,
            }
        return {
            "catalog_identity": "rpnh-v1",
            "definitions": [asdict(item) for item in self.definitions()],
            "schema_index": [asdict(item) for item in self.schema_index()],
            "schemas": schemas,
        }
