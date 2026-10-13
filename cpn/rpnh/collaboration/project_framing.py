"""Pure bounded JSON-line responses for the controlled project transports.

This is a wire budget, not permission to read/export more Registry material.
"""
from __future__ import annotations

import json
import math

from .share_packages import DEFAULT_LIMITS

# Includes the final LF. Independent Base64 padding costs at most four bytes
# per package. SubnetExportResult/v1 has only fixed fields and <=63 digests;
# 8192 bytes covers that metadata, the envelope, array punctuation and LF.
# Revisit this allowance if the export-result wire schema grows.
MAX_RESPONSE_BYTES = (4 * ((DEFAULT_LIMITS.closure_bytes + 2) // 3)
                      + 4 * DEFAULT_LIMITS.packages + 8192)


class ResponseFrameError(ValueError):
    """A transport error; the original operation may already have committed."""


def response_error(code):
    return {"ok": False, "error": code,
            "message": "Response unavailable; the operation may already have committed. "
                       "Inspect its receipt before retrying; do not retry with a new command ID."}


def _envelope(value):
    if type(value) is not dict or type(value.get("ok")) is not bool:
        raise ResponseFrameError("invalid_response_envelope")
    fields = {"ok", "result"} if value["ok"] else {"ok", "error", "message"}
    if set(value) != fields or (not value["ok"] and
            (type(value["error"]) is not str or type(value["message"]) is not str)):
        raise ResponseFrameError("invalid_response_envelope")
    return value


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ResponseFrameError("duplicate_response_key")
        value[key] = item
    return value


def _constant(value):
    raise ResponseFrameError("nonfinite_response_number")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ResponseFrameError("nonfinite_response_number")
    return result


def read_response(stream, *, max_bytes=MAX_RESPONSE_BYTES):
    """Consume only the first LF-terminated frame, using a bounded max+1 read."""
    raw = stream.readline(max_bytes + 1)
    if not raw:
        raise ResponseFrameError("empty_response_eof")
    if len(raw) > max_bytes:
        raise ResponseFrameError("response_too_large")
    if not raw.endswith(b"\n"):
        raise ResponseFrameError("incomplete_response_frame")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_constant, parse_float=_float)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ResponseFrameError("invalid_response_json") from exc
    return _envelope(value)


def encode_response(response, *, max_bytes=MAX_RESPONSE_BYTES):
    """Encode once, replacing unavailable results with a short transport error.

    Never dispatch/retry an operation here. Canonical ordering/ASCII escaping
    and default=str match Registry canonical_json; nonfinite numbers fail closed.
    """
    def encode(value):
        return json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), default=str,
                          allow_nan=False).encode("utf-8") + b"\n"
    try:
        raw = encode(_envelope(response))
    except (ValueError, TypeError, RecursionError):
        raw = encode(response_error("invalid_response_payload"))
    if len(raw) > max_bytes:
        raw = encode(response_error("response_too_large"))
    if len(raw) > max_bytes:
        raise ResponseFrameError("response_limit_too_small_for_error")
    return raw
