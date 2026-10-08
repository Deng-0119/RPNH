"""Exact immutable archive cuts, closed readers, and bounded directory pages."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft7Validator

from cpn.components.agent_loop.action_records import ActionRecordsMechanicsMixin
from cpn.components.agent_loop.mechanical_lifecycle import AgentLoopMechanicalLifecycle
from cpn.components.agent_loop.models import AgentActionRecord, AgentLoopProtocolError, AgentLoopState, stable_action_id
from cpn.components.agent_loop.program_execution import ProgramExecutionMixin
from cpn.components.agent_loop.result_archive import (
    ARCHIVE_READERS, bound_result_archive_page, read_result_archive,
    result_archive_locator, validate_result_archive_page,
)
from cpn.components.agent_loop.managed_output import bounded_managed_output_projection, serialized_managed_json
from cpn.components.agent_loop.tool_catalog import TOOL_ARGUMENT_SCHEMAS, build_agent_tool_catalog
from cpn.components.agent_loop.workspace import WorkspaceExecutionMixin
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload


def _size(value):
    return len(serialized_managed_json(value).encode("utf-8"))


def _ref(kind, logical, version):
    return VersionRef(kind, new_id(logical), new_id(version))


class History:
    """Read-only Registry surface with real loop/action hydration and records."""
    def __init__(self, names=ARCHIVE_READERS):
        from test_agent_loop_functional_modularization import _snapshot
        self.loop = replace(_snapshot(), revision=100, next_turn_sequence=30,
                            llm_turns_used=30, llm_turn_budget=100)
        self.objects, self.turns, self.settlements, self.rows = {}, [], [], []
        core = SimpleNamespace(event_store=self)
        kernel = SimpleNamespace(core=core, _exact_object=self.exact_object)
        self.lifecycle = AgentLoopMechanicalLifecycle(core, kernel)
        self.put_loop(self.loop)
        catalog = build_agent_tool_catalog(tool_names=tuple(sorted((*names, "complete_interaction"))))
        self.gateway = SimpleNamespace(
            mechanical_lifecycle=self.lifecycle, _execution=lambda *a: None,
            _context=lambda loop: loop,
            _static=lambda *a: (self.loop.tool_catalog_ref, None, catalog.payload))
        self.arguments = dict(result_archive_locator(
            self.loop, tool_names=names, before_turn_sequence=20) or {
                "archive_loop_ref": _ref_payload(self.lifecycle.loop_ref(self.loop)),
                "before_turn_sequence": 20})
        self.arguments.pop("reader", None)

    def exact_object(self, ref, **kwargs):
        return SimpleNamespace(metadata=deepcopy(self.objects[ref]))

    def put_loop(self, loop):
        ref = self.lifecycle.loop_ref(loop)
        self.objects[ref] = self.lifecycle.loop_document(loop)
        return ref

    def list_events_by_aggregate(self, loop_id, *, event_types):
        events = self.turns if event_types == ("agent_turn_recorded/v2",) else self.settlements
        return tuple(e for e in reversed(events) if e.payload["agent_loop_id"] == loop_id)

    def object_rows_by_logical(self, logical_id, *, object_type):
        return tuple(r for r in reversed(self.rows)
                     if r["logical_id"] == str(logical_id) and r["object_type"] == object_type)

    def add(self, sequence=0, ordinal=0, *, kind="managed", status="returned", revision=5):
        turn = next((e for e in self.turns if e.payload["sequence"] == sequence), None)
        if turn is None:
            ref = _ref("agent_turn/v1", "agent_turn", "agent_turn_version")
            turn = SimpleNamespace(payload={
                "agent_loop_id": self.loop.loop_id, "agent_turn_id": str(ref.entity_id),
                "agent_turn_version_id": str(ref.version_id), "sequence": sequence,
                "revision": revision - 1})
            self.turns.append(turn)
        turn_ref = self.lifecycle.turn_ref_from_event(turn)
        source_loop = replace(self.loop, loop_version_id=str(new_id("agent_loop_version")), revision=revision)
        source_loop_ref = self.put_loop(source_loop)
        call_id = f"call-{sequence}-{ordinal}"
        action_id = stable_action_id(self.loop.loop_id, sequence, call_id)
        ref = VersionRef("agent_action/v3" if kind == "managed" else "agent_action/v2",
                         TypedId.parse(action_id, expected="agent_action"),
                         new_id("agent_action_version"))
        receipt = {"resource_id": str(new_id("resource")), "resource_version_id": str(new_id("resource_version"))}
        metadata = {"kind": "managed_native_plugin_result/v1", "output": "needle中😀\\\"",
                    "terminal_receipt_ref": receipt}
        args, managed, error = {}, None, None
        state = AgentLoopState.ACTION_APPLIED
        result_refs = ()
        if kind == "managed":
            tool_name = "synthetic中😀\\\""
            returned = status == "returned"
            managed = {"provider_name": tool_name, "registration_key": "synthetic/output",
                       "selector": "synthetic/output", "plugin_catalog_digest": "catalog",
                       "binding_digest": "binding", "effect": "pure", "outcome": status,
                       "request_admission_receipt_ref": receipt, "started_receipt_ref": receipt,
                       "admitted_at_utc": "2026-10-08T00:00:00Z", "terminal_receipt_ref": receipt,
                       "model_visible_result_ref": None, "non_delivery_reason": "provider_delivery_not_recorded",
                       "output": metadata["output"], "error": None, "max_result_bytes": 10000}
            if not returned:
                state, metadata = AgentLoopState.ACTION_REJECTED, None
                error = _ref("agent_tool_error/v1", "agent_tool_error", "agent_tool_error_version")
                managed.update(output=None, error={"code": status}, non_delivery_reason={
                    "failed": "managed_call_failed", "outcome_unknown": "outcome_unknown",
                    "rejected": "rejected_before_dispatch"}[status])
                if status == "rejected":
                    managed.update(request_admission_receipt_ref=None, started_receipt_ref=None,
                                   admitted_at_utc=None, terminal_receipt_ref=None)
        elif kind == "program":
            tool_name = "run_tool_program"
            metadata = {"kind": "tool_program_result/v1", "status": status, "call_count": 0,
                        "reader": "read_tool_program_output", "agent_action_ref": _ref_payload(ref),
                        "output_resource_ref": receipt,
                        "program_invocation_ref": _ref_payload(_ref(
                            "agent_tool_program_invocation/v1", "invocation", "invocation_version"))}
            result_refs = (_version_from_payload({"entity_type": "resource_version/v1",
                "logical_id": receipt["resource_id"], "version_id": receipt["resource_version_id"]}),)
        elif kind == "page":
            tool_name = "read_managed_output"
            source = next(value for key, value in self.objects.items() if key.entity_type == "agent_action/v3")
            args = {"agent_action_ref": source["agent_action_ref"], "terminal_receipt_ref": source["terminal_receipt_ref"]}
            metadata = bounded_managed_output_projection({"kind": "managed_native_plugin_result/v1",
                "output": source["output"], "terminal_receipt_ref": source["terminal_receipt_ref"]}, args)
            result_refs = (_version_from_payload(source["agent_action_ref"]),)
        else:
            raise AssertionError(kind)
        record = AgentActionRecord(
            action_id=action_id, loop_id=self.loop.loop_id, turn_sequence=sequence,
            tool_call_ordinal=ordinal, tool_call_id=call_id, action_identity_kind="tool_call_id",
            action_identity_key=call_id, tool_name=tool_name, raw_arguments=json.dumps(args),
            arguments=args, expected_revision=revision - 1, state=state,
            result_refs=result_refs, result_metadata=metadata, managed_action=managed, tool_error_ref=error)
        document = ActionRecordsMechanicsMixin._action_document(
            record, action_ref=ref, loop_ref=source_loop_ref, turn_ref=turn_ref)
        self.objects[ref] = document
        self.rows.append({"logical_id": action_id, "object_type": ref.entity_type,
                          "metadata_json": json.dumps(document)})
        self.settlements.append(SimpleNamespace(payload={
            "agent_loop_id": self.loop.loop_id, "agent_turn_id": str(turn_ref.entity_id),
            "agent_action_id": action_id, "revision": revision, "settlement": state.value,
            "expected_revision": revision - 1, "tool_call_ordinal": ordinal,
            "tool_call_id": call_id, "tool_name": tool_name}))
        return ref, record

    def read(self, reader=ARCHIVE_READERS[0], **changes):
        return read_result_archive(self.gateway, None, self.loop, SimpleNamespace(sequence=30),
                                   dict(self.arguments, **changes), reader=reader)


def test_fixed_cut_uses_exact_settlements_and_ignores_read_pages_and_future_turns():
    h = History()
    h.add(1, 2, kind="program")
    h.add(0, 1)
    h.add(0, 0)
    h.add(2, 0, kind="page")
    h.add(20, 0)
    refs, first = h.read()
    assert refs == (_version_from_payload(h.arguments["archive_loop_ref"]),)
    assert [(e["turn_sequence"], e["tool_call_ordinal"]) for e in first["entries"]] == [(0, 0), (0, 1), (1, 2)]
    # Appended outcomes below the turn cut but after the anchor revision do not
    # change a frozen directory. Nor do new turns or repeated body pages.
    h.add(3, 0, revision=101)
    h.add(21, 0, revision=102)
    h.add(4, 0, kind="page", revision=103)
    assert h.read()[1] == first
    # Registered newer versions of the same action must not replace its source.
    source_ref = _version_from_payload(first["entries"][0]["agent_action_ref"])
    newer = deepcopy(h.objects[source_ref])
    newer_ref = replace(source_ref, version_id=new_id("agent_action_version"))
    newer.update(agent_action_ref=_ref_payload(newer_ref), expected_revision=100)
    h.rows.append({"logical_id": str(source_ref.entity_id), "object_type": source_ref.entity_type,
                   "metadata_json": json.dumps(newer)})
    assert h.read()[1] == first


@pytest.mark.parametrize("reader", ARCHIVE_READERS)
def test_both_reader_dispatches_return_the_same_directory(reader):
    h = History()
    h.add()
    h.add(1, kind="program")
    method = (WorkspaceExecutionMixin._read_managed_output if reader == ARCHIVE_READERS[0]
              else ProgramExecutionMixin._read_tool_program_output)
    assert method(h.gateway, None, h.loop, SimpleNamespace(sequence=30), h.arguments, "key") == h.read(reader)


@pytest.mark.parametrize("names", [ARCHIVE_READERS, ARCHIVE_READERS[:1], ARCHIVE_READERS[1:]])
def test_only_actual_available_body_readers_are_advertised(names):
    h = History(names)
    for index, status in enumerate(("returned", "failed", "outcome_unknown", "rejected")):
        h.add(0, index, status=status)
    for index, status in enumerate(("returned", "failed", "cancelled", "outcome_unknown")):
        h.add(1, index, kind="program", status=status)
    entries = h.read(names[0])[1]["entries"]
    assert [e["reader"] for e in entries[:4]] == [ARCHIVE_READERS[0] if ARCHIVE_READERS[0] in names else None, None, None, None]
    assert all(e["reader"] == (ARCHIVE_READERS[1] if ARCHIVE_READERS[1] in names else None) for e in entries[4:])
    assert entries[3]["terminal_receipt_ref"] is None
    missing = next((name for name in ARCHIVE_READERS if name not in names), None)
    if missing:
        with pytest.raises(ValueError, match="not exposed"):
            h.read(missing)


def test_locator_is_small_deterministic_optional_and_matches_parent_capsule():
    h = History()
    locator = result_archive_locator(h.loop, tool_names=ARCHIVE_READERS, before_turn_sequence=20)
    assert locator == dict(h.arguments, reader=ARCHIVE_READERS[0])
    assert set(locator) == {"reader", "archive_loop_ref", "before_turn_sequence", "offset", "max_bytes"}
    assert result_archive_locator(h.loop, tool_names=(), before_turn_sequence=20) is None
    with pytest.raises(ValueError, match="cut exceeds"):
        result_archive_locator(h.loop, tool_names=ARCHIVE_READERS, before_turn_sequence=31)


def test_zero_cut_empty_archive_and_default_page_arguments():
    h = History()
    h.add()
    args = {"archive_loop_ref": h.arguments["archive_loop_ref"], "before_turn_sequence": 0}
    refs, page = read_result_archive(h.gateway, None, h.loop, SimpleNamespace(sequence=30),
                                     args, reader=ARCHIVE_READERS[0])
    assert page["offset"] == page["total_count"] == 0
    assert page["entries"] == [] and page["next_offset"] is None
    assert _size(page) <= 10000
    assert refs == (_version_from_payload(args["archive_loop_ref"]),)
    assert _read_record(h, page, args).result_metadata == page


@pytest.mark.parametrize("case", ["unregistered", "cross_loop", "wrong_version", "cut_snapshot", "cut_reader", "catalog"])
def test_reader_rejects_non_exact_anchors_cuts_and_catalog(case):
    h = History()
    h.add()
    args = deepcopy(h.arguments)
    turn = SimpleNamespace(sequence=30)
    if case == "unregistered":
        args["archive_loop_ref"]["version_id"] = str(new_id("agent_loop_version"))
    elif case in {"cross_loop", "wrong_version"}:
        ref = _version_from_payload(args["archive_loop_ref"])
        h.objects[ref]["agent_loop_id" if case == "cross_loop" else "agent_loop_version_id"] = str(new_id(
            "agent_loop" if case == "cross_loop" else "agent_loop_version"))
    elif case == "cut_snapshot":
        args["before_turn_sequence"] = 31
        turn.sequence = 32
    elif case == "cut_reader":
        turn.sequence = 19
    else:
        catalog = build_agent_tool_catalog(tool_names=("complete_interaction", "read_managed_output"))
        h.gateway._static = lambda *a: (None, None, catalog.payload)
    with pytest.raises((ValueError, KeyError)):
        read_result_archive(h.gateway, None, h.loop, turn, args, reader=ARCHIVE_READERS[0])


def test_full_serialized_page_budget_exact_boundary_pagination_and_count_limit():
    h = History()
    for index in range(40):
        h.add(index // 4, index % 4, kind="program" if index % 2 else "managed")
    assert len(h.read(max_bytes=100000)[1]["entries"]) == 16
    offset, entries, pages = 0, [], []
    while True:
        _, page = h.read(offset=offset, max_bytes=1700)
        validate_result_archive_page(page)
        assert _size(page) <= 1700 and 1 <= len(page["entries"]) <= 16
        entries.extend(page["entries"])
        pages.append(page)
        assert h.read(offset=offset, max_bytes=_size(page))[1] == page
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert len(entries) == 40 and len(pages) > 3
    assert len({e["tool_call_id"] for e in entries}) == 40
    one = h.read(max_bytes=1000)[1]
    assert len(one["entries"]) == 1
    with pytest.raises(ValueError, match="minimum envelope"):
        h.read(max_bytes=_size(one) - 1)
    with pytest.raises(ValueError, match="minimum envelope"):
        h.read(max_bytes=1)
    _, empty = h.read(offset=40)
    assert empty["entries"] == [] and empty["next_offset"] is None
    assert h.read(offset=40, max_bytes=_size(empty))[1] == empty
    with pytest.raises(ValueError, match="minimum envelope"):
        h.read(offset=40, max_bytes=_size(empty) - 1)
    with pytest.raises(ValueError, match="outside"):
        h.read(offset=41)


def test_rebound_sixteen_entries_to_1000_bytes_preserves_complete_entry_continuation():
    h = History()
    for index in range(16):
        h.add(index)
    original = h.read(max_bytes=100000)[1]
    assert len(original["entries"]) == 16 and original["next_offset"] is None
    before = deepcopy(original)
    small = bound_result_archive_page(original, 1000)
    validate_result_archive_page(small)
    assert original == before
    assert _size(small) <= 1000 and len(small["entries"]) == 1
    assert small["next_offset"] == 1 and small["total_count"] == 16
    assert small["entries"] == original["entries"][:1]
    remaining = h.read(offset=small["next_offset"], max_bytes=100000)[1]
    assert small["entries"] + remaining["entries"] == original["entries"]
    with pytest.raises(ValueError, match="minimum envelope"):
        bound_result_archive_page(original, _size(small) - 1)
    for budget in (True, 0, -1):
        with pytest.raises(ValueError):
            bound_result_archive_page(original, budget)


@pytest.mark.parametrize("change", [
    {"offset": True}, {"offset": -1}, {"max_bytes": False}, {"max_bytes": 0},
    {"before_turn_sequence": True}, {"before_turn_sequence": -1}, {"offset_chars": 0},
    {"extra": 0}, {"archive_loop_ref": {}},
    {"agent_action_ref": {}}, {"terminal_receipt_ref": {}},
])
def test_archive_alternate_rejects_open_mixed_or_invalid_arguments(change):
    h = History()
    arguments = dict(h.arguments, **change)
    for name in ARCHIVE_READERS:
        assert not Draft7Validator(TOOL_ARGUMENT_SCHEMAS[name]).is_valid(arguments)
        with pytest.raises(ValueError):
            read_result_archive(h.gateway, None, h.loop, SimpleNamespace(sequence=30), arguments, reader=name)


def _read_record(h, page, args=None, refs=None):
    args = h.arguments if args is None else args
    return AgentActionRecord(
        action_id=stable_action_id(h.loop.loop_id, 30, "archive-read"), loop_id=h.loop.loop_id,
        turn_sequence=30, tool_call_ordinal=0, tool_call_id="archive-read",
        action_identity_kind="tool_call_id", action_identity_key="archive-read",
        tool_name="read_managed_output", raw_arguments=json.dumps(args), arguments=args,
        expected_revision=100, state=AgentLoopState.ACTION_APPLIED,
        result_refs=(_version_from_payload(h.arguments["archive_loop_ref"]),) if refs is None else refs,
        result_metadata=page)


@pytest.mark.parametrize("tamper", [None, "refs", "cut", "anchor", "offset", "next", "count", "reader", "budget", "entry", "order", "unknown"])
def test_saved_archive_records_are_closed_and_bound_to_request(tamper):
    h = History()
    h.add(0)
    h.add(1, kind="program")
    _, page = h.read()
    args, refs = deepcopy(h.arguments), None
    if tamper == "refs":
        refs = ()
    elif tamper == "cut":
        page["before_turn_sequence"] += 1
    elif tamper == "anchor":
        page["archive_loop_ref"]["version_id"] = str(new_id("agent_loop_version"))
    elif tamper == "offset":
        args["offset"] = 1
    elif tamper == "next":
        page["next_offset"] = True
    elif tamper == "count":
        page["entries"] *= 9
        page["total_count"] = 18
    elif tamper == "reader":
        page["reader"] = ARCHIVE_READERS[1]
    elif tamper == "budget":
        args["max_bytes"] = _size(page) - 1
    elif tamper == "entry":
        page["entries"][0]["status"] = "failed"
    elif tamper == "order":
        page["entries"].reverse()
    elif tamper == "unknown":
        page["extra"] = 1
    if tamper:
        with pytest.raises(AgentLoopProtocolError):
            _read_record(h, page, args, refs)
    else:
        assert _read_record(h, page).result_metadata == page


def test_original_output_schemas_and_saved_pages_still_work_without_default_exposure():
    h = History()
    h.add()
    h.add(1, kind="page")
    page_ref, = [ref for ref, value in h.objects.items() if value.get("tool_name") == "read_managed_output"]
    saved = h.lifecycle.hydrate_action(page_ref)
    assert saved.result_metadata["kind"] == "managed_output_page/v1"
    assert Draft7Validator(TOOL_ARGUMENT_SCHEMAS[ARCHIVE_READERS[0]]).is_valid(saved.arguments)
    assert all(reader not in build_agent_tool_catalog().tool_names for reader in ARCHIVE_READERS)
    for reader in ARCHIVE_READERS:
        schema = TOOL_ARGUMENT_SCHEMAS[reader]
        Draft7Validator.check_schema(schema)
        assert Draft7Validator(schema).is_valid(h.arguments)
        original = schema["oneOf"][0]
        assert original["additionalProperties"] is False
        assert original["properties"]["max_bytes"]["maximum"] == 10000


@pytest.mark.parametrize("reader", ARCHIVE_READERS)
@pytest.mark.parametrize("tamper", [None, "extra_page", "extra_entry", "ref_type", "status",
                                    "wrong_body_reader", "mixed_args", "refs", "page_reader", "tool", "count"])
def test_registered_v2_schema_accepts_closed_archive_records_and_rejects_tampering(reader, tamper):
    from cpn.rpnh.agent_tasks import agent_task_catalog
    from cpn.rpnh.registry.schema_catalog import SchemaGovernanceError

    schema = json.loads((Path(__file__).resolve().parents[1] / "cpn/schemas/registry_v1/agent_action.v2.schema.json").read_text())
    Draft7Validator.check_schema(schema)
    h = History()
    for ordinal, status in enumerate(("returned", "failed", "outcome_unknown", "rejected")):
        h.add(0, ordinal, status=status)
    for ordinal, status in enumerate(("returned", "failed", "cancelled", "outcome_unknown")):
        h.add(1, ordinal, kind="program", status=status)
    _, page = h.read()
    record = _read_record(h, page)
    if reader != ARCHIVE_READERS[0]:
        page = dict(page, reader=reader)
        record = replace(record, tool_name=reader, result_metadata=page)
    doc = ActionRecordsMechanicsMixin._action_document(
        record, action_ref=VersionRef("agent_action/v2", TypedId.parse(record.action_id), new_id("agent_action_version")),
        loop_ref=h.lifecycle.loop_ref(h.loop), turn_ref=_ref("agent_turn/v1", "agent_turn", "agent_turn_version"))
    if tamper == "extra_page":
        doc["result_metadata"]["extra"] = 1
    elif tamper == "extra_entry":
        doc["result_metadata"]["entries"][0]["extra"] = 1
    elif tamper == "ref_type":
        doc["result_metadata"]["archive_loop_ref"]["entity_type"] = "agent_action/v2"
    elif tamper == "status":
        doc["result_metadata"]["entries"][0]["status"] = "unknown-status"
    elif tamper == "wrong_body_reader":
        doc["result_metadata"]["entries"][1]["reader"] = ARCHIVE_READERS[0]
    elif tamper == "mixed_args":
        doc["arguments"]["offset_chars"] = 0
    elif tamper == "refs":
        doc["result_refs"] = [doc["agent_action_ref"]]
    elif tamper == "page_reader":
        doc["result_metadata"]["reader"] = next(n for n in ARCHIVE_READERS if n != reader)
    elif tamper == "tool":
        doc["tool_name"] = "read_file"
    elif tamper == "count":
        doc["result_metadata"]["entries"] *= 3
    assert Draft7Validator(schema).is_valid(doc) is (tamper is None)
    catalog = agent_task_catalog()
    if tamper is None:
        catalog.validate_schema_ref("registry_v1/agent_action/v2", doc)
    else:
        with pytest.raises(SchemaGovernanceError):
            catalog.validate_schema_ref("registry_v1/agent_action/v2", doc)
