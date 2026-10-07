"""Exact managed-v3 output pages; saved managed metadata remains unchanged."""

from collections.abc import Mapping
import json
import re


MANAGED_OUTPUT_READER = "read_managed_output"
MANAGED_OUTPUT_PAGE_KIND = "managed_output_page/v1"


def serialized_managed_json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def validate_managed_locator(action_ref, receipt_ref):
    if (not isinstance(action_ref, Mapping)
            or set(action_ref) != {"entity_type", "logical_id", "version_id"}
            or action_ref.get("entity_type") != "agent_action/v3"
            or re.fullmatch(r"agent_action:[a-f0-9]{32}",
                            str(action_ref.get("logical_id"))) is None
            or re.fullmatch(r"agent_action_version:[a-f0-9]{32}",
                            str(action_ref.get("version_id"))) is None
            or not isinstance(receipt_ref, Mapping)
            or set(receipt_ref) != {"resource_id", "resource_version_id"}
            or any(re.fullmatch(prefix + r":[a-f0-9]{32}",
                                str(receipt_ref.get(field))) is None
                   for field, prefix in (("resource_id", "resource"),
                                         ("resource_version_id", "resource_version")))):
        raise ValueError("managed output requires exact v3 action and terminal receipt refs")


def validate_managed_output_page(page):
    if (not isinstance(page, Mapping)
            or set(page) != {"kind", "agent_action_ref", "terminal_receipt_ref",
                             "reader", "content", "offset_chars",
                             "next_offset_chars", "total_chars", "truncated"}
            or page.get("kind") != MANAGED_OUTPUT_PAGE_KIND
            or page.get("reader") != MANAGED_OUTPUT_READER):
        raise ValueError("managed output page has invalid fields")
    validate_managed_locator(page["agent_action_ref"], page["terminal_receipt_ref"])
    offset, total, following = (page[key] for key in
                               ("offset_chars", "total_chars", "next_offset_chars"))
    if (any(isinstance(n, bool) or not isinstance(n, int) or n < 0
            for n in (offset, total))
            or offset > total or not isinstance(page["content"], str)
            or not isinstance(page["truncated"], bool)):
        raise ValueError("managed output page range is invalid")
    end = offset + len(page["content"])
    if (end > total or (end == offset and end < total)
            or (end == total and (following is not None or page["truncated"]))
            or (end < total and (isinstance(following, bool)
                                or not isinstance(following, int)
                                or following != end or not page["truncated"]))):
        raise ValueError("managed output page continuation is invalid")


def _fit_page(page, max_bytes):
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
    # Test the complete page separately: EOF's null continuation has a
    # different size from an integer. Partial-page sizes are monotone.
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


def bound_managed_output_page(page, max_bytes):
    """Rebound an already exact page without breaking its continuation."""
    validate_managed_output_page(page)
    return _fit_page(page, max_bytes)


def bounded_managed_output_projection(result_metadata, arguments):
    if (not isinstance(result_metadata, Mapping)
            or set(result_metadata) != {"kind", "output", "terminal_receipt_ref"}
            or result_metadata.get("kind") != "managed_native_plugin_result/v1"
            or not isinstance(arguments, Mapping)
            or not {"agent_action_ref", "terminal_receipt_ref"}.issubset(arguments)
            or set(arguments) - {"agent_action_ref", "terminal_receipt_ref",
                                 "offset_chars", "max_bytes"}):
        raise ValueError("managed output requires returned metadata and closed page arguments")
    action_ref, receipt_ref = arguments["agent_action_ref"], arguments["terminal_receipt_ref"]
    validate_managed_locator(action_ref, receipt_ref)
    if receipt_ref != result_metadata["terminal_receipt_ref"]:
        raise ValueError("managed output terminal receipt differs from its exact action")
    text = serialized_managed_json(result_metadata["output"])
    offset = arguments.get("offset_chars", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= len(text):
        raise ValueError("managed output offset_chars is outside the output")
    return _fit_page({
        "kind": MANAGED_OUTPUT_PAGE_KIND,
        "agent_action_ref": dict(action_ref),
        "terminal_receipt_ref": dict(receipt_ref),
        "reader": MANAGED_OUTPUT_READER,
        "content": text[offset:], "offset_chars": offset,
        "next_offset_chars": None, "total_chars": len(text), "truncated": False,
    }, arguments.get("max_bytes", 10_000))


def render_managed_output(result_metadata, action_ref, *, reader_available, max_bytes):
    """Advertise a reader only when the exact loop catalog exposes it."""
    arguments = {"agent_action_ref": action_ref,
                 "terminal_receipt_ref": result_metadata["terminal_receipt_ref"],
                 "max_bytes": max_bytes}
    if reader_available:
        return bounded_managed_output_projection(result_metadata, arguments)
    validate_managed_locator(action_ref, result_metadata["terminal_receipt_ref"])
    result = dict(result_metadata, agent_action_ref=dict(action_ref), reader=None)
    if len(serialized_managed_json(result).encode("utf-8")) > max_bytes:
        raise ValueError("managed output exceeds the visible budget and read_managed_output is not exposed")
    return result
