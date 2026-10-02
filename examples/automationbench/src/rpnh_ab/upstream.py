"""Only the installed upstream owns worlds, tool semantics, and scoring."""
from __future__ import annotations
import asyncio
import copy
import importlib.metadata
import inspect
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit
from .constants import PUBLIC_DOMAINS, TOOLS, UPSTREAM_COMMIT
from .io import append, now, sha


def normalize_api_fetch_arguments(arguments: dict) -> tuple[dict, tuple[str, ...], tuple[str, ...]]:
    """Apply the two compatibility mappings observed in the local pilot.

    The caller retains the model's original arguments in tool_events.jsonl. This
    function only adapts the copy sent to the pinned upstream implementation.
    """
    normalized = copy.deepcopy(arguments)
    changed: list[str] = []
    rules: list[str] = []
    for field in ("params", "body"):
        value = normalized.get(field)
        if isinstance(value, str) and value.strip() == "null":
            normalized[field] = None
            changed.append(field)
    if changed:
        rules.append("json-string-null-to-native-no-value")

    method = normalized.get("method")
    url = normalized.get("url")
    body = normalized.get("body")
    parsed = urlsplit(url) if isinstance(url, str) else None
    parts = parsed.path.split("/") if parsed is not None else []
    trello_add_label = (
        parsed is not None
        and parsed.hostname == "api.trello.com"
        and len(parts) == 5
        and parts[:3] == ["", "1", "cards"]
        and bool(parts[3])
        and parts[4] == "idLabels"
    )
    if (
        isinstance(method, str)
        and method.upper() == "POST"
        and trello_add_label
        and isinstance(body, str)
    ):
        try:
            parsed_body = json.loads(body)
        except json.JSONDecodeError:
            parsed_body = None
        if isinstance(parsed_body, str):
            normalized["body"] = json.dumps({"value": parsed_body})
            if "body" not in changed:
                changed.append("body")
            rules.append("trello-add-label-json-scalar-to-value-object")
    return normalized, tuple(changed), tuple(rules)


def information(row: dict) -> dict:
    info = row.get("info")
    if isinstance(info, str):
        info = json.loads(info)
    if not isinstance(info, dict):
        raise TypeError("upstream task info must be a JSON object")
    return copy.deepcopy(info)


def public_messages(row: dict) -> list[dict]:
    """Only row.prompt crosses the actor boundary. Never copy row.info."""
    messages = row.get("prompt")
    if not isinstance(messages, list) or not messages:
        raise ValueError("expected the upstream public prompt message list")
    result = []
    user_seen = False
    for message in messages:
        if hasattr(message, "model_dump"):
            message = message.model_dump()
        if not isinstance(message, dict):
            raise TypeError("prompt message is not an object")
        role, content = message.get("role"), message.get("content")
        if role not in {"system", "developer", "user"} or not isinstance(content, str):
            raise ValueError("unsupported upstream prompt role/content; do not silently flatten")
        if user_seen and role in {"system", "developer"}:
            raise ValueError("interleaved system/user prompt cannot be preserved by a text-stage mapping")
        user_seen = user_seen or role == "user"
        result.append({"role": role, "content": content})
    if not any(m["role"] == "user" and m["content"].strip() for m in result):
        raise ValueError("task has no public user message")
    return result


def split_prompt(messages: list[dict]) -> tuple[str, str]:
    systems = [m["content"] for m in messages if m["role"] in {"system", "developer"}]
    users = [m["content"] for m in messages if m["role"] == "user"]
    # Text inside every source message is preserved. Exact roles/order are also
    # saved in public_task.json; the native node API is a text-stage interface.
    return "\n\n".join(systems), "\n\n".join(users)


def git_identity(root: Path) -> dict:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    return {"commit": git("rev-parse", "HEAD"),
            "tracked_changes": git("status", "--porcelain", "--untracked-files=no")}


class Upstream:
    def __init__(self, root: Path):
        root = root.resolve()
        actual = git_identity(root)
        if actual["commit"] != UPSTREAM_COMMIT or actual["tracked_changes"]:
            raise ValueError("upstream must be the unmodified pinned commit in its own checkout")
        import automationbench
        imported_root = Path(automationbench.__file__).resolve().parent.parent
        if imported_root != root:
            raise ValueError("automationbench imports a different installation from --upstream")
        from automationbench.domains import get_domain_dataset
        from automationbench.runner import AutomationBenchEnv
        from automationbench.rubric import create_rubric
        from automationbench.tools.api import API_TOOLS
        from automationbench.task_contract import task_contract_sha256
        self.root, self.identity = root, actual
        self.get_dataset = get_domain_dataset
        self.fingerprint = task_contract_sha256
        self.functions = {tool.__name__: tool for tool in API_TOOLS}
        if set(self.functions) != set(TOOLS):
            raise ValueError("native API tool inventory differs from reviewed three-tool interface")
        # No rollout/evaluate/generate call is made on this environment.
        # max_turns remains an unused upstream constructor value, NOT an RPNH cap.
        self.env = AutomationBenchEnv(dataset=get_domain_dataset("simple"),
                                      rubric=create_rubric(), toolset="api")
        self.schemas = copy.deepcopy(self.env._all_oai_tools)
        if {t["function"]["name"] for t in self.schemas} != set(TOOLS):
            raise ValueError("upstream API schemas are incomplete")
        self.package_version = importlib.metadata.version("automation-bench")
        self.normalization_log: Path | None = None

    def cases(self, split: str = "public") -> list[dict]:
        if split not in {"public", "simple"}:
            raise ValueError("only public or separate simple acceptance split is supported")
        domains = PUBLIC_DOMAINS if split == "public" else ("simple",)
        cases, global_index = [], 0
        for domain in domains:
            dataset = self.get_dataset(domain)
            expected = 100 if split == "public" else 200
            if len(dataset) != expected:
                raise ValueError(f"{domain}: expected {expected} tasks, got {len(dataset)}")
            for index, source in enumerate(dataset):
                row = copy.deepcopy(dict(source))
                row["info"] = information(row)
                public = public_messages(row)
                row["prompt"] = public
                # Match the selected combined dataset's stable global order.
                # Preserve a real upstream example_id; otherwise explicitly assign.
                assigned = row.get("example_id") is None
                if assigned:
                    row["example_id"] = global_index
                name = row["info"].get("task_name")
                if not isinstance(name, str) or not name:
                    raise ValueError(f"{domain}[{index}] has no upstream task_name")
                digest = self.fingerprint(example_id=row["example_id"],
                                          prompt=public, info=row["info"])
                cases.append({"id": f"{domain}-{index+1:04d}", "domain": domain,
                              "domain_index": index, "task_name": name,
                              "example_id": row["example_id"],
                              "example_id_assigned_by_adapter": assigned,
                              "task_contract_sha256": digest, "row": row})
                global_index += 1
        if len({c["id"] for c in cases}) != len(cases):
            raise ValueError("duplicate task identity")
        return cases

    def start(self, row: dict) -> dict:
        state = asyncio.run(self.env.setup_state(copy.deepcopy(row)))
        # Ensure schema/tool availability did not collapse after per-task setup.
        names = {t.name for t in state["tool_defs"]}
        if names != set(TOOLS):
            raise ValueError("per-task API tools are incomplete")
        return state

    def dispatch(self, state: dict, tool: str, arguments: dict) -> str:
        if tool not in self.functions:
            raise ValueError("unknown upstream tool")
        if "world" in arguments:
            raise ValueError("world is host-owned, never a model-supplied argument")
        normalized = copy.deepcopy(arguments)
        changed: tuple[str, ...] = ()
        rules: tuple[str, ...] = ()
        if tool == "api_fetch":
            normalized, changed, rules = normalize_api_fetch_arguments(arguments)
        if changed and self.normalization_log is not None:
            append(self.normalization_log, {
                "schema": "rpnh-ab/normalization-event/v1",
                "at": now(),
                "tool": tool,
                "fields": list(changed),
                "rules": list(rules),
                "original_arguments_preserved_in_tool_events": True,
            })
        # Preserve empty-dict optional sentinel behavior and host-world injection.
        updated = self.env.update_tool_args(tool, normalized, [], state)
        value = self.functions[tool](**updated)
        if inspect.isawaitable(value):
            value = asyncio.run(value)
        if not isinstance(value, str):
            raise TypeError("upstream native tool returned a non-string")
        return value

    @staticmethod
    def dump_world(state: dict) -> dict:
        return state["world"].model_dump(mode="json")

    @staticmethod
    def score(final_world: dict, initial_state: dict, info: dict) -> dict:
        from automationbench.schema.world import WorldState
        from automationbench.rubric import partial_credit, task_completed_correctly
        from automationbench.rubric.registry import AssertionRegistry, STRICT_MODE
        if not STRICT_MODE:
            raise ValueError("strict upstream assertion mode must remain enabled")
        AssertionRegistry.reset_error_counts()
        state = {"world": WorldState(**copy.deepcopy(final_world)),
                 "initial_state": copy.deepcopy(initial_state), "info": copy.deepcopy(info)}
        partial = partial_credit(state)
        completed = task_completed_correctly(state)
        return {"partial_credit": partial, "task_completed_correctly": completed,
                "assertion_results": state.get("_assertion_results", []),
                "assertion_error_counts": AssertionRegistry.get_error_summary(),
                "scoring_semantics": "upstream-unmodified-strict"}
