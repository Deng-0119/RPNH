"""Skill document data over original OwnerInput, PublishResource and Registry reads.

A caller supplies selected bytes and exact original resource references. The
adapter publishes typed products and reads through existing firing authority;
the original owner/operation still settles PN Success. No installation, automatic
discovery, grant issuance or script execution is performed. Only UTF-8 text
members are supported.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat
import unicodedata
from collections.abc import Mapping

from jsonschema import Draft7Validator

from cpn.rpnh.agent_workflows import TEXT_SCHEMA
from cpn.rpnh.material_types import decode_agent_text, encode_agent_text
from cpn.rpnh.run import OwnerInput, RunOwner
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.operations import OperationExecutionAuthority
from cpn.rpnh.registry.resources import (
    PetriOutputOrigin, PublishResource, ResourceVersionRef,
)
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.collaboration.references import SourceQualifiedResourceRef
from cpn.rpnh.collaboration.registry_read_contracts import (
    IndexQuery, TypedIndexClause, TypedPredicate,
)

SKILL_INDEX_SCHEMA = "application/rpnh_skill_index/v1"
_ALLOWED_FIELDS = frozenset({
    "name", "description", "license", "compatibility", "metadata", "allowed-tools",
})


class SkillMaterialError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SkillDocument:
    root_name: str
    text: str
    body: str
    metadata_bytes: bytes

    @property
    def metadata(self):
        return json.loads(self.metadata_bytes)


def _metadata(value, root_name):
    if (type(value) is not dict or set(value) - _ALLOWED_FIELDS
            or not {"name", "description"} <= set(value)):
        raise SkillMaterialError("unsupported Skill frontmatter fields")
    name = value["name"]
    if type(name) is not str or name != name.strip() or not name:
        raise SkillMaterialError("Skill name must be a nonempty string")
    normalized = unicodedata.normalize("NFKC", name)
    if (len(normalized) > 64 or normalized != normalized.lower()
            or normalized.startswith("-") or normalized.endswith("-")
            or "--" in normalized
            or not all(c.isalnum() or c == "-" for c in normalized)):
        raise SkillMaterialError("invalid Skill name")
    if (type(root_name) is not str
            or unicodedata.normalize("NFKC", root_name) != normalized):
        raise SkillMaterialError("Skill name differs from selected directory")
    description = value["description"]
    if (type(description) is not str or not description.strip()
            or len(description) > 1024):
        raise SkillMaterialError("invalid Skill description")
    for key in ("license", "compatibility", "allowed-tools"):
        if key in value and type(value[key]) is not str:
            raise SkillMaterialError("Skill optional text field is not text")
    if "compatibility" in value and (
            not value["compatibility"].strip() or len(value["compatibility"]) > 500):
        raise SkillMaterialError("invalid Skill compatibility")
    if "metadata" in value and (
            type(value["metadata"]) is not dict
            or any(type(k) is not str or type(v) is not str
                   for k, v in value["metadata"].items())):
        raise SkillMaterialError("Skill metadata must map strings to strings")
    return json.loads(canonical_json(value))


def relative_member(name):
    """Validate an exact package-relative member; never normalize or open it."""
    if (type(name) is not str or not name or name.startswith("/")
            or "\\" in name or ":" in name
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or any(part in ("", ".", "..") for part in name.split("/"))):
        raise SkillMaterialError("member is not an exact relative path")
    return name


def parse_skill_document(payload, *, root_name):
    """Parse selected bytes with the reference implementation's YAML library.

    StrictYAML intentionally rejects aliases, tags, duplicate keys and flow
    collections. This is a declared support subset, not general YAML support.
    Body bytes are never executed or recursively interpreted as instructions.
    """
    if type(payload) is not bytes:
        raise TypeError("Skill source must be explicit immutable bytes")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SkillMaterialError("Skill source is not UTF-8") from exc
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise SkillMaterialError("Skill source requires YAML frontmatter")
    closing = next((i for i in range(1, len(lines))
                    if lines[i].rstrip("\r\n") == "---"), None)
    if closing is None:
        raise SkillMaterialError("Skill frontmatter is not closed")
    try:
        import strictyaml
    except ImportError as exc:
        raise SkillMaterialError("Skill parser dependency unavailable: strictyaml") from exc
    try:
        values = strictyaml.load("".join(lines[1:closing])).data
    except strictyaml.YAMLError as exc:
        raise SkillMaterialError("invalid or unsupported Skill YAML") from exc
    metadata = _metadata(values, root_name)
    return SkillDocument(root_name, text, "".join(lines[closing + 1:]),
                         canonical_json(metadata))


def prepare_member_inputs(document, members, entry_ports):
    """Prepare original OwnerInput values for an explicitly declared Module.

    This does not start a run or publish. Every selected text member needs one
    explicit original entry binding. Scripts remain text, with no execute bit,
    callable, import locator, tool grant or dependency installation.
    """
    if not isinstance(document, SkillDocument):
        raise TypeError("a parsed Skill document is required")
    if not isinstance(members, Mapping) or not isinstance(entry_ports, Mapping):
        raise TypeError("members and entry ports must be explicit mappings")
    if set(members) != set(entry_ports) or "SKILL.md" not in members:
        raise SkillMaterialError("selected members and entry ports differ")
    if (any(type(port) is not str or not port for port in entry_ports.values())
            or len(set(entry_ports.values())) != len(entry_ports)):
        raise SkillMaterialError("entry bindings must be unique explicit names")
    if members["SKILL.md"] != document.text.encode("utf-8"):
        raise SkillMaterialError("selected SKILL.md differs from parsed document")
    result = {}
    for path, payload in members.items():
        relative_member(path)
        if type(payload) is not bytes:
            raise TypeError("selected text members must be bytes")
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise SkillMaterialError("binary member is not supported by text import") from exc
        result[entry_ports[path]] = OwnerInput(
            TEXT_SCHEMA, encode_agent_text(text),
            "Skill " + document.metadata["name"] + " / " + path,
        )
    return result


def prepare_directory_member_inputs(directory, entry_ports, *, max_bytes):
    """Read an explicit directory selection into original OwnerInput values.

    ``directory`` must be absolute and ``entry_ports`` names every selected
    relative text member, including SKILL.md. No directory is scanned. Every
    path component is opened without following symlinks; only regular files are
    accepted. ``max_bytes`` bounds the total accepted source bytes, including
    SKILL.md; at most one extra probe byte is read to detect growth past the
    bound. A zero bound permits no nonempty material. Platforms without the
    required descriptor-relative no-follow operations are rejected.

    Returns (SkillDocument, entry-input mapping). The caller still supplies the
    declared Module and uses original start_run/publication/Success. This HOST
    preparation neither registers resources nor executes selected script text.
    It imports the bytes read from each open file, not an atomic directory
    snapshot or a promise that the source files will remain unchanged.
    """
    if not isinstance(entry_ports, Mapping):
        raise TypeError("selected Skill members require an explicit entry-port mapping")
    entry_ports = dict(entry_ports)
    if ("SKILL.md" not in entry_ports
            or any(type(port) is not str or not port for port in entry_ports.values())
            or len(set(entry_ports.values())) != len(entry_ports)):
        raise SkillMaterialError("selected members require unique explicit entry ports and SKILL.md")
    paths = tuple(relative_member(name) for name in entry_ports)
    if type(max_bytes) is not int or max_bytes < 0:
        raise SkillMaterialError("Skill directory byte limit must be a nonnegative integer")
    root = Path(directory)
    if (not root.is_absolute() or root.anchor != os.sep or len(root.parts) < 2
            or ".." in root.parts):
        raise SkillMaterialError("Skill directory must be an explicit absolute path")
    if (os.open not in os.supports_dir_fd
            or any(not hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"))):
        raise SkillMaterialError("Skill directory import requires descriptor-relative no-follow access")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    remaining = max_bytes

    def opened(stack, path, flags, *, directory_fd=None):
        descriptor = os.open(path, flags, dir_fd=directory_fd)
        stack.callback(os.close, descriptor)
        return descriptor

    try:
        with ExitStack() as directories:
            root_fd = opened(directories, root.anchor, directory_flags)
            for part in root.parts[1:]:
                root_fd = opened(directories, part, directory_flags, directory_fd=root_fd)

            def read_member(name):
                nonlocal remaining
                with ExitStack() as opened_members:
                    parent = root_fd
                    parts = name.split("/")
                    for part in parts[:-1]:
                        parent = opened(opened_members, part, directory_flags, directory_fd=parent)
                    descriptor = opened(opened_members, parts[-1], file_flags, directory_fd=parent)
                    info = os.fstat(descriptor)
                    if not stat.S_ISREG(info.st_mode):
                        raise SkillMaterialError("selected Skill member must be a regular file")
                    if info.st_size > remaining:
                        raise SkillMaterialError("selected Skill source exceeds the total byte limit")
                    payload = bytearray()
                    while True:
                        chunk = os.read(descriptor, min(65536, remaining - len(payload) + 1))
                        if not chunk:
                            break
                        payload.extend(chunk)
                        if len(payload) > remaining:
                            raise SkillMaterialError("selected Skill source exceeds the total byte limit")
                    remaining -= len(payload)
                    return bytes(payload)

            members = {"SKILL.md": read_member("SKILL.md")}
            document = parse_skill_document(members["SKILL.md"], root_name=root.name)
            for path in paths:
                if path != "SKILL.md":
                    members[path] = read_member(path)
            return document, prepare_member_inputs(document, members, entry_ports)
    except OSError as exc:
        raise SkillMaterialError("selected Skill directory or member could not be opened without following links") from exc


def skill_index_schema():
    """An optional application schema, registered through original Registration."""
    source_ref = {
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "source_id", "ref"],
        "properties": {
            "schema_version": {"const": "rpnh/collaboration/source_resource_ref/v1"},
            "source_id": {"type": "string", "minLength": 1},
            "ref": {
                "type": "object", "additionalProperties": False,
                "required": ["resource_id", "resource_version_id"],
                "properties": {
                    "resource_id": {"type": "string", "minLength": 1},
                    "resource_version_id": {"type": "string", "minLength": 1},
                },
            },
        },
    }
    metadata = {
        "type": "object", "additionalProperties": False,
        "required": ["name", "description"],
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 64},
            "description": {"type": "string", "minLength": 1, "maxLength": 1024},
            "license": {"type": "string"},
            "compatibility": {"type": "string", "minLength": 1, "maxLength": 500},
            "allowed-tools": {"type": "string"},
            "metadata": {"type": "object", "additionalProperties": {"type": "string"}},
        },
    }
    return {
        "$id": SKILL_INDEX_SCHEMA,
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "metadata", "members"],
        "properties": {
            "schema_version": {"const": SKILL_INDEX_SCHEMA},
            "metadata": metadata,
            "members": {
                "type": "object", "required": ["SKILL.md"],
                "additionalProperties": source_ref,
            },
        },
    }


def register_skill_schema(registration):
    registration.register_schema(SKILL_INDEX_SCHEMA, skill_index_schema())


def _resource_ref(value):
    try:
        inner = value["ref"]
        ref = SourceQualifiedResourceRef(
            value["source_id"], ResourceVersionRef(
                TypedId.parse(inner["resource_id"], expected="resource"),
                TypedId.parse(inner["resource_version_id"], expected="resource_version"),
            ),
        )
        if ref.to_dict() != value:
            raise ValueError("reference has extra or different fields")
        return ref
    except (KeyError, TypeError, ValueError) as exc:
        raise SkillMaterialError("invalid exact source-qualified member reference") from exc


def _validate_index(value):
    try:
        Draft7Validator(skill_index_schema()).validate(value)
    except Exception as exc:
        raise SkillMaterialError("invalid Skill index document") from exc
    metadata = _metadata(value["metadata"], value["metadata"]["name"])
    sources = set()
    for name, wire_ref in value["members"].items():
        relative_member(name)
        sources.add(_resource_ref(wire_ref).source_id)
    if len(sources) != 1:
        raise SkillMaterialError("Skill members must use one explicit source")
    return {"schema_version": SKILL_INDEX_SCHEMA, "metadata": metadata,
            "members": json.loads(canonical_json(value["members"]))}


def make_skill_index(document, member_refs):
    """Build inert index data after original import returned exact resource refs."""
    if not isinstance(document, SkillDocument) or not isinstance(member_refs, Mapping):
        raise TypeError("parsed document and exact member mapping are required")
    if any(type(ref) is not SourceQualifiedResourceRef for ref in member_refs.values()):
        raise TypeError("member mapping requires exact source-qualified resources")
    return _validate_index({
        "schema_version": SKILL_INDEX_SCHEMA,
        "metadata": document.metadata,
        "members": {name: ref.to_dict() for name, ref in member_refs.items()},
    })


def _index_publication(
        owner, execution, index, *, output_port_id, outcome_id, idempotency_key):
    """Prepare one original typed product; original Registry and PN still settle it.

    The selected Module must already declare this real product port/schema.
    The local source is read from original immutable Registry owner facts,
    never accepted as a caller-supplied display name or source alias.
    """
    if any(type(v) is not str or not v for v in (
            output_port_id, outcome_id, idempotency_key)):
        raise SkillMaterialError("Skill publication requires exact output choices")
    source_id = _local_source(owner)
    index = _validate_index(index)
    members = tuple(_resource_ref(value) for value in index["members"].values())
    if any(member.source_id != source_id for member in members):
        raise SkillMaterialError("member source differs from actual local Registry")
    admitted = {item.resource_ref: item for item in execution.operation.inputs
                if item.projected_payload is None
                and item.source_resource_ref is None
                and item.substituted_content_schema_id is None}
    if any(member.ref not in admitted for member in members):
        raise SkillMaterialError("Skill publication members must be exact admitted inputs")
    kernel, _repository = owner.operation_repository()
    for ref in dict.fromkeys(member.ref for member in members):
        _full_input_header(owner._core, kernel, execution,
                           admitted[ref], schema=TEXT_SCHEMA)
    context = execution.operation.canonical.context
    bindings = tuple(binding for binding in
        execution.operation.operation_binding.output_port_bindings
        if binding.port_id == output_port_id)
    if len(bindings) != 1 or bindings[0].content_schema.schema_id != SKILL_INDEX_SCHEMA:
        raise SkillMaterialError("Skill index requires its original typed output port")
    binding, = bindings
    return PublishResource(
        origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref),
        payload=canonical_json(index), media_type="application/json",
        content_schema_ref=SKILL_INDEX_SCHEMA,
        content_schema_authority_ref=binding.content_schema.content_schema_ref,
        summary="Skill " + index["metadata"]["name"] + ": " + index["metadata"]["description"],
        lifetime_ref=context.invocation_ref,
        derived_from=tuple(dict.fromkeys(member.ref for member in members)),
        descriptors={"output_outcome_id": outcome_id,
                     "output_port_id": binding.port_id, "place": binding.place},
        idempotency_key=idempotency_key,
    )


def prepare_index_publication(
        owner, execution, index, *, output_port_id, outcome_id, idempotency_key):
    """Prepare the original typed product command without publishing it."""
    _kernel, _repository, execution = _owner_execution(owner, execution)
    return _index_publication(owner, execution, index,
        output_port_id=output_port_id, outcome_id=outcome_id,
        idempotency_key=idempotency_key)


def skill_index_query(source_ids, *, page_size=100, cuts=None):
    """Discover through the existing typed index; no body reads or Registry writes."""
    return IndexQuery(tuple(source_ids), (TypedIndexClause(
        "resource_version/v1",
        predicates=(TypedPredicate("content_schema_ref", "eq", SKILL_INDEX_SCHEMA),),
        projection=("summary", "content_schema_ref", "byte_count"),
    ),), page_size=page_size, cuts=cuts)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SkillMaterialError("duplicate Skill index JSON field")
        result[key] = value
    return result


def read_skill_index(session, index_ref, *, at_cut, max_bytes=None):
    """Read only authorized metadata/index bytes, not all package members."""
    if type(index_ref) is not SourceQualifiedResourceRef:
        raise TypeError("Skill index requires an exact source-qualified reference")
    result = session.read_material(resource_ref=index_ref, at_cut=at_cut,
        max_bytes=max_bytes, representation="bytes", include_digest=False)
    if (result["resource_ref"] != index_ref.to_dict()
            or result["content_schema_ref"] != SKILL_INDEX_SCHEMA
            or result["media_type"] != "application/json"):
        raise SkillMaterialError("resource is not the exact typed Skill index")
    try:
        value = json.loads(result["bytes"], object_pairs_hook=_unique_object)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SkillMaterialError("invalid Skill index JSON") from exc
    index = _validate_index(value)
    if any(_resource_ref(value).source_id != index_ref.source_id
           for value in index["members"].values()):
        raise SkillMaterialError("Skill index points outside its imported source")
    return index


def read_skill_member(session, index_ref, member, *, at_cut, max_bytes=None):
    """Read one listed immutable member using its own current original authority."""
    member = relative_member(member)
    index = read_skill_index(session, index_ref, at_cut=at_cut, max_bytes=max_bytes)
    if member not in index["members"]:
        raise SkillMaterialError("member is outside the imported Skill selection")
    ref = _resource_ref(index["members"][member])
    result = session.read_material(resource_ref=ref, at_cut=at_cut,
        max_bytes=max_bytes, representation="bytes", include_digest=False)
    if result["resource_ref"] != ref.to_dict():
        raise SkillMaterialError("reader returned a different exact member")
    text = decode_agent_text(result["bytes"],
        content_schema_ref=result["content_schema_ref"], media_type=result["media_type"])
    if member == "SKILL.md":
        document = parse_skill_document(text.encode("utf-8"),
                                       root_name=index["metadata"]["name"])
        if document.metadata != index["metadata"]:
            raise SkillMaterialError("Skill index metadata differs from exact SKILL.md")
    return {"resource_ref": ref.to_dict(), "source_cut": result["source_cut"],
            "member": member, "text": text}




def _owner_execution(owner, execution):
    """Use original durable Start and rehydrate consumer-facing projections."""
    from cpn.rpnh.registry.operation_execution import verify_operation_execution
    from cpn.rpnh.registry.operations import hydrate_operation_authority

    if (not isinstance(owner, RunOwner)
            or not isinstance(execution, OperationExecutionAuthority)):
        raise TypeError("Skill operation requires original owner and operation execution")
    kernel, repository = owner.operation_repository()
    verify_operation_execution(owner._core, kernel, repository, execution)
    # The original verifier validates the Start but returns its supplied wrapper.
    # Only original hydration supplies consumer payloads and canonical context.
    current = hydrate_operation_authority(repository,
        input_plan=execution.operation.input_plan,
        petri_inputs=tuple(item.artifact for item in execution.operation.inputs))
    return kernel, repository, replace(execution, operation=current)


def _local_source(owner):
    from cpn.rpnh.registry._event_store.branch_publication import _local_binding

    with owner._core.event_store.connect() as db:
        db.execute("BEGIN")
        return _local_binding(db, owner._core.catalog, owner._core.task_id,
                              owner._core.object_store)["source_id"]


def publish_skill_index(
        owner, execution, index, *, output_port_id, outcome_id, command_id):
    """Publish one original typed Skill product and register its output bundle.

    The caller settles the returned original outputs with owner.succeed().
    Until that succeeds, the product remains private to its original firing.
    No new state store, completion claim or Registry write path is introduced.
    """
    from cpn.rpnh.registry.operations import register_operation_outputs
    from cpn.rpnh.registry.resource_verification import verify_resource

    kernel, repository, execution = _owner_execution(owner, execution)
    command = _index_publication(owner, execution, index,
        output_port_id=output_port_id, outcome_id=outcome_id,
        idempotency_key=command_id + ":product")
    resource = kernel.publish_bytes(execution.operation.canonical.context, command)
    artifact = verify_resource(
        owner._core, kernel, execution.operation.canonical, resource)
    return register_operation_outputs(repository, execution, (artifact,),
        selected_outcome_id=outcome_id, idempotency_key=command_id + ":outputs")


def _full_input_header(core, kernel, execution, item, *, schema):
    """Close original full-input facts before a selected registered body read.

    Live/historical inputs retain the original repository verifier. Settled
    hydration only compares caller-held labels and bytes; its receipt/header
    must also match the original durable facts before using full body access.
    """
    from cpn.rpnh.registry.publication import _ref_payload
    from cpn.rpnh.registry.resource_verification import verify_petri_input_receipt
    from cpn.rpnh.registry.resources import ResourceDeliveryReceipt, SettledPetriInputArtifact

    message = "Skill requires exact full input backed by its original acknowledgment"
    artifact = item.artifact
    header = artifact.resource.header
    count = artifact.receipt.positive_byte_count
    if (item.projected_payload is not None
            or item.source_resource_ref is not None
            or item.substituted_content_schema_id is not None
            or type(count) is not int or count <= 0
            or type(header.byte_size) is not int
            or count != header.byte_size):
        raise SkillMaterialError(message)
    if isinstance(artifact, SettledPetriInputArtifact):
        context = execution.operation.canonical.context
        terminal = kernel._exact_object(
            artifact.receipt.terminal_delivery_ref,
            expected_type="resource_delivery/v1")
        if (artifact.operation_start_event_id != execution.start_event_id
                or terminal.metadata.get("context_ref")
                != _ref_payload(context.invocation_ref)):
            raise SkillMaterialError(message)
        receipt = verify_petri_input_receipt(
            core, kernel, ResourceDeliveryReceipt(
                delivery_ref=artifact.receipt.terminal_delivery_ref,
                terminal_event_id=artifact.receipt.terminal_event_id,
                outcome="acknowledged",
                observed_read_event_id=artifact.receipt.observed_read_event_id),
            item.resource_ref)
        actual_header = kernel._firing_header(context, item.resource_ref)
        if (replace(receipt, verified_at_head=artifact.receipt.verified_at_head)
                != artifact.receipt
                or replace(actual_header,
                    relation_kinds=header.relation_kinds,
                    published_at_head=header.published_at_head) != header
                or type(receipt.positive_byte_count) is not int
                or receipt.positive_byte_count <= 0
                or receipt.positive_byte_count != actual_header.byte_size):
            raise SkillMaterialError(message)
        header = actual_header
    if (header.content_schema_ref != schema
            or header.media_type != "application/json"):
        raise SkillMaterialError("active Skill input has the wrong registered content type")
    return header


def _firing_material(kernel, execution, ref, *, core, source_id, authorization_ref,
                     command_id, schema, max_bytes):
    """Consume exact original input bytes or one original monitored release."""
    from cpn.rpnh.registry.models import VersionRef
    from cpn.rpnh.registry.resources import (
        AcknowledgeResourceDelivery, AuthorizeResourceRelease,
        PrepareResourceDelivery,
    )

    if type(ref) is not SourceQualifiedResourceRef:
        raise TypeError("active Skill reading requires an exact source-qualified reference")
    if ref.source_id != source_id:
        raise SkillMaterialError("active Skill resource differs from actual local Registry")
    if (type(command_id) is not str or not command_id
            or (max_bytes is not None
                and (type(max_bytes) is not int or max_bytes < 0))):
        raise SkillMaterialError("active Skill read requires a command and nonnegative byte limit")
    context = execution.operation.canonical.context
    authorization_ref = (context.operation_binding_ref
                         if authorization_ref is None else authorization_ref)
    if type(authorization_ref) is not VersionRef:
        raise TypeError("Skill authorization requires an original binding or capability reference")

    # Reuse original full acknowledged input authority, not caller-held bytes.
    # The visibility-only registered read below cannot replace this gate.
    inputs = tuple(item for item in execution.operation.inputs
                   if item.resource_ref == ref.ref)
    if inputs and authorization_ref == context.operation_binding_ref:
        if len(inputs) != 1:
            raise SkillMaterialError("Skill requires one exact full input")
        item = inputs[0]
        header = _full_input_header(core, kernel, execution, item, schema=schema)
        if max_bytes is not None and header.byte_size > max_bytes:
            raise SkillMaterialError("active Skill input exceeds the selected byte limit")
        payload = kernel._read_firing_registered(context, item.resource_ref)
        return payload, item.artifact.receipt

    # A visible index never grants its members. The original delivery gate
    # checks the selected existing binding/capability, availability and firing.
    registered = kernel._firing_prepared(context, ref.ref)
    if (registered.metadata.get("content_schema_ref") != schema
            or registered.media_type != "application/json"):
        raise SkillMaterialError("active Skill resource has the wrong registered content type")
    if max_bytes is not None and registered.size > max_bytes:
        raise SkillMaterialError("active Skill resource exceeds the selected byte limit")
    prepared = kernel.prepare_delivery(context, PrepareResourceDelivery(
        ref.ref, authorization_ref, new_id("resource_delivery"),
        command_id + ":prepare", "tool_result", "selected Skill material"))
    release = kernel.authorize_release(context, AuthorizeResourceRelease(
        prepared.delivery_ref, "tool_result", new_id("release_nonce"),
        command_id + ":release"))
    payload = kernel._consume_authorized_release(context, release)
    boundary = kernel._record_boundary_receipt(context, release,
        outcome="acknowledged", positive_byte_count=len(payload),
        consumer_evidence="Skill reader consumed exact registered text bytes",
        idempotency_key=command_id + ":boundary")
    receipt = kernel.acknowledge_delivery(context, AcknowledgeResourceDelivery(
        release.delivery_ref, boundary, "acknowledged", command_id + ":acknowledge"))
    return payload, receipt


def _decoded_index(payload, index_ref):
    try:
        value = json.loads(payload, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SkillMaterialError("invalid Skill index JSON") from exc
    index = _validate_index(value)
    if any(_resource_ref(item).source_id != index_ref.source_id
           for item in index["members"].values()):
        raise SkillMaterialError("Skill index points outside its imported source")
    return index


def read_firing_skill_index(
        owner, execution, index_ref, *, command_id,
        authorization_ref=None, max_bytes=None):
    """Read Skill metadata for one active original operation.

    Existing admitted inputs reuse their original acknowledged bytes. Other
    resources require their own existing binding/capability and monitored
    delivery. This API neither issues grants nor executes Skill text.
    """
    kernel, _repository, execution = _owner_execution(owner, execution)
    payload, receipt = _firing_material(kernel, execution, index_ref,
        core=owner._core, source_id=_local_source(owner),
        authorization_ref=authorization_ref, command_id=command_id,
        schema=SKILL_INDEX_SCHEMA, max_bytes=max_bytes)
    return {"resource_ref": index_ref.to_dict(),
            "index": _decoded_index(payload, index_ref), "receipt": receipt}


def read_firing_skill_member(
        owner, execution, index_ref, member, *, command_id,
        index_authorization_ref=None, member_authorization_ref=None,
        max_bytes=None):
    """Read one exact selected member using this firing's original authority.

    Each non-input delivery consumes one original release and returns its real
    receipt. Reusing a command does not retry or reconstruct an uncertain
    delivery; original Registry recovery remains responsible for that case.
    SKILL.md metadata checking retains the explicit StrictYAML dependency.
    """
    member = relative_member(member)
    kernel, _repository, execution = _owner_execution(owner, execution)
    source_id = _local_source(owner)
    payload, index_receipt = _firing_material(kernel, execution, index_ref,
        core=owner._core, source_id=source_id,
        authorization_ref=index_authorization_ref, command_id=command_id + ":index",
        schema=SKILL_INDEX_SCHEMA, max_bytes=max_bytes)
    index = _decoded_index(payload, index_ref)
    if member not in index["members"]:
        raise SkillMaterialError("member is outside the imported Skill selection")
    ref = _resource_ref(index["members"][member])
    payload, member_receipt = _firing_material(kernel, execution, ref,
        core=owner._core, source_id=source_id,
        authorization_ref=member_authorization_ref, command_id=command_id + ":member",
        schema=TEXT_SCHEMA, max_bytes=max_bytes)
    text = decode_agent_text(payload, content_schema_ref=TEXT_SCHEMA,
                             media_type="application/json")
    if member == "SKILL.md":
        document = parse_skill_document(text.encode("utf-8"),
                                       root_name=index["metadata"]["name"])
        if document.metadata != index["metadata"]:
            raise SkillMaterialError("Skill index metadata differs from exact SKILL.md")
    return {"resource_ref": ref.to_dict(), "member": member, "text": text,
            "index_receipt": index_receipt, "receipt": member_receipt}

__all__ = (
    "SKILL_INDEX_SCHEMA", "SkillMaterialError", "SkillDocument",
    "relative_member", "parse_skill_document", "prepare_member_inputs",
    "prepare_directory_member_inputs",
    "skill_index_schema", "register_skill_schema", "make_skill_index",
    "prepare_index_publication", "skill_index_query", "read_skill_index",
    "read_skill_member", "publish_skill_index", "read_firing_skill_index",
    "read_firing_skill_member",
)


