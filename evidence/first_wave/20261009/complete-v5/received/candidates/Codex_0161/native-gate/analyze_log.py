"""Body-free completeness check; native visual review is always separate."""
from pathlib import Path
import argparse
import json
from uuid import NAMESPACE_URL, uuid5


def analyze(rows):
    problems = []
    starts = [r for r in rows if r.get("phase") == "start"]
    if len(starts) != 1:
        return {"status": "INCOMPLETE", "problems": ["Exactly one start record required"]}
    start = starts[0]
    turns = start["fixture"]["turns"]
    requests = {(r["id"], r["method"]): r for r in rows if r.get("phase") == "request"}
    responses = [r for r in rows if r.get("phase") == "response"]
    if any(r.get("phase") in ("execution_denied", "child_launch_denied") for r in rows):
        problems.append("Unexpected write/child request; inspect and stop lane")
    if any(r.get("error") is not None for r in responses):
        problems.append("RPC error observed")
    resumed = [r for r in responses if r.get("method") == "thread/resume" and r.get("error") is None]
    if not resumed:
        return {"status": "INCOMPLETE", "problems": problems + ["No successful resume"]}
    thread = resumed[0].get("thread_id")
    if not thread or any(r.get("thread_id") != thread for r in resumed):
        problems.append("Missing or changed thread identity")
    expected_status = "active" if start["fixture"]["pending_start"] else "idle"
    if any((r.get("thread_status") or {}).get("type") != expected_status for r in resumed):
        problems.append("Unexpected pending/cold status")
    def public(ordinal, kind):
        return str(uuid5(NAMESPACE_URL, f"rpnh:codex:{thread}:{ordinal}:active-{kind}"))
    if len(resumed) != 1:
        problems.append("Multiple resume chains require separate evidence logs")
    baseline = resumed[0].get("itemsBackwardsCursor") or {}
    expected_cut = (baseline.get("cut_ordinal"), baseline.get("boundary_event"))
    tokens = {}
    # Verify chronological per-query chains. Dynamic turn metadata requests
    # may interleave item requests, but neither chain may jump or replay.
    for record in rows:
        method = record.get("method")
        if record.get("phase") == "response" and method == "thread/resume" and record.get("error") is None:
            tokens = {"thread/items/list": (record.get("itemsBackwardsCursor") or {}).get("token_sha256"),
                      "thread/turns/list": (record.get("turnsBackwardsCursor") or {}).get("token_sha256")}
        elif method in {"thread/items/list", "thread/turns/list"}:
            if record.get("phase") == "request":
                edge = record.get("cursor_edge") or {}
                if not tokens.get(method) or edge.get("token_sha256") != tokens.get(method):
                    problems.append("Request does not consume the preceding query continuation")
                if (edge.get("cut_ordinal"), edge.get("boundary_event")) != expected_cut:
                    problems.append("Request cut differs from resume cut")
            elif record.get("phase") == "response" and record.get("error") is None:
                edge = record.get("nextCursor") or {}
                if edge and edge.get("token_sha256") == tokens.get(method):
                    problems.append("Query continuation did not advance")
                tokens[method] = edge.get("token_sha256")
    if any(tokens.values()):
        problems.append("Unconsumed final continuation remains")
    expected_turns = {public(n, "turn") for n in range(1, turns + 1)}
    expected_items = {public(n, k): public(n, "turn") for n in range(1, turns + 1) for k in ("user", "agent")}
    seen_turns, seen_items, cuts = set(), set(), set()
    page_methods = {"thread/turns/list", "thread/items/list"}
    initial_turn_requests = [r for r in rows if r.get("phase") == "request" and r.get("method") == "thread/turns/list"]
    if not initial_turn_requests or any(initial_turn_requests[0].get(k) != v for k, v in
                                       (("limit", 5), ("order", "desc"), ("view", "notLoaded"))):
        problems.append("Initial turn metadata request must be desc/notLoaded/limit5")
    for record in responses:
        method = record.get("method")
        for key in ("turnsBackwardsCursor", "itemsBackwardsCursor", "nextCursor", "backwardsCursor"):
            edge = record.get(key)
            if edge:
                cuts.add((edge["cut_ordinal"], edge["boundary_event"]))
        if method not in page_methods or record.get("error") is not None:
            continue
        req = requests.get((record["id"], method), {})
        requested_limit = req.get("limit")
        if type(requested_limit) is not int or not 1 <= requested_limit <= 100:
            problems.append("Missing/out-of-range actual page limit")
        elif record["count"] > requested_limit:
            problems.append("Response exceeds requested page limit")
        ids = record.get("ids", [])
        if len(ids) != record.get("count") or len(ids) != len(set(ids)):
            problems.append("Inconsistent/duplicate IDs within page")
        if method == "thread/turns/list":
            seen_turns.update(ids)
        else:
            if seen_items.intersection(ids):
                problems.append("Repeated items across descending stock pages")
            incoming = req.get("cursor_edge") or {}
            outgoing = record.get("nextCursor") or {}
            if incoming.get("token_sha256") and incoming.get("token_sha256") == outgoing.get("token_sha256"):
                problems.append("Item continuation did not advance")
            if not ids and outgoing:
                problems.append("Empty item page advertises continuation")
            if req.get("order") != "desc":
                problems.append("Stock hydration item request is not desc")
            owners = record.get("item_turn_ids", [])
            if len(owners) != len(ids) or any(expected_items.get(i) != owner for i, owner in zip(ids, owners)):
                problems.append("Item/turn association mismatch")
            seen_items.update(ids)
    if len(cuts) != 1:
        problems.append("Resume/page cuts disagree or are missing")
    if seen_turns != expected_turns:
        problems.append("Turn coverage incomplete or contains unexpected IDs")
    if seen_items != set(expected_items):
        problems.append("Item coverage incomplete or contains unexpected IDs")
    exits = [r for r in rows if r.get("phase") == "exit"]
    if not exits or exits[-1].get("exit_code") != 0:
        problems.append("No clean client exit recorded")
    return {"status": "INCOMPLETE" if problems else "RPC_COVERAGE_COMPLETE_UI_REVIEW_REQUIRED",
            "thread_id": thread, "observed_turns": len(seen_turns), "observed_items": len(seen_items),
            "problems": sorted(set(problems)), "native_certification": "NOT_ESTABLISHED_BY_THIS_ANALYZER"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze([json.loads(line) for line in args.log.read_text().splitlines()]), indent=2))


if __name__ == "__main__":
    main()
