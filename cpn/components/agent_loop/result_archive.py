"""Read-only, exact-loop result directories over existing Registry history.

The registered loop version and exclusive turn cut are the directory identity.
Only original outcomes settled by that loop revision belong to the directory;
neither later settlement nor reading an output page adds an entry.
"""
from collections.abc import Mapping
import json
import re

from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
from .managed_output import serialized_managed_json


ARCHIVE_PAGE_KIND = "tool_result_archive_page/v1"
ARCHIVE_READERS = ("read_managed_output", "read_tool_program_output")
ARCHIVE_MAX_ENTRIES = 16
ARCHIVE_ARGUMENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "archive_loop_ref": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "entity_type": {"const": "agent_loop/v1"},
                "logical_id": {"type": "string", "pattern": "^agent_loop:[a-f0-9]{32}$"},
                "version_id": {"type": "string", "pattern": "^agent_loop_version:[a-f0-9]{32}$"},
            },
            "required": ["entity_type", "logical_id", "version_id"],
        },
        "before_turn_sequence": {"type": "integer", "minimum": 0},
        "offset": {"type": "integer", "minimum": 0, "default": 0},
        "max_bytes": {"type": "integer", "minimum": 1, "default": 10000},
    },
    "required": ["archive_loop_ref", "before_turn_sequence"],
}


def _exact_ref(raw, entity_type, logical_kind, version_kind):
    return (isinstance(raw, Mapping)
            and set(raw) == {"entity_type", "logical_id", "version_id"}
            and raw.get("entity_type") == entity_type
            and _id(raw.get("logical_id"), logical_kind)
            and _id(raw.get("version_id"), version_kind))


def _id(value, kind):
    return isinstance(value, str) and re.fullmatch(kind + r":[a-f0-9]{32}", value) is not None


def _resource_ref(raw):
    return (isinstance(raw, Mapping)
            and set(raw) == {"resource_id", "resource_version_id"}
            and _id(raw.get("resource_id"), "resource")
            and _id(raw.get("resource_version_id"), "resource_version"))


def validate_archive_arguments(arguments):
    if (not isinstance(arguments, Mapping)
            or not {"archive_loop_ref", "before_turn_sequence"}.issubset(arguments)
            or set(arguments) - set(ARCHIVE_ARGUMENT_SCHEMA["properties"])
            or not _exact_ref(arguments["archive_loop_ref"], "agent_loop/v1",
                              "agent_loop", "agent_loop_version")
            or any(type(arguments.get(key, default)) is not int
                   or arguments.get(key, default) < minimum
                   for key, default, minimum in (("before_turn_sequence", -1, 0),
                                                 ("offset", 0, 0), ("max_bytes", 10000, 1)))):
        raise ValueError("result archive requires exact closed loop/cut/page arguments")


def result_archive_locator(loop, *, tool_names, before_turn_sequence):
    """Return {reader, archive_loop_ref, before_turn_sequence, offset, max_bytes}, or None.

    The caller supplies the exact loop's selected catalog. No reader is added
    to that catalog. Use the returned reader with the remaining fields as args.
    """
    arguments = {
        "archive_loop_ref": {"entity_type": "agent_loop/v1", "logical_id": loop.loop_id,
                             "version_id": loop.loop_version_id},
        "before_turn_sequence": before_turn_sequence,
        "offset": 0, "max_bytes": 10000,
    }
    validate_archive_arguments(arguments)
    if before_turn_sequence > loop.next_turn_sequence:
        raise ValueError("result archive cut exceeds its anchored loop snapshot")
    reader = next((name for name in ARCHIVE_READERS if name in tool_names), None)
    return dict(arguments, reader=reader) if reader is not None else None


def validate_result_archive_page(page):
    fields = {"kind", "archive_loop_ref", "before_turn_sequence", "reader",
              "offset", "next_offset", "total_count", "entries"}
    if (not isinstance(page, Mapping) or set(page) != fields
            or page.get("kind") != ARCHIVE_PAGE_KIND or page.get("reader") not in ARCHIVE_READERS):
        raise ValueError("result archive page has invalid fields")
    validate_archive_arguments({key: page[key] for key in
                                ("archive_loop_ref", "before_turn_sequence", "offset")})
    entries, offset, total = page["entries"], page["offset"], page["total_count"]
    if (not isinstance(entries, list) or len(entries) > ARCHIVE_MAX_ENTRIES
            or type(total) is not int or not 0 <= offset <= total
            or offset + len(entries) > total
            or (not entries and offset < total)):
        raise ValueError("result archive page range is invalid")
    end = offset + len(entries)
    following = page["next_offset"]
    if ((end == total and following is not None)
            or (end < total and (type(following) is not int or following != end))):
        raise ValueError("result archive page continuation is invalid")
    prior = None
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("result archive entry must be an object")
        ref = entry.get("agent_action_ref")
        managed = isinstance(ref, Mapping) and ref.get("entity_type") == "agent_action/v3"
        resource_key = "terminal_receipt_ref" if managed else "output_resource_ref"
        expected = {"agent_action_ref", resource_key, "tool_name", "status", "reader",
                    "turn_sequence", "tool_call_id", "tool_call_ordinal"}
        if (set(entry) != expected
                or not _exact_ref(ref, "agent_action/v3" if managed else "agent_action/v2",
                                  "agent_action", "agent_action_version")
                or not isinstance(entry["tool_name"], str) or not entry["tool_name"]
                or not isinstance(entry["tool_call_id"], str) or not entry["tool_call_id"]
                or type(entry["turn_sequence"]) is not int
                or not 0 <= entry["turn_sequence"] < page["before_turn_sequence"]
                or type(entry["tool_call_ordinal"]) is not int or entry["tool_call_ordinal"] < 0
                or entry["status"] not in ({"returned", "failed", "outcome_unknown", "rejected"}
                                           if managed else {"returned", "failed", "cancelled", "outcome_unknown"})
                or (not managed and entry["tool_name"] != "run_tool_program")
                or (entry[resource_key] is None and not (managed and entry["status"] == "rejected"))
                or (managed and entry["status"] == "rejected" and entry[resource_key] is not None)
                or (entry[resource_key] is not None and not _resource_ref(entry[resource_key]))
                or entry["reader"] not in (None, ARCHIVE_READERS[0] if managed else ARCHIVE_READERS[1])
                or (managed and entry["status"] != "returned" and entry["reader"] is not None)):
            raise ValueError("result archive entry has invalid closed outcome fields")
        order = (entry["turn_sequence"], entry["tool_call_ordinal"])
        if prior is not None and order <= prior:
            raise ValueError("result archive entries are not in deterministic turn/call order")
        prior = order


def bound_result_archive_page(page, max_bytes):
    """Shrink an archive page without dropping identities or skipping entries."""
    validate_result_archive_page(page)
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


def _entries(lifecycle, anchor, cutoff, tool_names):
    """Resolve settlement versions, never mutable latest action heads."""
    turns = {event.payload["agent_turn_id"]: event
             for event in lifecycle.turn_events(anchor)
             if event.payload["sequence"] < cutoff and event.payload["revision"] <= anchor.revision}
    entries = []
    for event in lifecycle.core.event_store.list_events_by_aggregate(
            anchor.loop_id, event_types=("agent_action_settled/v1",)):
        settled = event.payload
        turn_event = turns.get(settled["agent_turn_id"])
        if settled["revision"] > anchor.revision or turn_event is None:
            continue
        if (settled["agent_loop_id"] != anchor.loop_id
                or turn_event.payload["agent_loop_id"] != anchor.loop_id):
            raise ValueError("result archive history crossed its anchored loop")
        turn_ref = lifecycle.turn_ref_from_event(turn_event)
        candidates = []
        for action_type in ("agent_action/v3", "agent_action/v2"):
            for row in lifecycle.core.event_store.object_rows_by_logical(
                    settled["agent_action_id"], object_type=action_type):
                document = json.loads(row["metadata_json"])
                if (action_type == "agent_action/v2"
                        and (document.get("tool_name") != "run_tool_program"
                             or not isinstance(document.get("result_metadata"), Mapping)
                             or document["result_metadata"].get("kind") != "tool_program_result/v1")):
                    continue
                if (document["agent_turn_ref"] != _ref_payload(turn_ref)
                        or document["tool_call_ordinal"] != settled["tool_call_ordinal"]
                        or document["expected_revision"] != settled["expected_revision"]
                        or document["state"] != settled["settlement"]):
                    continue
                source_loop = lifecycle.hydrate_loop(_version_from_payload(document["agent_loop_ref"]))
                if source_loop.loop_id == anchor.loop_id and source_loop.revision == settled["revision"]:
                    candidates.append(document)
        if not candidates:
            continue  # Other tools and output read pages are not original results.
        if len(candidates) != 1:
            raise ValueError("result archive outcome lacks one exact settlement version")
        document, = candidates
        ref = _version_from_payload(document["agent_action_ref"])
        action = lifecycle.hydrate_action(ref)
        if (action.loop_id != anchor.loop_id or action.turn_sequence != turn_event.payload["sequence"]
                or action.tool_call_id != settled["tool_call_id"] or action.tool_name != settled["tool_name"]):
            raise ValueError("result archive outcome differs from its immutable turn/settlement")
        managed = ref.entity_type == "agent_action/v3"
        metadata = action.managed_action if managed else action.result_metadata
        status = metadata["outcome" if managed else "status"]
        reader = ARCHIVE_READERS[0] if managed else ARCHIVE_READERS[1]
        resource_key = "terminal_receipt_ref" if managed else "output_resource_ref"
        entries.append({
            "agent_action_ref": _ref_payload(ref), resource_key: metadata[resource_key],
            "tool_name": action.tool_name, "status": status,
            "reader": reader if reader in tool_names and (not managed or status == "returned") else None,
            "turn_sequence": action.turn_sequence, "tool_call_id": action.tool_call_id,
            "tool_call_ordinal": action.tool_call_ordinal,
        })
    return sorted(entries, key=lambda item: (item["turn_sequence"], item["tool_call_ordinal"]))


def read_result_archive(gateway, execution, loop, turn, arguments, *, reader):
    """Return ((exact_archive_loop_ref,), closed_page) for either optional reader.

    Uses only existing Registry reads; normal reader action settlement is owned
    by the caller. The complete canonical JSON page (including envelope) fits
    max_bytes, or an explicit minimum-envelope error is raised.
    """
    validate_archive_arguments(arguments)
    ref = _version_from_payload(arguments["archive_loop_ref"])
    lifecycle = gateway.mechanical_lifecycle
    anchor = lifecycle.hydrate_loop(ref)
    cutoff = arguments["before_turn_sequence"]
    if (anchor.loop_id != loop.loop_id or str(ref.entity_id) != anchor.loop_id
            or str(ref.version_id) != anchor.loop_version_id
            or cutoff > anchor.next_turn_sequence or cutoff > turn.sequence):
        raise ValueError("result archive requires an exact same-loop anchor and previous turn cut")
    # Read the catalog actually registered for this loop, not the default tool set.
    from .tool_catalog import parse_agent_tool_catalog
    catalog_ref, _, payload = gateway._static(gateway._context(loop), "optional_agent_tool_catalog")
    if catalog_ref != loop.tool_catalog_ref or catalog_ref != anchor.tool_catalog_ref:
        raise ValueError("result archive crossed its exact loop tool catalog")
    names = parse_agent_tool_catalog(payload).tool_names
    if reader not in ARCHIVE_READERS or reader not in names:
        raise ValueError("result archive reader is not exposed in this loop catalog")
    entries = _entries(lifecycle, anchor, cutoff, names)
    offset, budget = arguments.get("offset", 0), arguments.get("max_bytes", 10000)
    if offset > len(entries):
        raise ValueError("result archive offset is outside the directory")
    def page(count):
        end = offset + count
        return {"kind": ARCHIVE_PAGE_KIND, "archive_loop_ref": _ref_payload(ref),
                "before_turn_sequence": cutoff, "reader": reader,
                "offset": offset, "next_offset": end if end < len(entries) else None,
                "total_count": len(entries), "entries": entries[offset:end]}
    # Complete envelopes include EOF's differently sized null continuation.
    return (ref,), bound_result_archive_page(
        page(min(ARCHIVE_MAX_ENTRIES, len(entries) - offset)), budget)
