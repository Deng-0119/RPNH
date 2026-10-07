"""Comparison-only KB metadata discovery and retrieved-policy write prerequisite.

Reads only the declared KB metadata relations in the pinned backend. No task,
scorer, private policy allowlist, state checkpoint or other table is consulted.
"""
from __future__ import annotations

import json
import re

from .comparison_condition import DISCOVERY_TOOL, POLICY_ARGUMENT
from .constants import OFFICE_EFFECTS
from .jsonio import dumps


def discover_knowledge_queries(bank, *, topic: str, audience: str = "",
                               offset: int = 0, limit: int = 10) -> str:
    if not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 200:
        raise ValueError("topic must be a nonempty string of at most 200 characters")
    words = sorted(set(re.findall(r"\w+", topic.casefold())))
    if not words or len(words) > 12:
        raise ValueError("topic needs one to twelve literal words")
    if not isinstance(audience, str) or len(audience) > 100:
        raise ValueError("audience must be a metadata string of at most 100 characters")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("offset must be nonnegative and limit must be between 1 and 20")
    # The original search returns only the first article ordered by article_id.
    # Select that same reachable article BEFORE filtering titles/audiences, so a
    # discovery result never promises an article the original tool cannot return.
    rows = bank.conn.execute(
        "SELECT q.query_key, a.article_id, a.title, a.audience "
        "FROM (SELECT kq.query_key, MIN(ka.article_id) AS article_id "
        "FROM knowledge_queries kq JOIN knowledge_articles ka "
        "ON ka.article_id = kq.article_id GROUP BY kq.query_key) q "
        "JOIN knowledge_articles a ON a.article_id = q.article_id "
        "ORDER BY a.article_id, q.query_key").fetchall()
    matches = []
    for raw in rows:
        query_key, article_id, title, row_audience = raw
        title_words = set(re.findall(r"\w+", str(title).casefold()))
        if (query_key and set(words) <= title_words
                and (not audience or row_audience == audience)):
            matches.append({"query_key": query_key, "article_id": article_id,
                            "title": title, "audience": row_audience})
    page = matches[offset:offset + limit]
    return dumps({"schema_version": "rpnh-ha/kb-discovery/v1",
                  "status": "matched" if page else "no_match",
                  "matches": page,
                  "next_offset": offset + limit if offset + limit < len(matches) else None,
                  "audience_semantics": "metadata filter only; no authorization claim",
                  "search_semantics": "all literal topic words in title; original exact-key lookup remains unchanged"})


def validate_policy_evidence(value, retrieved: set[tuple[str, str]]) -> dict:
    """Fail closed on unavailable/malformed declarations and absent real returns."""
    if not isinstance(value, str):
        raise ValueError("policy_evidence is required as a JSON string")
    try:
        document = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("policy_evidence must be valid JSON") from exc
    if not isinstance(document, dict) or set(document) != {"status", "reason", "references"}:
        raise ValueError("policy_evidence requires status, reason and references")
    if document["status"] != "ready":
        raise ValueError("policy evidence is not ready")
    if not isinstance(document["reason"], str) or not document["reason"].strip():
        raise ValueError("a public policy basis is required")
    references = document["references"]
    if not isinstance(references, list) or not 1 <= len(references) <= 20:
        raise ValueError("nonempty retrieved policy references are required")
    for ref in references:
        if (not isinstance(ref, dict) or set(ref) != {"query_key", "article_id"}
                or any(not isinstance(v, str) or not v for v in ref.values())
                or (ref["query_key"], ref["article_id"]) not in retrieved):
            raise ValueError("policy reference has no earlier successful KB return in this run")
    return document


class ComparisonDispatch:
    """One instance per isolated backend run; original writes keep their semantics.

    Evidence is actual backend return availability, NOT registered model input.
    A cited unrelated policy can pass this structural check; the public workflow
    remains responsible for judging relevance and faithfully reporting limits.
    """
    def __init__(self, original_dispatch):
        self.original_dispatch = original_dispatch
        self.retrieved: set[tuple[str, str]] = set()

    def __call__(self, bank, tool: str, arguments: dict) -> str:
        args = dict(arguments)
        if tool == DISCOVERY_TOOL:
            if set(args) - {"topic", "audience", "offset", "limit"}:
                raise ValueError("unknown discovery argument")
            return discover_knowledge_queries(bank, **args)
        if tool not in OFFICE_EFFECTS:
            raise ValueError("unknown Office comparison operation")
        if OFFICE_EFFECTS[tool] == "external_write":
            try:
                validate_policy_evidence(args.pop(POLICY_ARGUMENT, None), self.retrieved)
            except ValueError as exc:
                return dumps({"status": "blocked_policy", "write_dispatched": False,
                              "reason": str(exc)})
        result = self.original_dispatch(bank, tool, args)
        if tool == "search_knowledge_base" and isinstance(result, str):
            match = re.match(r"\AKnowledge Search Result\nArticle: ([^\n]+)\nPolicy:\n- \S", result)
            query = args.get("query")
            if match and isinstance(query, str) and query:
                self.retrieved.add((query, match.group(1)))
        return result
