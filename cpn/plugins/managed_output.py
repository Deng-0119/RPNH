"""Exact pages over ordinary, settled managed-operation result products.

The output product is a projection of original managed receipts.  Its ordinary
Petri publication and Success closure, and the consumer's registered resource
read contract, remain the authority.  Internal invocation receipts are never
used as a substitute for a committed operation product.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import re


MANAGED_OUTPUT_READER = "read_managed_output"
MANAGED_OPERATION_OUTPUT_TOOL = "rpnh/managed-operation-output-reader/v1"
MANAGED_OPERATION_OUTPUT_IDENTITY = {
    "implementation_id": "rpnh.managed_operation_output_reader", "revision": "v1"}
MANAGED_OPERATION_OUTPUT_CONTRACTS = {
    "binding_protocol": MANAGED_OPERATION_OUTPUT_TOOL, "effect": "pure"}
MANAGED_OPERATION_RESULTS_SCHEMA = "application/rpnh_managed_operation_results/v1"
MANAGED_OPERATION_RESULTS_KIND = "managed_operation_results/v1"
MANAGED_OPERATION_OUTPUT_PAGE_KIND = "managed_operation_output_page/v1"
MANAGED_OPERATION_ARCHIVE_PAGE_KIND = "managed_operation_result_archive_page/v1"
MANAGED_OPERATION_ARCHIVE_MAX_ENTRIES = 16
MANAGED_OPERATION_RESULTS_EXPORT_TOOL = "rpnh/managed-operation-result-exporter/v1"
MANAGED_OPERATION_RESULTS_EXPORT_IDENTITY = {
    "implementation_id": "rpnh.managed_operation_result_exporter", "revision": "v1"}
MANAGED_OPERATION_RESULTS_EXPORT_CONTRACTS = {
    "binding_protocol": MANAGED_OPERATION_RESULTS_EXPORT_TOOL, "effect": "pure"}
MANAGED_OPERATION_RESULT_DELIVERY_SCHEMA = "application/rpnh_managed_operation_result_delivery/v1"
MANAGED_OPERATION_RESULT_DELIVERY_KIND = "managed_operation_result_delivery/v1"
MANAGED_OPERATION_RESULT_IMPORT_SCHEMA = "application/rpnh_managed_operation_result_import/v1"
MANAGED_OPERATION_RESULT_IMPORT_KIND = "managed_operation_result_import/v1"
MANAGED_OPERATION_IMPORT_OUTPUT_PAGE_KIND = "managed_operation_import_output_page/v1"
MANAGED_OPERATION_IMPORT_ARCHIVE_PAGE_KIND = "managed_operation_import_archive_page/v1"
MANAGED_OPERATION_IMPORT_READ_SCOPE = "registered_input_copy"


class ManagedOperationOutputReadDenied(ValueError):
    """Known read-only request rejection; no business invocation occurred."""


def serialized_managed_json(value):
    """The original AgentLoop exact-output serialization, shared unchanged."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def fit_managed_output_page(page, max_bytes):
    """Fit the complete JSON envelope, preserving exact character offsets.

    Entry points validate their own locator/page shape before calling this
    shared pure algorithm.  Its error and EOF behavior match the original
    AgentLoop reader.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("managed output max_bytes must be a positive integer")
    content = page["content"]

    def candidate(count):
        end = page["offset_chars"] + count
        return dict(page, content=content[:count],
                    next_offset_chars=end if end < page["total_chars"] else None,
                    truncated=end < page["total_chars"])

    def fits(value):
        return len(serialized_managed_json(value).encode("utf-8")) <= max_bytes

    if fits(page):
        return dict(page)
    # EOF's null continuation differs in size from an integer. Partial-page
    # sizes are monotone; test the complete page separately, as before.
    low, high = 0, len(content) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if fits(candidate(middle)):
            low = middle
        else:
            high = middle - 1
    value = candidate(low)
    if not fits(value) or (not low and page["offset_chars"] < page["total_chars"]):
        raise ValueError("managed output budget cannot fit the minimum locator envelope and one character")
    return value


def fit_result_archive_page(page, max_bytes):
    """The original complete-entry directory budget, shared by both readers."""
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("result archive max_bytes must be a positive integer")
    for count in range(len(page["entries"]), -1, -1):
        end = page["offset"] + count
        candidate = dict(page, entries=page["entries"][:count],
                         next_offset=end if end < page["total_count"] else None)
        if len(serialized_managed_json(candidate).encode("utf-8")) <= max_bytes:
            if count or end == page["total_count"]:
                return candidate
    raise ValueError("result archive budget cannot fit the minimum envelope and next complete entry")


def _ref_schema(entity_type, logical_kind, version_kind):
    return {
        "type": "object", "additionalProperties": False,
        "required": ["entity_type", "logical_id", "version_id"],
        "properties": {
            "entity_type": {"const": entity_type},
            "logical_id": {"type": "string", "pattern": f"^{logical_kind}:[a-f0-9]{{32}}$"},
            "version_id": {"type": "string", "pattern": f"^{version_kind}:[a-f0-9]{{32}}$"},
        },
    }


_EXECUTION_REF_SCHEMA = _ref_schema("operation_execution_lease/v1",
    "operation_execution_lease", "operation_execution_lease_version")
_FIRING_REF_SCHEMA = _ref_schema("transition_firing/v1", "transition_firing",
    "transition_firing_version")
_RESOURCE_REF_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["resource_id", "resource_version_id"],
    "properties": {
        "resource_id": {"type": "string", "pattern": "^resource:[a-f0-9]{32}$"},
        "resource_version_id": {"type": "string", "pattern": "^resource_version:[a-f0-9]{32}$"},
    },
}
_LOCATOR_PROPERTIES = {
    "caller_execution_ref": _EXECUTION_REF_SCHEMA,
    "caller_firing_ref": _FIRING_REF_SCHEMA,
    "result_resource_ref": _RESOURCE_REF_SCHEMA,
    "terminal_receipt_ref": _RESOURCE_REF_SCHEMA,
    "call_id": {"type": "string", "minLength": 1, "maxLength": 512},
}
_LOCATOR_FIELDS = frozenset(_LOCATOR_PROPERTIES)
MANAGED_OPERATION_OUTPUT_ARGUMENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": list(_LOCATOR_PROPERTIES),
    "properties": dict(_LOCATOR_PROPERTIES,
        offset_chars={"type": "integer", "minimum": 0, "default": 0},
        max_bytes={"type": "integer", "minimum": 1, "default": 10000}),
}
MANAGED_OPERATION_ARCHIVE_ARGUMENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["archive_firing_ref"],
    "properties": {
        "archive_firing_ref": _FIRING_REF_SCHEMA,
        "offset": {"type": "integer", "minimum": 0, "default": 0},
        "max_bytes": {"type": "integer", "minimum": 1, "default": 10000},
    },
}
MANAGED_OPERATION_READER_ARGUMENT_SCHEMA = {
    "anyOf": [MANAGED_OPERATION_OUTPUT_ARGUMENT_SCHEMA,
              MANAGED_OPERATION_ARCHIVE_ARGUMENT_SCHEMA],
}
MANAGED_OPERATION_OUTPUT_INPUT_SCHEMA = MANAGED_OPERATION_READER_ARGUMENT_SCHEMA
MANAGED_OPERATION_RESULTS_DOCUMENT = {
    "$id": MANAGED_OPERATION_RESULTS_SCHEMA,
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object", "additionalProperties": False,
    "required": ["schema_version", "caller_execution_ref", "caller_firing_ref", "entries"],
    "properties": {
        "schema_version": {"const": MANAGED_OPERATION_RESULTS_KIND},
        "caller_execution_ref": _EXECUTION_REF_SCHEMA,
        "caller_firing_ref": _FIRING_REF_SCHEMA,
        "entries": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["call", "name", "call_id", "terminal_receipt_ref", "output", "managed_result"],
            "properties": {
                "call": {"type": "object", "required": ["id", "name", "arguments"],
                    "properties": {"id": {"type": "string"}, "name": {"type": "string"},
                                   "arguments": {"type": "object"}}},
                "name": {"type": "string", "minLength": 1},
                "call_id": {"type": "string", "minLength": 1, "maxLength": 512},
                "terminal_receipt_ref": _RESOURCE_REF_SCHEMA,
                "output": {}, "managed_result": {"type": "object"},
            },
        }},
    },
}

_TASK_REF_SCHEMA = _ref_schema("task/v1", "task", "task_version")
_RUN_REF_SCHEMA = _ref_schema("native_run_identity/v1", "run", "run_version")
_BINDING_REF_SCHEMA = _ref_schema("operation_binding/v1", "operation_binding", "operation_binding_version")
_RESOURCE_VERSION_REF_SCHEMA = _ref_schema("resource_version/v1", "resource", "resource_version")
MANAGED_OPERATION_RESULT_DELIVERY_DOCUMENT = {
    "$id": MANAGED_OPERATION_RESULT_DELIVERY_SCHEMA,
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object", "additionalProperties": False,
    "required": ["schema_version", "source_task_ref", "source_run_ref",
                 "producer_execution_ref", "producer_firing_ref",
                 "source_operation_binding_ref", "products"],
    "properties": {
        "schema_version": {"const": MANAGED_OPERATION_RESULT_DELIVERY_KIND},
        "source_task_ref": _TASK_REF_SCHEMA,
        "source_run_ref": _RUN_REF_SCHEMA,
        "producer_execution_ref": _EXECUTION_REF_SCHEMA,
        "producer_firing_ref": _FIRING_REF_SCHEMA,
        "source_operation_binding_ref": _BINDING_REF_SCHEMA,
        "products": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["result_resource_ref", "document"],
            "properties": {"result_resource_ref": _RESOURCE_REF_SCHEMA,
                           "document": MANAGED_OPERATION_RESULTS_DOCUMENT},
        }},
    },
}
MANAGED_OPERATION_RESULT_IMPORT_DOCUMENT = {
    "$id": MANAGED_OPERATION_RESULT_IMPORT_SCHEMA,
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object", "additionalProperties": False,
    "required": ["schema_version", "parent_thread_ref", "deliveries"],
    "properties": {
        "schema_version": {"const": MANAGED_OPERATION_RESULT_IMPORT_KIND},
        "parent_thread_ref": _ref_schema("main_thread/v1", "resource", "resource_version"),
        "deliveries": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["main_turn_ref", "execution_receipt_ref", "terminal_evidence_ref",
                         "terminal_result_ref", "final_result_index_ref", "delivery"],
            "properties": {
                "main_turn_ref": _ref_schema("main_turn/v1", "resource", "resource_version"),
                "execution_receipt_ref": _ref_schema("main_turn_execution_receipt/v1", "resource", "resource_version"),
                "terminal_evidence_ref": _ref_schema("run_terminal_evidence/v1", "terminal_evidence", "terminal_evidence_version"),
                "terminal_result_ref": _RESOURCE_VERSION_REF_SCHEMA,
                "final_result_index_ref": _ref_schema("final_result_index/v1", "terminal_evidence", "terminal_evidence_version"),
                "delivery": MANAGED_OPERATION_RESULT_DELIVERY_DOCUMENT,
            },
        }},
    },
}
MANAGED_OPERATION_IMPORT_ARCHIVE_ARGUMENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["imported_archive"],
    "properties": {
        "imported_archive": {"const": True},
        "import_resource_ref": _RESOURCE_REF_SCHEMA,
        "offset": {"type": "integer", "minimum": 0, "default": 0},
        "max_bytes": {"type": "integer", "minimum": 1, "default": 10000},
    },
}
MANAGED_OPERATION_IMPORT_OUTPUT_ARGUMENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["import_resource_ref", *_LOCATOR_PROPERTIES],
    "properties": dict(MANAGED_OPERATION_OUTPUT_ARGUMENT_SCHEMA["properties"],
                       import_resource_ref=_RESOURCE_REF_SCHEMA),
}
# The v1 declaration remains exact for existing saved reader runs. A fresh
# transfer-enabled operation explicitly chooses this larger provider schema.
MANAGED_OPERATION_TRANSFER_INPUT_SCHEMA = {
    "anyOf": [MANAGED_OPERATION_OUTPUT_ARGUMENT_SCHEMA,
              MANAGED_OPERATION_ARCHIVE_ARGUMENT_SCHEMA,
              MANAGED_OPERATION_IMPORT_ARCHIVE_ARGUMENT_SCHEMA,
              MANAGED_OPERATION_IMPORT_OUTPUT_ARGUMENT_SCHEMA],
}


def _id(value, kind):
    return isinstance(value, str) and re.fullmatch(kind + r":[a-f0-9]{32}", value) is not None


def _exact_ref(value, entity_type, logical_kind, version_kind):
    return (isinstance(value, Mapping)
            and set(value) == {"entity_type", "logical_id", "version_id"}
            and value.get("entity_type") == entity_type
            and _id(value.get("logical_id"), logical_kind)
            and _id(value.get("version_id"), version_kind))


def _resource_ref(value):
    return (isinstance(value, Mapping)
            and set(value) == {"resource_id", "resource_version_id"}
            and _id(value.get("resource_id"), "resource")
            and _id(value.get("resource_version_id"), "resource_version"))


def _source_refs(value):
    return (_exact_ref(value.get("caller_execution_ref"), "operation_execution_lease/v1",
                       "operation_execution_lease", "operation_execution_lease_version")
            and _exact_ref(value.get("caller_firing_ref"), "transition_firing/v1",
                           "transition_firing", "transition_firing_version"))


def validate_managed_operation_locator(locator):
    if (not isinstance(locator, Mapping) or set(locator) != _LOCATOR_FIELDS
            or not _source_refs(locator)
            or not _resource_ref(locator.get("result_resource_ref"))
            or not _resource_ref(locator.get("terminal_receipt_ref"))
            or not isinstance(locator.get("call_id"), str)
            or not locator["call_id"].strip() or len(locator["call_id"]) > 512):
        raise ValueError("managed operation output requires exact execution, firing, product, receipt and call refs")


def validate_managed_operation_result_document(document):
    if (not isinstance(document, Mapping)
            or set(document) != {"schema_version", "caller_execution_ref", "caller_firing_ref", "entries"}
            or document.get("schema_version") != MANAGED_OPERATION_RESULTS_KIND
            or not _source_refs(document) or not isinstance(document.get("entries"), list)):
        raise ValueError("managed operation results require their exact source product shape")
    calls = set()
    for entry in document["entries"]:
        if (not isinstance(entry, Mapping)
                or set(entry) != {"call", "name", "call_id", "terminal_receipt_ref", "output", "managed_result"}
                or not isinstance(entry.get("name"), str) or not entry["name"]
                or not isinstance(entry.get("call_id"), str)
                or not entry["call_id"].strip() or len(entry["call_id"]) > 512
                or entry["call_id"] in calls or not _resource_ref(entry.get("terminal_receipt_ref"))
                or not isinstance(entry.get("call"), Mapping)
                or entry["call"].get("id") != entry["call_id"]
                or entry["call"].get("name") != entry["name"]
                or not isinstance(entry["call"].get("arguments"), Mapping)
                or not isinstance(entry.get("managed_result"), Mapping)):
            raise ValueError("managed result product has an invalid or repeated original call")
        evidence = entry["managed_result"]
        legacy = evidence.get("schema_version") == "rpnh/managed_native_plugin_tool_result/v1"
        if (evidence.get("name") != entry["name"]
                or evidence.get("call_id") != entry["call_id"]
                or evidence.get("terminal_receipt_ref") != entry["terminal_receipt_ref"]
                or "output" in evidence
                or (not legacy and (evidence.get("schema_version") != "rpnh/managed_native_plugin_tool_result/v2"
                                    or evidence.get("outcome") not in {"returned", "failed"}))):
            raise ValueError("managed result product differs from its original receipt projection")
        calls.add(entry["call_id"])
    # Reject non-JSON values before any registered product is constructed.
    serialized_managed_json(document)


def _validate_transfer_document(document, schema, label):
    from jsonschema import Draft7Validator, ValidationError
    try:
        Draft7Validator(schema).validate(document)
        serialized_managed_json(document)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} differs from its closed JSON document contract") from exc


def validate_managed_operation_result_delivery(document):
    """Validate copied delivery data, never grant access to its source refs."""
    _validate_transfer_document(document, MANAGED_OPERATION_RESULT_DELIVERY_DOCUMENT,
                                "managed result delivery")
    resources, firings = set(), set()
    for product in document["products"]:
        original = product["document"]
        validate_managed_operation_result_document(original)
        resource = product["result_resource_ref"]["resource_version_id"]
        firing = original["caller_firing_ref"]["version_id"]
        if resource in resources or firing in firings:
            raise ValueError("managed result delivery repeats a source product or firing")
        if original["caller_firing_ref"] == document["producer_firing_ref"]:
            raise ValueError("managed result delivery cannot export its unsettled producer")
        resources.add(resource)
        firings.add(firing)


def validate_managed_operation_result_import(document):
    """Validate an Owner input snapshot; its original PN claim grants reads."""
    _validate_transfer_document(document, MANAGED_OPERATION_RESULT_IMPORT_DOCUMENT,
                                "managed result import")
    turns, runs, products = set(), set(), set()
    for item in document["deliveries"]:
        validate_managed_operation_result_delivery(item["delivery"])
        turn = item["main_turn_ref"]["logical_id"]
        run = item["delivery"]["source_run_ref"]["version_id"]
        product = item["terminal_result_ref"]["version_id"]
        if turn in turns or run in runs or product in products:
            raise ValueError("managed result import repeats a committed turn, run or terminal product")
        turns.add(turn)
        runs.add(run)
        products.add(product)


def managed_operation_result_document(execution, entries):
    from cpn.rpnh.registry.publication import _ref_payload
    document = {
        "schema_version": MANAGED_OPERATION_RESULTS_KIND,
        "caller_execution_ref": _ref_payload(execution.operation_execution_lease_ref),
        "caller_firing_ref": _ref_payload(execution.operation.firing.transition_firing_ref),
        "entries": list(entries),
    }
    validate_managed_operation_result_document(document)
    return document


def managed_operation_output_locator(execution, result_resource_ref, entry):
    from cpn.rpnh.registry.publication import _ref_payload
    ref = (dict(result_resource_ref) if isinstance(result_resource_ref, Mapping) else {
        "resource_id": str(result_resource_ref.resource_id),
        "resource_version_id": str(result_resource_ref.resource_version_id)})
    value = {
        "caller_execution_ref": _ref_payload(execution.operation_execution_lease_ref),
        "caller_firing_ref": _ref_payload(execution.operation.firing.transition_firing_ref),
        "result_resource_ref": ref,
        "terminal_receipt_ref": dict(entry["terminal_receipt_ref"]),
        "call_id": entry["call_id"],
    }
    validate_managed_operation_locator(value)
    return value


def bounded_managed_operation_output_projection(output, arguments):
    if (not isinstance(arguments, Mapping) or not _LOCATOR_FIELDS.issubset(arguments)
            or set(arguments) - (_LOCATOR_FIELDS | {"offset_chars", "max_bytes"})):
        raise ValueError("managed operation output requires closed page arguments")
    locator = {key: arguments[key] for key in _LOCATOR_FIELDS}
    validate_managed_operation_locator(locator)
    return _operation_body_page(output, locator,
        offset=arguments.get("offset_chars", 0),
        max_bytes=arguments.get("max_bytes", 10_000))


def _operation_body_page(output, locator, *, offset, max_bytes, context=None):
    text = serialized_managed_json(output)
    if type(offset) is not int or not 0 <= offset <= len(text):
        raise ValueError("managed output offset_chars is outside the output")
    return fit_managed_output_page({
        "kind": MANAGED_OPERATION_OUTPUT_PAGE_KIND, **locator,
        **(context or {}),
        "reader": MANAGED_OUTPUT_READER, "content": text[offset:],
        "offset_chars": offset, "next_offset_chars": None,
        "total_chars": len(text), "truncated": False,
    }, max_bytes)


def render_managed_operation_output(entry, locator, *, max_bytes):
    evidence = entry["managed_result"]
    if (evidence.get("outcome", "returned") != "returned"
            or entry["call_id"] != locator["call_id"]
            or entry["terminal_receipt_ref"] != locator["terminal_receipt_ref"]):
        raise ValueError("managed output requires the exact returned result projection")
    return bounded_managed_operation_output_projection(entry["output"], {
        **locator, "max_bytes": max_bytes})


def validate_managed_operation_output_page(page):
    if (not isinstance(page, Mapping)
            or set(page) != _LOCATOR_FIELDS | {"kind", "reader", "content", "offset_chars",
                                                "next_offset_chars", "total_chars", "truncated"}
            or page.get("kind") != MANAGED_OPERATION_OUTPUT_PAGE_KIND
            or page.get("reader") != MANAGED_OUTPUT_READER):
        raise ValueError("managed operation output page has invalid fields")
    validate_managed_operation_locator({key: page[key] for key in _LOCATOR_FIELDS})
    offset, total, following = (page[key] for key in
                               ("offset_chars", "total_chars", "next_offset_chars"))
    if (any(type(n) is not int or n < 0 for n in (offset, total))
            or offset > total or not isinstance(page["content"], str)
            or type(page["truncated"]) is not bool):
        raise ValueError("managed output page range is invalid")
    end = offset + len(page["content"])
    if (end > total or (end == offset and end < total)
            or (end == total and (following is not None or page["truncated"]))
            or (end < total and (type(following) is not int
                                or following != end or not page["truncated"]))):
        raise ValueError("managed output page continuation is invalid")


def bound_managed_operation_output_page(page, max_bytes):
    validate_managed_operation_output_page(page)
    return fit_managed_output_page(page, max_bytes)


def validate_managed_operation_import_output_page(page):
    if (not isinstance(page, Mapping)
            or page.get("kind") != MANAGED_OPERATION_IMPORT_OUTPUT_PAGE_KIND
            or page.get("read_scope") != MANAGED_OPERATION_IMPORT_READ_SCOPE
            or not _resource_ref(page.get("import_resource_ref"))):
        raise ValueError("managed imported output page lacks its registered-copy scope")
    original = {key: value for key, value in page.items()
                if key not in {"import_resource_ref", "read_scope"}}
    original["kind"] = MANAGED_OPERATION_OUTPUT_PAGE_KIND
    validate_managed_operation_output_page(original)


class ManagedOperationOutputReader:
    """Owner-side read-only adapter over committed ordinary result products.

    An exact locator only selects evidence.  The current operation must expose
    the registered HOST reader, and its original resource read contract must
    authorize the selected product.  Neither receipt existence nor a matching
    session string grants access.  No business-tool prepare/invoke/finish path
    is reachable from this service.
    """

    def __init__(self, invocations):
        from .managed_tools import ManagedPluginInvocationService
        if not isinstance(invocations, ManagedPluginInvocationService):
            raise TypeError("managed output reader requires the existing invocation service")
        self.owner = invocations.owner
        self.core = invocations.core
        self.kernel = invocations.kernel
        self.repository = invocations.repository

    def gateway_methods(self):
        return {"read_managed_operation_output_v1": self.read,
                "read_managed_operation_inputs_v1": self.read_inputs,
                "export_managed_operation_results_v1": self.export}

    def read_inputs(self, execution) -> tuple[bytes, ...]:
        """Read only this current execution's original declared input bytes.

        Same-length copied DTO bodies do not replace registered source bytes.
        The existing execution/Start and HOST gates authorize these inputs;
        this entry accepts no resource locator, grant, cache or new fact.
        """
        from cpn.rpnh.registry.operation_execution import verify_operation_execution
        execution = verify_operation_execution(
            self.core, self.kernel, self.repository, execution)
        if (MANAGED_OPERATION_RESULTS_EXPORT_TOOL in execution.operation.spec.allowed_tool_ids
                and MANAGED_OPERATION_OUTPUT_TOOL not in execution.operation.spec.allowed_tool_ids):
            execution, _source = self._export_consumer(execution)
        else:
            execution, _limit = self._consumer(execution)
        context = execution.operation.canonical.context
        return tuple(self.kernel._read_firing_registered(context, item.resource_ref)
                     for item in execution.operation.inputs)

    def _host_consumer(self, execution, key, identity, contracts):
        from cpn.components.execution_services import resolve_registered_tool
        from cpn.rpnh.registry.operation_execution import verify_operation_execution
        execution = verify_operation_execution(
            self.core, self.kernel, self.repository, execution)
        resolve_registered_tool(self.owner, self.kernel, self.repository, execution,
            key, identity=identity, contracts=contracts)
        compiled, declared = self.repository.registered_compiled_operation(execution.operation)
        return execution, compiled, declared

    def _consumer(self, execution):
        execution, _compiled, declared = self._host_consumer(execution,
            MANAGED_OPERATION_OUTPUT_TOOL, MANAGED_OPERATION_OUTPUT_IDENTITY,
            MANAGED_OPERATION_OUTPUT_CONTRACTS)
        limit = declared.declaration.config.get("context_tool_output_byte_limit")
        if type(limit) is not int or limit < 1:
            from cpn.rpnh.registry.operations import OperationAuthorityError
            raise OperationAuthorityError("managed output reader lacks its registered positive page budget")
        return execution, limit

    def _export_consumer(self, execution):
        from cpn.rpnh.registry.errors import ResourceIntegrityFault
        from cpn.rpnh.registry.operations import OperationAuthorityError
        from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
        execution, compiled, declared = self._host_consumer(execution,
            MANAGED_OPERATION_RESULTS_EXPORT_TOOL, MANAGED_OPERATION_RESULTS_EXPORT_IDENTITY,
            MANAGED_OPERATION_RESULTS_EXPORT_CONTRACTS)
        source_name = declared.declaration.config.get("managed_result_export_source")
        selected = [item for item in compiled.symbolic.transitions if item.name == source_name]
        if len(selected) != 1:
            raise OperationAuthorityError("managed result export lacks one registered source transition")
        operations = [item for item in compiled.operations
                      if item.declaration.name == selected[0].operation]
        ports = {port.name: port for port in compiled.ports}
        if (len(operations) != 1 or not any(
                ports[name].schema == MANAGED_OPERATION_RESULTS_SCHEMA
                for name in operations[0].declaration.outputs)):
            raise OperationAuthorityError("managed result export source has no declared result product")
        context = execution.operation.canonical.context
        view = self.core.event_store.canonical_view(through_ordinal=execution.admission_head.ordinal)
        net = self.kernel._exact_object_for_view(view, context.net_instance_ref,
                                                expected_type="net_instance/v1").metadata
        bindings = []
        for raw in net["executable_transition_binding_refs"]:
            transition = self.kernel._exact_object_for_view(view, _version_from_payload(raw),
                expected_type="executable_transition_binding/v1").metadata
            if transition.get("transition_id") == source_name:
                if (transition.get("net_instance_ref") != _ref_payload(context.net_instance_ref)
                        or transition.get("declaration_resource_ref") != net["team_net_declaration_resource_ref"]):
                    raise ResourceIntegrityFault("managed export source differs from its original net")
                bindings.append(transition["operation_binding_ref"])
        if len(bindings) != 1:
            raise ResourceIntegrityFault("managed result export source binding is not exact")
        return execution, bindings[0]

    def export(self, execution):
        """Copy this run's settled result products for an explicit HOST terminal.

        The caller publishes the returned bytes in its selected terminal
        product. Until that product succeeds and its parent validates actual
        terminal delivery, this value grants no cross-run reading authority.
        Imported copies are deliberately excluded.
        """
        from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
        execution, binding = self._export_consumer(execution)
        context = execution.operation.canonical.context
        view = self.core.event_store.canonical_view(through_ordinal=execution.admission_head.ordinal)
        products = []
        for event in self.core.event_store.canonical_events(through_ordinal=execution.admission_head.ordinal):
            if event.event_type != "transition_firing_settled/v1":
                continue
            firing_raw = event.payload["transition_firing_ref"]
            firing = self.kernel._exact_object_for_view(view, _version_from_payload(firing_raw),
                expected_type="transition_firing/v1").metadata
            if (firing.get("operation_binding_ref") != binding
                    or firing.get("task_ref") != _ref_payload(context.task_ref)
                    or firing.get("net_instance_ref") != _ref_payload(context.net_instance_ref)):
                continue
            source = self._source(execution, firing_raw)
            for raw in source["result"]["output_resource_refs"]:
                product = self.kernel._exact_object_for_view(source["view"], _version_from_payload(raw),
                    expected_type="resource_version/v1")
                if product.metadata.get("content_schema_ref") != MANAGED_OPERATION_RESULTS_SCHEMA:
                    continue
                resource_raw = {"resource_id": str(product.logical_id),
                                "resource_version_id": str(product.version_id)}
                document = self._product(execution, source, resource_raw)
                for entry in document["entries"]:
                    self._receipt(source, entry, returned_only=False)
                if document["entries"]:
                    products.append({"result_resource_ref": resource_raw, "document": document})
        value = {
            "schema_version": MANAGED_OPERATION_RESULT_DELIVERY_KIND,
            "source_task_ref": _ref_payload(context.task_ref),
            "source_run_ref": _ref_payload(self.owner.identity.run_ref),
            "producer_execution_ref": _ref_payload(execution.operation_execution_lease_ref),
            "producer_firing_ref": _ref_payload(execution.operation.firing.transition_firing_ref),
            "source_operation_binding_ref": binding,
            "products": products,
        }
        validate_managed_operation_result_delivery(value)
        return value

    def _import_snapshot(self, execution, requested_ref=None):
        """Read the exact static lease claimed by this firing, never a child."""
        from cpn.rpnh.registry.errors import ResourceIntegrityFault, UnauthorizedResourceDelivery
        from cpn.rpnh.registry.publication import _ref_payload, _resource_from_payload
        compiled, declared = self.repository.registered_compiled_operation(execution.operation)
        symbol = declared.declaration.config.get("managed_result_import_symbol")
        leases = [item for item in compiled.symbolic.lease_identities
                  if item.name == symbol and item.kind == "resource"]
        pools = [item for item in compiled.symbolic.lease_pools
                 if item.initial_resources == (symbol,) and not item.initial_slots]
        if not isinstance(symbol, str) or len(leases) != 1 or len(pools) != 1:
            raise ManagedOperationOutputReadDenied("this operation has no declared managed result import lease")
        pool = pools[0]
        places = [item for item in compiled.symbolic.places if item.name == pool.place]
        arcs = [item for item in compiled.symbolic.arcs
                if item.place == pool.place and item.transition == execution.operation.firing.transition_id
                and item.direction == "input" and item.mode == "read" and item.weight == 1]
        if (len(places) != 1 or places[0].token_kind != "resource_lease"
                or places[0].schema != MANAGED_OPERATION_RESULT_IMPORT_SCHEMA
                or places[0].capacity != 1 or not places[0].reusable or len(arcs) != 1):
            raise ManagedOperationOutputReadDenied("managed result import requires its original static read arc")
        context = execution.operation.canonical.context
        view = self.core.event_store.canonical_view(through_ordinal=execution.admission_head.ordinal)
        net = self.kernel._exact_object_for_view(view, context.net_instance_ref,
                                                expected_type="net_instance/v1").metadata
        raw = net["module_resource_bindings"]["owner_resource_inputs"].get(symbol)
        if not _resource_ref(raw):
            raise ResourceIntegrityFault("managed result import lease lacks its original Owner resource")
        if requested_ref is not None and requested_ref != raw:
            raise ManagedOperationOutputReadDenied("managed result import ref differs from this run's registered copy")
        resource = _resource_from_payload(raw)
        claimed = []
        for token_ref in execution.operation.firing.claimed_input_refs:
            token = self.kernel._exact_object_for_view(view, token_ref,
                expected_type="petri_token/v1").metadata
            if token.get("place") == pool.place:
                claimed.append(token)
        if (len(claimed) != 1
                or claimed[0].get("resource_ref") != raw
                or claimed[0].get("lease_identity_ref") != _ref_payload(resource.as_version_ref())
                or claimed[0].get("net_instance_ref") != _ref_payload(context.net_instance_ref)
                or claimed[0].get("consumed_by") is not None):
            raise ManagedOperationOutputReadDenied("managed result import is outside this firing's exact lease claim")
        prepared = self.kernel._canonical_prepared(resource, through_ordinal=execution.admission_head.ordinal)
        if (prepared.metadata.get("content_schema_ref") != MANAGED_OPERATION_RESULT_IMPORT_SCHEMA
                or prepared.metadata.get("task_ref") != _ref_payload(context.task_ref)
                or prepared.metadata.get("origin_kind") != "private_system"):
            raise ResourceIntegrityFault("managed result import differs from its registered Owner input schema")
        try:
            self.kernel._authorize_resource(context, context.operation_binding_ref,
                                            resource, metadata_only=False)
        except UnauthorizedResourceDelivery as exc:
            raise ManagedOperationOutputReadDenied(str(exc)) from exc
        document = json.loads(self.kernel._read_firing_registered(context, resource, prepared=prepared))
        validate_managed_operation_result_import(document)
        return dict(raw), document

    @staticmethod
    def _import_entries(document):
        for delivered in document["deliveries"]:
            for product in delivered["delivery"]["products"]:
                original = product["document"]
                for ordinal, entry in enumerate(original["entries"]):
                    locator = {"caller_execution_ref": original["caller_execution_ref"],
                               "caller_firing_ref": original["caller_firing_ref"],
                               "result_resource_ref": product["result_resource_ref"],
                               "terminal_receipt_ref": entry["terminal_receipt_ref"],
                               "call_id": entry["call_id"]}
                    yield delivered, ordinal, entry, locator

    def _import_archive(self, execution, arguments):
        if (set(arguments) - {"imported_archive", "import_resource_ref", "offset", "max_bytes"}
                or arguments.get("imported_archive") is not True
                or ("import_resource_ref" in arguments and not _resource_ref(arguments["import_resource_ref"]))
                or type(arguments.get("offset", 0)) is not int or arguments.get("offset", 0) < 0):
            raise ManagedOperationOutputReadDenied("managed imported archive requires exact closed page arguments")
        resource, document = self._import_snapshot(execution, arguments.get("import_resource_ref"))
        entries = []
        for delivered, ordinal, entry, locator in self._import_entries(document):
            phase = entry["managed_result"].get("outcome", "returned")
            entries.append({**locator, "import_resource_ref": resource,
                "main_turn_ref": delivered["main_turn_ref"], "name": entry["name"],
                "outcome": phase, "call_ordinal": ordinal,
                "reader": MANAGED_OUTPUT_READER if phase == "returned" else None})
        offset = arguments.get("offset", 0)
        if offset > len(entries):
            raise ManagedOperationOutputReadDenied("managed imported archive offset is outside its frozen input")
        page_entries = entries[offset:offset + MANAGED_OPERATION_ARCHIVE_MAX_ENTRIES]
        end = offset + len(page_entries)
        try:
            return fit_result_archive_page({
                "kind": MANAGED_OPERATION_IMPORT_ARCHIVE_PAGE_KIND,
                "read_scope": MANAGED_OPERATION_IMPORT_READ_SCOPE,
                "import_resource_ref": resource, "parent_thread_ref": document["parent_thread_ref"],
                "reader": MANAGED_OUTPUT_READER, "offset": offset,
                "next_offset": end if end < len(entries) else None,
                "total_count": len(entries), "entries": page_entries,
            }, arguments["max_bytes"])
        except ValueError as exc:
            raise ManagedOperationOutputReadDenied(str(exc)) from exc

    def _import_output(self, execution, arguments):
        if (set(arguments) - (_LOCATOR_FIELDS | {"import_resource_ref", "offset_chars", "max_bytes"})
                or not _LOCATOR_FIELDS.issubset(arguments)
                or not _resource_ref(arguments.get("import_resource_ref"))):
            raise ManagedOperationOutputReadDenied("managed imported output requires the local copy and exact source locator")
        locator = {key: arguments[key] for key in _LOCATOR_FIELDS}
        try:
            validate_managed_operation_locator(locator)
        except ValueError as exc:
            raise ManagedOperationOutputReadDenied(str(exc)) from exc
        resource, document = self._import_snapshot(execution, arguments["import_resource_ref"])
        matches = [entry for _delivery, _ordinal, entry, original in self._import_entries(document)
                   if original == locator]
        if len(matches) != 1 or matches[0]["managed_result"].get("outcome", "returned") != "returned":
            raise ManagedOperationOutputReadDenied("managed imported output is not one delivered returned result")
        try:
            return _operation_body_page(matches[0]["output"], locator,
                offset=arguments.get("offset_chars", 0), max_bytes=arguments["max_bytes"],
                context={"kind": MANAGED_OPERATION_IMPORT_OUTPUT_PAGE_KIND,
                         "read_scope": MANAGED_OPERATION_IMPORT_READ_SCOPE,
                         "import_resource_ref": resource})
        except ValueError as exc:
            raise ManagedOperationOutputReadDenied(str(exc)) from exc

    def _source(self, execution, firing_raw, caller_raw=None):
        from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
        from cpn.rpnh.registry.errors import UnknownResourceVersion
        firing_ref = _version_from_payload(firing_raw)
        view = self.core.event_store.canonical_view(
            through_ordinal=execution.admission_head.ordinal)
        try:
            firing = self.kernel._exact_object_for_view(
                view, firing_ref, expected_type="transition_firing/v1").metadata
        except UnknownResourceVersion as exc:
            raise ManagedOperationOutputReadDenied(
                "managed output source is outside the consumer's settled admission cut") from exc
        record = self.core.event_store.ordered_firing_record(firing_ref.version_id)
        publication_ordinal = self.core.event_store.canonical_object_publication_ordinal(
            firing_ref.version_id, through_ordinal=execution.admission_head.ordinal)
        completion = record["firing_completion"]
        starts = [event for event in record["events"]
                  if event.event_type == "operation_execution_started/v1"]
        settlements = [event for event in record["events"]
                       if event.event_type == "transition_firing_settled/v1"]
        if (record["state"] != "PUBLISHED" or completion is None
                or len(starts) != 1 or len(settlements) != 1 or publication_ordinal is None
                or settlements[0].ordinal > execution.admission_head.ordinal
                or firing.get("transition_firing_ref") != firing_raw
                or firing.get("task_ref") != _ref_payload(execution.operation.canonical.context.task_ref)
                or completion.get("transition_firing_ref") != firing_raw):
            raise ManagedOperationOutputReadDenied("managed output source requires an earlier Success in this run")
        started = starts[0].payload
        source_execution = started["operation_execution_lease_ref"]
        invocation_ref = started["invocation_ref"]
        if (started.get("transition_firing_ref") != firing_raw
                or (caller_raw is not None and caller_raw != source_execution)):
            raise ManagedOperationOutputReadDenied("managed output source execution and firing disagree")
        lease = self.kernel._exact_object_for_view(view,
            _version_from_payload(source_execution),
            expected_type="operation_execution_lease/v1").metadata
        invocation = self.kernel._exact_object_for_view(view,
            _version_from_payload(invocation_ref), expected_type="invocation/v1").metadata
        result = self.kernel._exact_object_for_view(view,
            _version_from_payload(completion["operation_result_ref"]),
            expected_type="operation_result/v1").metadata
        if (lease.get("invocation_ref") != invocation_ref
                or lease.get("operation_execution_lease_ref") != source_execution
                or invocation.get("own_transition_firing_ref") != firing_raw
                or invocation.get("operation_execution_lease_ref") != source_execution
                or invocation.get("operation_binding_ref") != firing.get("operation_binding_ref")
                or result.get("transition_firing_ref") != firing_raw
                or result.get("invocation_ref") != invocation_ref
                or result.get("business_outcome") != "completed"):
            raise ValueError("managed output source lacks exact original execution closure")
        return {"view": view, "firing": firing, "execution_ref": source_execution,
                "invocation_ref": invocation_ref, "result": result,
                "publication_ordinal": publication_ordinal,
                "temporary_members": record["temporary_members"]}

    def _product(self, execution, source, resource_raw):
        from cpn.rpnh.registry.publication import _ref_payload, _resource_from_payload
        from cpn.rpnh.registry.errors import UnauthorizedResourceDelivery
        resource_ref = _resource_from_payload(resource_raw)
        if _ref_payload(resource_ref.as_version_ref()) not in source["result"]["output_resource_refs"]:
            raise ManagedOperationOutputReadDenied("managed output product is outside its exact Success bundle")
        prepared = self.kernel._canonical_prepared(resource_ref,
            through_ordinal=execution.admission_head.ordinal)
        metadata = prepared.metadata
        if (metadata.get("content_schema_ref") != MANAGED_OPERATION_RESULTS_SCHEMA
                or metadata.get("origin_kind") != "petri_output"
                or metadata.get("producer_ref") != source["invocation_ref"]
                or metadata.get("task_ref") != source["firing"]["task_ref"]
                or metadata.get("round_ref") != source["firing"]["task_round_ref"]
                or metadata.get("net_ref") != source["firing"]["net_instance_ref"]
                or metadata.get("descriptors", {}).get("output_outcome_id")
                != source["result"]["selected_outcome_id"]):
            raise ValueError("managed output is not this source's formal result product")
        context = execution.operation.canonical.context
        try:
            self.kernel._authorize_resource(context, context.operation_binding_ref,
                                            resource_ref, metadata_only=False)
        except UnauthorizedResourceDelivery as exc:
            raise ManagedOperationOutputReadDenied(str(exc)) from exc
        payload = self.kernel._read_firing_registered(context, resource_ref, prepared=prepared)
        document = json.loads(payload)
        validate_managed_operation_result_document(document)
        if (document["caller_execution_ref"] != source["execution_ref"]
                or document["caller_firing_ref"] != source["firing"]["transition_firing_ref"]):
            raise ValueError("managed result product names another source operation")
        return document

    def _receipt(self, source, entry, *, returned_only):
        """Verify original receipt membership/phase; never deliver its body."""
        from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload, _resource_from_payload
        from .managed_tools import managed_call_receipt_refs
        evidence = entry["managed_result"]
        phase = evidence.get("outcome", "returned")
        if phase not in {"returned", "failed"} or (returned_only and phase != "returned"):
            raise ValueError("managed output requires an originally returned terminal receipt")
        key, refs = managed_call_receipt_refs(
            _version_from_payload(source["execution_ref"]),
            _version_from_payload(source["firing"]["transition_firing_ref"]), entry["call_id"])
        receipt_ref = _resource_from_payload(entry["terminal_receipt_ref"])
        if receipt_ref != refs[phase]:
            raise ValueError("managed result receipt differs from its original call identity")
        receipt = self.kernel._exact_object_for_view(source["view"],
            receipt_ref.as_version_ref(), expected_type="resource_version/v1")
        metadata = receipt.metadata
        if (metadata.get("origin_kind") != "managed_native_plugin_invocation"
                or metadata.get("producer_ref") != source["invocation_ref"]
                or metadata.get("lifetime_ref") != source["execution_ref"]
                or metadata.get("origin", {}).get("primary_ref") != source["execution_ref"]
                or metadata.get("origin", {}).get("secondary_ref") != source["invocation_ref"]
                or metadata.get("task_ref") != source["firing"]["task_ref"]
                or metadata.get("net_ref") != source["firing"]["net_instance_ref"]
                or metadata.get("descriptors") != {"managed_plugin_call": key, "phase": phase}
                or metadata.get("reference_provenance", {}).get("operation_binding_ref")
                != source["firing"]["operation_binding_ref"]
                or ("object", str(receipt_ref.resource_version_id)) not in source["temporary_members"]):
            raise ValueError("managed result receipt is outside the original settled caller")
        return phase

    def read(self, execution, arguments):
        execution, limit = self._consumer(execution)
        if not isinstance(arguments, Mapping):
            raise ManagedOperationOutputReadDenied("managed output arguments must be a JSON object")
        requested = arguments.get("max_bytes", limit)
        if type(requested) is not int or requested < 1:
            raise ManagedOperationOutputReadDenied("managed output max_bytes must be a positive integer")
        arguments = dict(arguments, max_bytes=min(requested, limit))
        if "imported_archive" in arguments:
            return self._import_archive(execution, arguments)
        if "import_resource_ref" in arguments:
            return self._import_output(execution, arguments)
        if isinstance(arguments, Mapping) and "archive_firing_ref" in arguments:
            return self._archive(execution, arguments)
        if (not isinstance(arguments, Mapping) or not _LOCATOR_FIELDS.issubset(arguments)
                or set(arguments) - (_LOCATOR_FIELDS | {"offset_chars", "max_bytes"})):
            raise ManagedOperationOutputReadDenied("managed operation output requires closed page arguments")
        locator = {key: arguments[key] for key in _LOCATOR_FIELDS}
        try:
            validate_managed_operation_locator(locator)
        except ValueError as exc:
            raise ManagedOperationOutputReadDenied(str(exc)) from exc
        source = self._source(execution, locator["caller_firing_ref"], locator["caller_execution_ref"])
        document = self._product(execution, source, locator["result_resource_ref"])
        entries = [entry for entry in document["entries"] if entry["call_id"] == locator["call_id"]]
        if len(entries) != 1 or entries[0]["terminal_receipt_ref"] != locator["terminal_receipt_ref"]:
            raise ManagedOperationOutputReadDenied("managed output call/receipt is outside the exact result product")
        if entries[0]["managed_result"].get("outcome", "returned") != "returned":
            raise ManagedOperationOutputReadDenied("managed output requires an originally returned terminal receipt")
        self._receipt(source, entries[0], returned_only=True)
        try:
            return bounded_managed_operation_output_projection(entries[0]["output"], arguments)
        except ValueError as exc:
            raise ManagedOperationOutputReadDenied(str(exc)) from exc

    def _archive(self, execution, arguments):
        from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
        if (set(arguments) - {"archive_firing_ref", "offset", "max_bytes"}
                or not _exact_ref(arguments["archive_firing_ref"], "transition_firing/v1",
                                  "transition_firing", "transition_firing_version")
                or type(arguments.get("offset", 0)) is not int or arguments.get("offset", 0) < 0
                or type(arguments.get("max_bytes", 10000)) is not int or arguments.get("max_bytes", 10000) < 1):
            raise ManagedOperationOutputReadDenied("managed archive requires exact closed firing/cut/page arguments")
        anchor = self._source(execution, arguments["archive_firing_ref"])
        context = execution.operation.canonical.context
        # The anchor must itself belong to this caller's registered output scope.
        # Matching a session/run label is never archive permission.
        if anchor["firing"]["operation_binding_ref"] != _ref_payload(context.operation_binding_ref):
            raise ManagedOperationOutputReadDenied("managed archive anchor belongs to another operation binding")
        entries = []
        events = self.core.event_store.canonical_events(
            through_ordinal=anchor["publication_ordinal"])
        for event in events:
            if event.event_type != "transition_firing_settled/v1":
                continue
            firing_raw = event.payload["transition_firing_ref"]
            firing = self.kernel._exact_object_for_view(anchor["view"],
                _version_from_payload(firing_raw), expected_type="transition_firing/v1").metadata
            if (firing.get("task_ref") != _ref_payload(context.task_ref)
                    or firing.get("operation_binding_ref") != _ref_payload(context.operation_binding_ref)):
                continue
            source = self._source(execution, firing_raw)
            for raw in source["result"]["output_resource_refs"]:
                product = self.kernel._exact_object_for_view(source["view"],
                    _version_from_payload(raw), expected_type="resource_version/v1")
                if product.metadata.get("content_schema_ref") != MANAGED_OPERATION_RESULTS_SCHEMA:
                    continue
                resource_raw = {"resource_id": str(product.logical_id),
                                "resource_version_id": str(product.version_id)}
                document = self._product(execution, source, resource_raw)
                for ordinal, entry in enumerate(document["entries"]):
                    phase = self._receipt(source, entry, returned_only=False)
                    entries.append({
                        "caller_execution_ref": source["execution_ref"],
                        "caller_firing_ref": firing_raw,
                        "result_resource_ref": resource_raw,
                        "terminal_receipt_ref": entry["terminal_receipt_ref"],
                        "call_id": entry["call_id"], "name": entry["name"],
                        "outcome": phase, "call_ordinal": ordinal,
                        "reader": MANAGED_OUTPUT_READER if phase == "returned" else None,
                    })
        offset = arguments.get("offset", 0)
        if offset > len(entries):
            raise ManagedOperationOutputReadDenied("managed archive offset is outside its settled source cut")
        page_entries = entries[offset:offset + MANAGED_OPERATION_ARCHIVE_MAX_ENTRIES]
        end = offset + len(page_entries)
        try:
            return fit_result_archive_page({
                "kind": MANAGED_OPERATION_ARCHIVE_PAGE_KIND,
                "archive_firing_ref": dict(arguments["archive_firing_ref"]),
                "reader": MANAGED_OUTPUT_READER, "offset": offset,
                "next_offset": end if end < len(entries) else None,
                "total_count": len(entries), "entries": page_entries,
            }, arguments.get("max_bytes", 10000))
        except ValueError as exc:
            raise ManagedOperationOutputReadDenied(str(exc)) from exc
