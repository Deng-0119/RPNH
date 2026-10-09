"""Pure JSON contract fixtures; no imports or execution of RPNH/Codex product code."""
from pathlib import Path
from copy import deepcopy
import hashlib
import json
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ("0.155.0", "0.161.0")
THREAD = "c67ce954-8d92-5dd2-babc-dd9fdb9046a6"
TURN = "28c89012-bdb9-5ec7-af6e-4f3c4f0992f9"
USER = "1c47d847-655d-536f-a78b-b6bdfb7ad8e8"
AGENT = "f3c97bba-1232-5f38-bf9b-ae055864db29"
ITEMS = [
    {"type": "userMessage", "id": USER, "content": [{"type": "text", "text": "Synthetic question"}]},
    {"type": "agentMessage", "id": AGENT, "text": "Synthetic reply"},
]
TURN_VALUE = {"id": TURN, "items": ITEMS, "itemsView": "full", "status": "completed",
              "error": None, "startedAt": None, "completedAt": None, "durationMs": None}
THREAD_VALUE = {"id": THREAD, "sessionId": THREAD, "preview": "Synthetic question", "name": None,
                "ephemeral": False, "model": "fixture-model", "modelProvider": "rpnh",
                "createdAt": 0, "updatedAt": 0, "recencyAt": 0, "cwd": "/synthetic/fixture",
                "cliVersion": "0.155.0", "source": "appServer", "status": {"type": "idle"},
                "turns": [], "projectId": None, "historyMode": "paginated"}
RESUME = {"thread": THREAD_VALUE, "turnsBackwardsCursor": "opaque-fixture-turns",
          "itemsBackwardsCursor": "opaque-fixture-items", "model": "fixture-model",
          "modelProvider": "rpnh", "cwd": "/synthetic/fixture", "approvalPolicy": "never",
          "approvalsReviewer": "user", "sandbox": {"type": "dangerFullAccess"},
          "reasoningEffort": "medium", "serviceTier": None, "instructionSources": []}


def validator(version, name):
    value = json.loads((ROOT / "upstream" / version / f"{name}.json").read_text())
    Draft7Validator.check_schema(value)
    return Draft7Validator(value)


def main():
    cases = []

    def check(name, schema, value, expected, version, note=""):
        errors = list(validator(version, schema).iter_errors(value))
        observed = not errors
        assert observed == expected, (name, version, [e.message for e in errors])
        cases.append({"name": name, "schema": schema, "version": version,
                      "schema_accepts": observed, "expected_schema_accepts": expected,
                      "interpretation": note})

    for version in VERSIONS:
        for view in ("notLoaded", "summary", "full"):
            turn = deepcopy(TURN_VALUE)
            turn["itemsView"] = view
            if view == "notLoaded": turn["items"] = []
            check(f"existing-safe-turn-{view}", "ThreadTurnsListResponse",
                  {"data": [turn], "nextCursor": None, "backwardsCursor": "opaque"}, True, version)
        for timestamps in ("omitted", "null", "actual-i64"):
            entries = [{"turnId": TURN, "item": item} for item in ITEMS]
            if timestamps != "omitted":
                for e in entries:
                    e.update(startedAtMs=None if timestamps == "null" else 1000,
                             completedAtMs=None if timestamps == "null" else 2000)
            check(f"safe-items-time-{timestamps}", "ThreadItemsListResponse",
                  {"data": entries, "nextCursor": None, "backwardsCursor": "opaque"}, True, version,
                  "0.155 accepts extra JSON keys; this is not proof of Rust live-notification acceptance")
        for optional in (False, True):
            r = deepcopy(RESUME)
            if optional: r.update(collaborationMode=None, disabledPluginIds=[])
            check(f"resume-optionals-{optional}", "ThreadResumeResponse", r, True, version,
                  "Synthetic shape matches frozen handler; Rust fallback consumption separately source-audited")
        for cursor in (None, "opaque"):
            check(f"string-null-cursor-{cursor is None}", "ThreadItemsListParams",
                  {"threadId": THREAD, "cursor": cursor}, True, version)
        for order in ("asc", "desc"):
            check(f"object-item-anchor-{order}", "ThreadItemsListParams",
                  {"threadId": THREAD, "turnId": TURN, "cursor": {"type": "item", "itemId": USER},
                   "sortDirection": order, "limit": 1}, version == "0.161.0", version)
        check("object-rejected-on-turns", "ThreadTurnsListParams",
              {"threadId": THREAD, "cursor": {"type": "item", "itemId": USER}}, False, version)
        check("invalid-anchor-discriminator", "ThreadItemsListParams",
              {"threadId": THREAD, "turnId": TURN, "cursor": {"type": "turn", "itemId": USER}},
              False, version)
        check("missing-required-itemId", "ThreadItemsListParams",
              {"threadId": THREAD, "turnId": TURN, "cursor": {"type": "item"}}, False, version)
        check("numeric-itemId", "ThreadItemsListParams",
              {"threadId": THREAD, "turnId": TURN, "cursor": {"type": "item", "itemId": 1}}, False, version)
        check("string-does-not-prove-uuid", "ThreadResumeResponse",
              {**deepcopy(RESUME), "thread": {**THREAD_VALUE, "id": "ses_" + "a" * 32}}, True, version,
              "Schema blind spot: real Rust ThreadId requires UUID; frozen product mapping already fixed")
    for label, extra in (("missing-turnId", {}), ("empty-turnId", {"turnId": ""}),
                         ("null-turnId", {"turnId": None})):
        check(label, "ThreadItemsListParams",
              {"threadId": THREAD, "cursor": {"type": "item", "itemId": USER}, **extra}, True, "0.161.0",
              "Schema accepts; upstream runtime rejects. Proposed semantic oracle must reject.")
    check("empty-itemId", "ThreadItemsListParams",
          {"threadId": THREAD, "turnId": TURN, "cursor": {"type": "item", "itemId": ""}},
          True, "0.161.0", "Schema accepts; upstream segment_paging rejects empty or nonmember item.")
    check("unknown-object-field", "ThreadItemsListParams",
          {"threadId": THREAD, "turnId": TURN, "cursor": {"type": "item", "itemId": USER, "root": "/elsewhere"}},
          True, "0.161.0", "Schema is permissive; proposed strict RPNH decoder rejects extra fields.")
    check("uint32-format-is-not-bound", "ThreadItemsListParams", {"threadId": THREAD, "limit": 2**32},
          True, "0.161.0", "Draft7 numeric format does not enforce Rust u32; current page_limit already does.")
    check("relative-cwd-is-schema-string", "ThreadResumeResponse",
          {**RESUME, "cwd": "relative"}, True, "0.161.0",
          "Rust AbsolutePathBuf requires absolute; existing state.cwd/root are resolved paths.")
    check("invalid-history-time-type", "ThreadItemsListResponse",
          {"data": [{"turnId": TURN, "item": ITEMS[0], "startedAtMs": "now"}]}, False, "0.161.0")
    result = {"scope": "Pure synthetic JSON fixtures, no product code import, Registry, Rust/TUI, installation or model calls",
              "count": len(cases), "passed": len(cases), "failed": 0, "cases": cases}
    (ROOT / "probes" / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (ROOT / "probes" / "synthetic-resume.json").write_text(json.dumps(RESUME, indent=2) + "\n")
    print(json.dumps({"passed": len(cases), "failed": 0, "scope": result["scope"]}))


if __name__ == "__main__":
    main()
