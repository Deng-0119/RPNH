"""RPNH main-session Designer and typed child-task decisions."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from cpn.llm_adapters import load_llm_execution_selection

from .agent_tasks import (
    AgentStage,
    AgentTaskSpec,
    resume_agent_task,
    run_agent_task,
)
from .agent_workflows import AgentWorkflowGraph
from .registry._registry import _RegistryCore
from .registry.identities import TypedId
from .registry.main_thread import MainThreadRegistry
from .registry.main_thread_history import MainThreadReadCut
from .registry.models import VersionRef
from .task_control import TaskControl, TaskHandle, owner_socket_path
from .user_config import profile_for_path


@dataclass(frozen=True, slots=True)
class MainTaskDecision:
    kind: str
    prompt: str
    stages: tuple[AgentStage, ...] = ()
    workflow_graph: AgentWorkflowGraph | None = None


@dataclass(frozen=True, slots=True)
class MainDecision:
    reply: str
    task: MainTaskDecision | None
    protocol_valid: bool = True


@dataclass(frozen=True, slots=True)
class MainTurnSnapshot:
    """Registry-derived state for the one active interactive turn."""

    ordinal: int
    user_text: str
    required_task_kind: str | None
    state: str
    attempt_path: Path | None
    registered_output: object | None
    registry_observation: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class MainTurnReconciliation:
    """Result of reconciling a main turn against its child Registry."""

    state: str
    snapshot: MainTurnSnapshot | None
    decision: MainDecision | None = None
    child: TaskHandle | None = None


class MainSessionInterrupted(RuntimeError):
    """Base error for a main turn stopped before conversation commit."""


class MainSessionPaused(MainSessionInterrupted):
    """The current main turn is checkpointed and awaits a user decision."""


class MainSessionExecutionFailed(MainSessionInterrupted):
    """The child returned without terminal Registry result authority."""


def render_main_decision(
        decision: MainDecision, child: TaskHandle | None,
) -> str:
    """Render the durable decision and launch result for user-facing clients."""
    output = decision.reply
    if not decision.protocol_valid:
        output += (
            "\n\n[RPNH main agent returned prose; no child task was launched.]")
    if child is not None:
        output += f"\n\n[launched {child.task_id}: {child.kind}]"
    return output


@dataclass(frozen=True, slots=True)
class MainDisplayTurn:
    """Safe owner-facing text from one committed turn at a native cut."""

    turn_ref: VersionRef
    ordinal: int
    user_text: str
    assistant_text: str


def parse_main_decision(
        value: object, *, required_task_kind: str | None = None,
) -> MainDecision:
    """Validate only the declared decision schema, never exact agent prose."""
    if isinstance(value, str):
        value = json.loads(value)
    if (not isinstance(value, Mapping)
            or set(value) != {"reply", "task"}
            or not isinstance(value["reply"], str)):
        raise ValueError("main agent decision requires reply and task")
    raw = value["task"]
    if raw is None:
        if required_task_kind is not None:
            raise ValueError(
                f"main agent decision requires {required_task_kind} task")
        return MainDecision(value["reply"], None)
    if not isinstance(raw, Mapping) or raw.get("kind") not in {
            "single_agent", "workflow"}:
        raise ValueError("main agent task kind is invalid")
    if not isinstance(raw.get("prompt"), str) or not raw["prompt"].strip():
        raise ValueError("main agent task prompt must be nonempty")
    if required_task_kind is not None and raw["kind"] != required_task_kind:
        raise ValueError(
            f"main agent decision requires {required_task_kind} task")
    if raw["kind"] == "single_agent":
        if set(raw) != {"kind", "prompt", "instruction"}:
            raise ValueError("single-agent decision fields are invalid")
        stages = (AgentStage("worker", raw["instruction"]),)
        graph = None
    else:
        if set(raw) != {"kind", "prompt", "graph"}:
            raise ValueError("workflow decision fields are invalid")
        stages = ()
        graph = AgentWorkflowGraph.from_mapping(raw["graph"])
    return MainDecision(
        value["reply"], MainTaskDecision(
            raw["kind"], raw["prompt"], stages, graph))


def _main_prompt(
        history: tuple[tuple[str, str], ...], user_text: str, *,
        required_task_kind: str | None = None, native_operations=(),
        history_message_limit: int = 20,
) -> str:
    if (isinstance(history_message_limit, bool)
            or not isinstance(history_message_limit, int)
            or history_message_limit < 1):
        raise ValueError("main history message limit must be positive")
    transcript = "\n".join(
        f"{role}: {body}"
        for role, body in history[-history_message_limit:])
    requirement = (
        "This request explicitly requires a workflow task; task must not be null "
        "or single_agent."
        if required_task_kind == "workflow" else
        "Choose whether an independent execution object is needed."
    )
    native_guidance = ""
    if native_operations:
        native_guidance = (
            "\nOwner-selected native plugin operations are available as workflow nodes, not direct LLM tools. "
            "Select a listed selector with node execution={role:actor,tools:null,profile_id:null,plugin:selector}. "
            "Each native node has one input and one output. Both carry serialized JSON TEXT; the input JSON "
            "must satisfy that operation's input_schema. Use an LLM node to prepare arguments when needed, "
            "and another LLM node to interpret the result. Native nodes execute no model. Graphs containing "
            "native nodes cannot contain feedback in this API version. Do not invent installed operations.\n"
            + json.dumps(native_operations, ensure_ascii=False, sort_keys=True))
    return f"""You are the one and only agent in the RPNH main session and the
Designer for any workflow you create.
Answer the user directly when no independent execution object is needed.
When actual work should run independently, request exactly one child object:
either a single_agent or a graph-shaped workflow. The child will receive its
own Registry, PetriNet and owner channel. {requirement}
Do not claim that a child has run; only request its launch.
RPNH will deterministically give the child the exact current user message as
its authoritative task input, followed by task.prompt as a supplemental
Designer execution brief. Do not copy, summarize, or replace the current user
message inside task.prompt, including any embedded dossier. Put detailed role
responsibilities in the single-agent instruction or workflow node instructions.

A workflow is a finite agent graph, not an ordered stage list. Declare
nodes with complete responsibilities and typed symbolic text-product ports,
arcs with exact source/target ports, and singular ingress/egress bindings.
Arcs are the sole topology authority; array order is inert. Fan-out, fan-in and
joins are supported. Every non-ingress input has exactly one arc producer,
every non-egress output has at least one consumer, every node lies on an
ingress-to-egress dependency path, and source/target artifact_id values match.
Each arc kind is dependency or feedback. Dependency arcs must form a DAG.
A feedback arc may close a dependency path toward an earlier node; it activates
that node as an alternative rework generation rather than becoming a first-run
join input. If feedback arcs exist, set max_rework_cycles to a positive integer
below 12; otherwise set it to 0. A node that can request feedback must have
separate dependency outputs for complete and feedback outputs for rework.

Return exactly one JSON object, without Markdown, using one of these shapes:
{{"reply":"text","task":null}}
{{"reply":"text","task":{{"kind":"single_agent","prompt":"task",\
"instruction":"agent responsibility"}}}}
{{"reply":"text","task":{{"kind":"workflow","prompt":"task",\
"graph":{{"nodes":[{{"node_id":"design","instruction":"...",\
"input_ports":[{{"port_id":"request","artifact_id":"task"}}],\
"output_ports":[{{"port_id":"plan","artifact_id":"plan"}}]}},\
{{"node_id":"execute","instruction":"...",\
"input_ports":[{{"port_id":"plan","artifact_id":"plan"}}],\
"output_ports":[{{"port_id":"result","artifact_id":"result"}}]}}],\
"arcs":[{{"arc_id":"design_to_execute",\
"source":{{"node_id":"design","port_id":"plan"}},\
"target":{{"node_id":"execute","port_id":"plan"}},"kind":"dependency"}}],\
"ingress":{{"node_id":"design","port_id":"request"}},\
"egress":{{"node_id":"execute","port_id":"result"}},\
"max_rework_cycles":0}}}}}}

{native_guidance}

Conversation so far:
{transcript or '(new session)'}

user: {user_text}
"""


class MainSession:
    """Interactive controller borrowing its caller's original owner lease.

    The caller reserves/acquires ``MainSessionOwnerLease`` and keeps it open
    across all writing actions. This API never reacquires or releases that
    lease. Pure history reads remain available after the caller releases it.
    """

    STATE_FILE = "session_state.json"
    PROFILE_FILE = "execution_profile.json"
    REGISTRY_DIR = "main"
    OWNER_LOCK_FILE = ".main-session-owner.lock"

    def _assert_owner_lease(self) -> None:
        from .session_access import MainSessionOwnerLease

        lease = self._owner_lease
        if not isinstance(lease, MainSessionOwnerLease):
            raise ValueError("MainSession requires its caller's live owner_lease")
        if lease.root != self.root:
            raise ValueError("MainSession owner_lease belongs to a different root")
        lease.assert_held()

    @property
    def child_path_root(self) -> Path:
        """Filesystem root for direct child references of the main Registry."""
        return self._main_thread.child_path_root

    def resolve_child_path(self, relative_path: str) -> Path:
        """Resolve one direct child link from the main Registry."""
        return self._main_thread.resolve_child_path(relative_path)

    def relative_child_path(self, child_path: Path) -> str:
        """Return one direct child link from the main Registry."""
        return self._main_thread.relative_child_path(child_path)

    def __init__(
            self, root: Path, execution_config_path: Path, *,
            task_control: TaskControl | None = None,
            resume: bool = False,
            owner_root_reserved: bool = False,
            owner_lease=None,
            managed_account_resolver_factory=None,
    ) -> None:
        from cpn.update_context import reject_candidate_runtime
        reject_candidate_runtime()
        if (managed_account_resolver_factory is not None
                and not callable(managed_account_resolver_factory)):
            raise TypeError("managed account resolver factory must be callable")
        self._managed_account_resolver_factory = managed_account_resolver_factory
        if not isinstance(root, Path) or not isinstance(execution_config_path, Path):
            raise TypeError("MainSession paths require pathlib.Path")
        if not isinstance(owner_root_reserved, bool):
            raise TypeError("owner_root_reserved requires bool")
        if resume and owner_root_reserved:
            raise ValueError(
                "resume cannot initialize a reserved main-session root")
        self.root = root.resolve()
        self.execution_config_path = execution_config_path.resolve()
        self._owner_lease = owner_lease
        self._assert_owner_lease()
        persisted_profile = (
            self._persisted_execution_profile(self.root)
            if resume else None)
        if resume:
            if not self.root.is_dir():
                raise ValueError(
                    "RPNH resume requires an existing session directory")
            # Reject an unreadable or changed profile before obtaining a
            # Registry writer or letting TaskControl recover pending launches.
            # Keep the existing post-open check as well: local configuration
            # may change between these reads. This is the profile boundary,
            # not a grant of cross-release Registry write compatibility.
            if persisted_profile is None:
                raise ValueError(
                    "main-session resume has no persisted execution "
                    "profile authority")
            if self.execution_config_path != persisted_profile[
                    "execution_config_path"]:
                raise ValueError(
                    "cannot change the execution profile when resuming a "
                    "main session")
            if persisted_profile["schema_version"] in {
                    "rpnh/main_session_profile/v2",
                    "rpnh/main_session_profile/v3"}:
                self._assert_execution_profile_identity(persisted_profile)
            from .business_admission import inspect_main_session_write_admission
            inspect_main_session_write_admission(self.root)
            try:
                self._registry_core = _RegistryCore(
                    self.root / self.REGISTRY_DIR, create=False)
            except Exception as exc:
                raise ValueError(
                    "RPNH session has no valid main-thread Registry") from exc
        else:
            if not owner_root_reserved:
                raise ValueError(
                    "fresh MainSession requires reserve_for_creation and "
                    "owner_root_reserved=True")
            lock_path = self.root / self.OWNER_LOCK_FILE
            if (self.root.is_symlink() or not self.root.is_dir()
                    or lock_path.is_symlink() or not lock_path.is_file()
                    or {path.name for path in self.root.iterdir()}
                    != {self.OWNER_LOCK_FILE}):
                raise ValueError(
                    "reserved main-session root must contain only its "
                    "owner lock")
            self._registry_core = _RegistryCore(
                self.root / self.REGISTRY_DIR, create=True)
        self._main_thread = MainThreadRegistry(
            self._registry_core, session_root=self.root,
            initialize_path_base=(
                MainThreadRegistry.REGISTRY_ROOT_PATH_BASE
                if not resume else None),
        )
        self._history_reader: MainThreadRegistry | None = None
        if not resume:
            self._main_thread.create_thread(
                idempotency_key="main-session-thread")
        self._launched_children: dict[int, TaskHandle | None] = {}
        self.task_control = task_control or TaskControl(
            self._main_thread.child_path_root / "tasks")
        self._refresh_from_authority()
        if resume:
            if persisted_profile is None:
                raise ValueError(
                    "main-session resume has no persisted execution "
                    "profile authority")
            if self.execution_config_path != persisted_profile[
                    "execution_config_path"]:
                raise ValueError(
                    "cannot change the execution profile when resuming a "
                    "main session")
            if persisted_profile["schema_version"] in {
                    "rpnh/main_session_profile/v2",
                    "rpnh/main_session_profile/v3"}:
                self._assert_execution_profile_identity(persisted_profile)
        self._persist_profile()
        self._persist_state()

    @classmethod
    def _persisted_execution_profile(
            cls, root: Path,
    ) -> dict[str, Any] | None:
        root = root.resolve()
        profile_path = root / cls.PROFILE_FILE
        try:
            document = json.loads(
                profile_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            document = None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "invalid execution profile projection") from exc
        if document is not None:
            try:
                if not isinstance(document, Mapping):
                    raise ValueError("invalid execution profile projection")
                schema_version = document.get("schema_version")
                if schema_version == "rpnh/main_session_profile/v1":
                    if set(document) != {
                            "schema_version", "execution_config_path"}:
                        raise ValueError("invalid execution profile projection")
                elif schema_version == "rpnh/main_session_profile/v2":
                    if set(document) != {
                            "schema_version", "execution_config_path",
                            "adapter_config_path", "selection_id", "provider",
                            "model_condition", "adapter_kind",
                            "registry_policy"}:
                        raise ValueError("invalid execution profile projection")
                    if (not isinstance(document["registry_policy"], Mapping)
                            or any(not isinstance(document[key], str)
                                   or not document[key]
                                   for key in (
                                       "adapter_config_path", "selection_id",
                                       "provider", "model_condition",
                                       "adapter_kind"))):
                        raise ValueError("invalid execution profile identity")
                elif schema_version == "rpnh/main_session_profile/v3":
                    if set(document) != {
                            "schema_version", "execution_config_path",
                            "adapter_config_path", "selection_id", "provider",
                            "model_condition", "reasoning_effort",
                            "adapter_kind", "registry_policy"}:
                        raise ValueError("invalid execution profile projection")
                    if (not isinstance(document["registry_policy"], Mapping)
                            or (document["reasoning_effort"] is not None
                                and (not isinstance(
                                    document["reasoning_effort"], str)
                                     or not document["reasoning_effort"]))
                            or any(not isinstance(document[key], str)
                                   or not document[key]
                                   for key in (
                                       "adapter_config_path", "selection_id",
                                       "provider", "model_condition",
                                       "adapter_kind"))):
                        raise ValueError("invalid execution profile identity")
                else:
                    raise ValueError("invalid execution profile projection")
                raw_path = document["execution_config_path"]
                if not isinstance(raw_path, str) or not raw_path:
                    raise ValueError("invalid execution profile path")
                return {
                    **document,
                    "execution_config_path": Path(raw_path).resolve(),
                }
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    "invalid execution profile projection") from exc
        try:
            document = json.loads(
                (root / cls.STATE_FILE).read_text(encoding="utf-8"))
            raw_path = document["execution_config_path"]
            if not isinstance(raw_path, str) or not raw_path:
                raise ValueError("invalid execution profile path")
            return {
                "schema_version": "rpnh/main_session_profile/v1",
                "execution_config_path": Path(raw_path).resolve(),
            }
        except (KeyError, OSError, TypeError, ValueError,
                json.JSONDecodeError):
            return None

    @classmethod
    def _persisted_execution_config_path(
            cls, root: Path,
    ) -> Path | None:
        profile = cls._persisted_execution_profile(root)
        return (None if profile is None else
                profile["execution_config_path"])

    def _execution_profile_document(self) -> dict[str, object]:
        """Capture the exact non-secret selection identity for this session."""

        selection = load_llm_execution_selection(self.execution_config_path)
        profile = profile_for_path(self.execution_config_path)
        return {
            "schema_version": "rpnh/main_session_profile/v3",
            "execution_config_path": str(self.execution_config_path),
            "adapter_config_path": str(selection.adapter_config_path),
            "selection_id": profile.selection_id,
            "provider": profile.provider,
            "model_condition": selection.input_target.model_condition,
            "reasoning_effort": selection.reasoning_effort,
            "adapter_kind": selection.adapter_kind,
            "registry_policy": selection.as_registry_policy(),
        }

    def _assert_execution_profile_identity(
            self, persisted: Mapping[str, object],
    ) -> None:
        expected = dict(persisted)
        expected["execution_config_path"] = str(
            expected["execution_config_path"])
        current = self._execution_profile_document()
        if expected.get("schema_version") == "rpnh/main_session_profile/v2":
            current = dict(current)
            current["schema_version"] = "rpnh/main_session_profile/v2"
            current.pop("reasoning_effort")
        if current != expected:
            raise ValueError(
                "execution profile identity changed for the main session")

    @classmethod
    def resume(
            cls, root: Path, execution_config_path: Path | None = None, *,
            task_control: TaskControl | None = None,
            owner_lease=None,
            managed_account_resolver_factory=None,
    ) -> "MainSession":
        root = root.resolve()
        selected = execution_config_path
        persisted = cls._persisted_execution_config_path(root)
        if persisted is None:
            raise ValueError(
                "RPNH resume requires a persisted execution profile")
        if selected is None:
            selected = persisted
        return cls(
            root, selected, task_control=task_control, resume=True,
            owner_lease=owner_lease,
            managed_account_resolver_factory=managed_account_resolver_factory)

    @property
    def state_path(self) -> Path:
        return self.root / self.STATE_FILE

    @staticmethod
    def _version_ref(value: Mapping[str, Any]) -> VersionRef:
        try:
            if set(value) != {"entity_type", "logical_id", "version_id"}:
                raise ValueError("invalid exact ref fields")
            return VersionRef(
                str(value["entity_type"]),
                TypedId.parse(str(value["logical_id"])),
                TypedId.parse(str(value["version_id"])),
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "main-thread Registry projection contains a malformed ref") from exc

    @staticmethod
    def _turn_input(
            value: object,
    ) -> tuple[str, str | None]:
        if (not isinstance(value, Mapping)
                or not {"text", "required_task_kind"}.issubset(value)
                or set(value) - {"text", "required_task_kind", "native_plugins",
                                 "agent_task_settings"}
                or not isinstance(value["text"], str)
                or value["required_task_kind"] not in {None, "workflow"}):
            raise RuntimeError(
                "main-thread Registry contains an invalid user input")
        return value["text"], value["required_task_kind"]

    @staticmethod
    def _selected_plugins():
        """Capture owner-selected code/config once, before accepting a fresh turn."""
        from cpn.plugins.catalog import read_config, load_catalog
        document = read_config()
        if document is None:
            return None
        catalog = load_catalog(document)
        return {"configuration": document, **(
            {"catalog_snapshot": catalog.versioned_snapshot()} if catalog.versioned else
            {"catalog_digest": catalog.digest})}

    def _selected_task_capabilities(self, selection=None):
        """Capture a fresh HOST selection in the existing turn input snapshot."""
        from cpn.plugins.api import json_copy
        if selection is None:
            selection = load_llm_execution_selection(self.execution_config_path)
        self._validate_account_task_settings(selection.agent_task_settings)
        native = self._selected_plugins()
        result = {"native_plugins": native} if native is not None else {}
        if selection.agent_task_settings:
            result["agent_task_settings"] = json_copy(selection.agent_task_settings)
        return result

    def _validate_account_task_settings(self, settings):
        # The installed resolver remains in this HOST process. Worker task
        # documents carry only the original non-secret selection plan.
        child = settings.get("child_task", {})
        main = settings.get("main_turn", {})
        if (child.get("managed_accounts") is not None
                and child.get("managed_account_provider") is None):
            raise ValueError(
                "child account tasks require an installed worker account provider")
        if (main.get("managed_accounts") is not None
                and main.get("managed_account_provider") is None
                and self._managed_account_resolver_factory is None):
            raise ValueError(
                "main-turn account tools require an installed account resolver factory")
        if (main.get("managed_account_provider") is not None
                and self._managed_account_resolver_factory is not None):
            raise ValueError("account provider selection and in-process factory are mutually exclusive")

    @staticmethod
    def _plugin_settings(turn, *, scope="main_turn"):
        """Project exact task options from the registered input, never live files."""
        from cpn.plugins.catalog import load_catalog
        if scope not in {"main_turn", "child_task"}:
            raise ValueError("unknown agent task settings scope")
        source = turn.get("user_input", {})
        native = source.get("native_plugins")
        settings, operations = {}, ()
        if native is not None:
            if (not isinstance(native, Mapping) or set(native) not in (
                    {"configuration", "catalog_digest"}, {"configuration", "catalog_snapshot"})):
                raise ValueError("invalid registered native plugin selection")
            catalog = load_catalog(native["configuration"])
            if catalog.versioned:
                from cpn.plugins.api import canonical
                if ("catalog_snapshot" not in native
                        or canonical(catalog.versioned_snapshot()) != canonical(native["catalog_snapshot"])):
                    raise ValueError("installed plugins differ from the registered main-turn snapshot")
                settings.update({"plugin_configuration": native["configuration"],
                                 "plugin_catalog_snapshot": native["catalog_snapshot"]})
            else:
                if "catalog_digest" not in native or catalog.digest != native["catalog_digest"]:
                    raise ValueError("installed plugins differ from the registered main-turn catalog")
                settings.update({"plugin_configuration": native["configuration"],
                                 "plugin_catalog_digest": native["catalog_digest"]})
            operations = catalog.describe()
        settings.update(source.get("agent_task_settings", {}).get(scope, {}))
        return settings, operations

    @staticmethod
    def _decision_document(decision: MainDecision) -> dict[str, Any]:
        task = decision.task
        return {
            "reply": decision.reply,
            "protocol_valid": decision.protocol_valid,
            "task": (
                None if task is None else {
                    "kind": task.kind,
                    "prompt": task.prompt,
                    "stages": [
                        {"stage_id": stage.stage_id,
                         "instruction": stage.instruction}
                        for stage in task.stages
                    ],
                    "workflow_graph": (
                        None if task.workflow_graph is None
                        else task.workflow_graph.to_dict()),
                }
            ),
        }

    @staticmethod
    def _decision_reply(value: object) -> str:
        if (not isinstance(value, Mapping)
                or set(value) != {"reply", "protocol_valid", "task"}
                or not isinstance(value["reply"], str)
                or not isinstance(value["protocol_valid"], bool)):
            raise RuntimeError(
                "main-thread Registry contains an invalid committed answer")
        return value["reply"]

    @staticmethod
    def _decision_from_document(value: object) -> MainDecision:
        if (not isinstance(value, Mapping)
                or set(value) != {"reply", "protocol_valid", "task"}
                or not isinstance(value["reply"], str)
                or not isinstance(value["protocol_valid"], bool)):
            raise RuntimeError(
                "main-thread Registry contains an invalid committed answer")
        raw_task = value["task"]
        if raw_task is None:
            return MainDecision(
                value["reply"], None, value["protocol_valid"])
        if (not isinstance(raw_task, Mapping)
                or set(raw_task) != {
                    "kind", "prompt", "stages", "workflow_graph"}
                or raw_task["kind"] not in {"single_agent", "workflow"}
                or not isinstance(raw_task["prompt"], str)
                or not raw_task["prompt"].strip()
                or not isinstance(raw_task["stages"], list)):
            raise RuntimeError(
                "main-thread Registry contains an invalid committed task")
        try:
            stages = tuple(
                AgentStage(stage["stage_id"], stage["instruction"])
                for stage in raw_task["stages"]
                if isinstance(stage, Mapping)
                and set(stage) == {"stage_id", "instruction"}
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                "main-thread Registry contains invalid committed stages") from exc
        if len(stages) != len(raw_task["stages"]):
            raise RuntimeError(
                "main-thread Registry contains invalid committed stages")
        try:
            graph = (
                None if raw_task["workflow_graph"] is None else
                AgentWorkflowGraph.from_mapping(raw_task["workflow_graph"])
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "main-thread Registry contains an invalid committed graph") from exc
        if ((raw_task["kind"] == "single_agent"
             and (len(stages) != 1 or graph is not None))
                or (raw_task["kind"] == "workflow"
                    and (stages or graph is None))):
            raise RuntimeError(
                "main-thread Registry committed task kind is inconsistent")
        return MainDecision(
            value["reply"],
            MainTaskDecision(
                raw_task["kind"], raw_task["prompt"], stages, graph),
            value["protocol_valid"],
        )

    @staticmethod
    def _parse_output(
            output: object, *, required_task_kind: str | None,
    ) -> MainDecision:
        try:
            return parse_main_decision(
                output, required_task_kind=required_task_kind)
        except (TypeError, ValueError, json.JSONDecodeError):
            return MainDecision(
                "" if output is None else str(output), None,
                protocol_valid=False)

    def _refresh_from_authority(self) -> dict[str, Any]:
        projection = self._main_thread.recover_thread()
        history: list[tuple[str, str]] = []
        for item in projection["committed_history"]:
            user_text, _required = self._turn_input(item["user_input"])
            history.extend((
                ("user", user_text),
                ("assistant", self._decision_reply(item["answer"])),
            ))
        self.history = history
        self.turn_ordinal = int(projection["next_turn_ordinal"]) - 1
        self._authority_projection = projection
        self._thread_ref = self._version_ref(projection["thread_ref"])
        return projection

    def _turn_document(self, ref: VersionRef) -> dict[str, Any]:
        return self._main_thread._read_exact(  # noqa: SLF001
            self._registry_core, ref, expected_type="main_turn/v1")

    def _active_turn(
            self,
    ) -> tuple[VersionRef, dict[str, Any]] | None:
        active = self._authority_projection["active_turn_ref"]
        if active is None:
            return None
        ref = self._version_ref(active)
        return ref, self._turn_document(ref)

    def active_turn_snapshot(self) -> MainTurnSnapshot | None:
        """Return the active turn reconstructed from Registry authority only."""

        self._refresh_from_authority()
        active = self._active_turn()
        if active is None:
            return None
        turn_ref, turn = active
        observed = self._main_thread.observe_turn_execution(turn_ref=turn_ref)
        user_text, required_task_kind = self._turn_input(turn["user_input"])
        raw_path = observed.get("attempt_relative_path")
        attempt_path: Path | None = None
        if raw_path is not None:
            if not isinstance(raw_path, str) or not raw_path:
                raise RuntimeError(
                    "main-thread Registry contains an invalid attempt path")
            try:
                attempt_path = self._main_thread.resolve_child_path(raw_path)
            except ValueError as exc:
                raise RuntimeError(
                    "registered main-turn attempt escapes its parent Registry"
                ) from exc
        observation = observed.get("observation")
        if observation is not None and not isinstance(observation, Mapping):
            raise RuntimeError(
                "main-thread Registry contains an invalid child observation")
        return MainTurnSnapshot(
            ordinal=int(observed["ordinal"]),
            user_text=user_text,
            required_task_kind=required_task_kind,
            state=str(observed["state"]),
            attempt_path=attempt_path,
            registered_output=observed.get("output"),
            registry_observation=observation,
        )

    def reconcile_active_turn(self) -> MainTurnReconciliation:
        """Commit an observed child terminal state, or report it still active.

        Terminal Registry evidence always wins over process/UI projections.
        A stopped-by-owner child remains the paused active main turn until the
        user explicitly resumes it or rolls the main session back.  The child
        Registry remains independent in either case.
        """

        self._assert_owner_lease()
        snapshot = self.active_turn_snapshot()
        if snapshot is None:
            return MainTurnReconciliation("idle", None)
        if snapshot.state == "terminal":
            decision, child = self.complete_turn(
                snapshot.user_text,
                snapshot.registered_output,
                required_task_kind=snapshot.required_task_kind,
            )
            return MainTurnReconciliation(
                "committed", snapshot, decision, child)
        if snapshot.state == "stopped_by_owner":
            return MainTurnReconciliation("paused", snapshot)
        if snapshot.state not in {"accepted", "pending_start", "running"}:
            raise RuntimeError(
                f"unsupported main-turn Registry state: {snapshot.state}")
        return MainTurnReconciliation(snapshot.state, snapshot)

    @staticmethod
    def _assert_turn_input(
            turn: Mapping[str, Any], user_text: str,
            required_task_kind: str | None,
    ) -> None:
        if MainSession._turn_input(turn.get("user_input")) != (
                user_text, required_task_kind):
            raise ValueError(
                "request differs from the active main-session turn")

    def _task_spec(
            self, turn: Mapping[str, Any], *, user_text: str,
            required_task_kind: str | None,
    ) -> AgentTaskSpec:
        self._validate_account_task_settings(
            turn.get("user_input", {}).get("agent_task_settings", {}))
        relative_path = turn.get("attempt_relative_path")
        if not isinstance(relative_path, str) or not relative_path:
            raise RuntimeError("active main turn has no registered attempt")
        persisted = self._persisted_execution_profile(self.root)
        if persisted is None:
            raise ValueError(
                "active main-session turn has no persisted execution "
                "profile authority")
        if self.execution_config_path != persisted["execution_config_path"]:
            raise ValueError(
                "cannot change the execution profile while a main-session "
                "turn is active")
        if persisted["schema_version"] in {
                "rpnh/main_session_profile/v2",
                "rpnh/main_session_profile/v3"}:
            self._assert_execution_profile_identity(persisted)
        try:
            run_dir = self._main_thread.resolve_child_path(relative_path)
        except ValueError as exc:
            raise RuntimeError(
                "registered main-turn attempt escapes its parent Registry"
            ) from exc
        plugin_settings, operations = self._plugin_settings(turn)
        from cpn.llm_adapters import load_llm_execution_selection
        runtime = load_llm_execution_selection(
            self.execution_config_path).runtime_policy
        return AgentTaskSpec(
            **plugin_settings,
            run_dir=run_dir,
            prompt=_main_prompt(
                tuple(self.history), user_text,
                required_task_kind=required_task_kind,
                native_operations=operations,
                history_message_limit=runtime.main_history_message_limit),
            stages=(AgentStage(
                "main",
                "Act only as the RPNH main-session agent and emit the declared "
                "JSON decision."),),
            execution_config_path=self.execution_config_path,
            max_attempts_per_stage=runtime.max_turns_per_node,
            max_parallel_nodes=runtime.max_parallel_nodes,
            owner_statement="RPNH interactive main-session turn",
            owner_socket_path=owner_socket_path(
                self.root, f"main-turn:{relative_path}", run_dir),
        )

    @staticmethod
    def _release_empty_attempt_directory(run_dir: Path) -> None:
        """Release the empty attachment reservation before child initialization."""
        try:
            run_dir.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            # A nonempty attempt is already initializing or is a child Registry.
            # Never remove any child-owned contents.
            pass

    def _persist_state(self) -> None:
        document = {
            "schema_version": "rpnh/main_session_state/v1",
            "execution_config_path": str(self.execution_config_path),
            "turn_ordinal": self.turn_ordinal,
            "history": [
                {"role": role, "body": body}
                for role, body in self.history
            ],
        }
        temporary = self.root / (self.STATE_FILE + ".tmp-" + uuid4().hex)
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        os.replace(temporary, self.state_path)

    def _persist_profile(self) -> None:
        document = self._execution_profile_document()
        temporary = self.root / (self.PROFILE_FILE + ".tmp-" + uuid4().hex)
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        os.replace(temporary, self.root / self.PROFILE_FILE)

    def set_execution_config(self, path: Path) -> None:
        self._assert_owner_lease()
        if not isinstance(path, Path):
            raise TypeError("execution config path requires pathlib.Path")
        selected = path.resolve()
        self._refresh_from_authority()
        if self._active_turn() is not None:
            persisted = self._persisted_execution_profile(self.root)
            if persisted is None:
                raise ValueError(
                    "active main-session turn has no persisted execution "
                    "profile authority")
            if persisted["schema_version"] in {
                    "rpnh/main_session_profile/v2",
                    "rpnh/main_session_profile/v3"}:
                self._assert_execution_profile_identity(persisted)
            if selected != self.execution_config_path:
                raise ValueError(
                    "cannot change the execution profile while a main-session "
                    "turn is active")
        self.execution_config_path = selected
        self._persist_profile()
        self._persist_state()

    def launch(
            self, prompt: str, stages: tuple[AgentStage, ...] = (), *,
            workflow_graph: AgentWorkflowGraph | None = None,
    ) -> TaskHandle:
        self._assert_owner_lease()
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("independent task prompt must be nonempty text")
        self.activate_for_execution()
        run_dir = self._main_thread.child_path_root / "tasks" / "runs" / (
            "child-" + uuid4().hex[:12])
        from cpn.llm_adapters import load_llm_execution_selection
        selection = load_llm_execution_selection(self.execution_config_path)
        settings, _ = self._plugin_settings(
            {"user_input": self._selected_task_capabilities(selection)},
            scope="child_task")
        runtime = selection.runtime_policy
        handle = self.task_control.start(AgentTaskSpec(
            **settings,
            run_dir=run_dir,
            prompt=prompt,
            stages=stages,
            execution_config_path=self.execution_config_path,
            workflow_graph=workflow_graph,
            max_attempts_per_stage=runtime.max_turns_per_node,
            max_parallel_nodes=runtime.max_parallel_nodes,
            owner_statement="RPNH main-session authorized child task",
        ))
        self._index_child_registry(
            handle, run_dir=run_dir, origin_main_turn_ref=None)
        return handle

    def _relative_child_registry_path(self, run_dir: Path) -> str:
        try:
            return self.relative_child_path(run_dir)
        except ValueError as exc:
            raise RuntimeError(
                "child Registry path escapes the parent Registry") from exc

    def _committed_turn_ref(self, ordinal: int) -> VersionRef:
        for item in self._authority_projection["committed_history"]:
            if int(item["ordinal"]) == ordinal:
                return self._version_ref(item["turn_ref"])
        raise RuntimeError(
            "child task has no committed main-turn origin")

    def _index_child_registry(
            self, handle: TaskHandle, *, run_dir: Path,
            origin_main_turn_ref: VersionRef | None,
    ) -> None:
        relative_path = self._relative_child_registry_path(run_dir)
        self._main_thread.register_child_registry_link(
            task_control_id=handle.task_id,
            task_kind=handle.kind,
            registry_relative_path=relative_path,
            origin_main_turn_ref=origin_main_turn_ref,
            idempotency_key=f"child-registry:{handle.task_id}:register",
        )

    @staticmethod
    def _child_task_prompt(
            original_user_task: str, designer_execution_brief: str,
    ) -> str:
        """Preserve exact user input while retaining additive design guidance."""

        if (not isinstance(original_user_task, str)
                or not original_user_task.strip()):
            raise ValueError("child task requires a nonempty original user task")
        if (not isinstance(designer_execution_brief, str)
                or not designer_execution_brief.strip()):
            raise ValueError("child task requires a nonempty Designer brief")
        return (
            "RPNH ORIGINAL USER TASK (authoritative)\n"
            "--- BEGIN ORIGINAL USER TASK ---\n"
            f"{original_user_task}\n"
            "--- END ORIGINAL USER TASK ---\n\n"
            "RPNH DESIGNER EXECUTION BRIEF (supplemental)\n"
            "--- BEGIN DESIGNER EXECUTION BRIEF ---\n"
            f"{designer_execution_brief}\n"
            "--- END DESIGNER EXECUTION BRIEF ---"
        )

    def _launch_decision_task(
            self, task: MainTaskDecision | None, *, ordinal: int,
    ) -> TaskHandle | None:
        if ordinal in self._launched_children:
            handle = self._launched_children[ordinal]
            if handle is not None:
                self._index_child_registry(
                    handle,
                    run_dir=(
                        self._main_thread.child_path_root / "tasks" / "runs"
                        / f"main-turn-{ordinal:04d}-child"),
                    origin_main_turn_ref=self._committed_turn_ref(ordinal),
                )
            return handle
        if task is None:
            self._launched_children[ordinal] = None
            return None
        run_dir = (
            self._main_thread.child_path_root / "tasks" / "runs"
            / f"main-turn-{ordinal:04d}-child").resolve()
        matches: list[TaskHandle] = []
        for status in self.task_control.list():
            if Path(status["run_dir"]).resolve() == run_dir:
                matches.append(self.task_control.get(status["task_id"]))
        if len(matches) > 1:
            raise RuntimeError(
                "multiple child launches match one committed main turn")
        if matches:
            handle = matches[0]
            if handle.kind != task.kind:
                raise RuntimeError(
                    "recovered child task kind differs from main decision")
            self._launched_children[ordinal] = handle
            self._index_child_registry(
                handle, run_dir=run_dir,
                origin_main_turn_ref=self._committed_turn_ref(ordinal),
            )
            return handle
        origin_turn = self._turn_document(self._committed_turn_ref(ordinal))
        original_user_task, _required_task_kind = self._turn_input(
            origin_turn["user_input"])
        settings, _ = self._plugin_settings(origin_turn, scope="child_task")
        # A committed parent already has its result. Launching its child must
        # not reacquire the parent's process-local account provider.
        self._validate_account_task_settings({"child_task": settings})
        from cpn.llm_adapters import load_llm_execution_selection
        runtime = load_llm_execution_selection(
            self.execution_config_path).runtime_policy
        handle = self.task_control.start(AgentTaskSpec(
            **settings,
            run_dir=run_dir,
            prompt=self._child_task_prompt(
                original_user_task, task.prompt),
            stages=task.stages,
            execution_config_path=self.execution_config_path,
            workflow_graph=task.workflow_graph,
            max_attempts_per_stage=runtime.max_turns_per_node,
            max_parallel_nodes=runtime.max_parallel_nodes,
            owner_statement="RPNH main-session authorized child task",
        ))
        self._launched_children[ordinal] = handle
        self._index_child_registry(
            handle, run_dir=run_dir,
            origin_main_turn_ref=self._committed_turn_ref(ordinal),
        )
        return handle

    def reconcile_committed_launches(self) -> dict[int, TaskHandle | None]:
        """Recover every committed child launch from Registry authority."""
        self._assert_owner_lease()
        projection = self._refresh_from_authority()
        recovered: dict[int, TaskHandle | None] = {}
        for item in projection["committed_history"]:
            ordinal = int(item["ordinal"])
            decision = self._decision_from_document(item["answer"])
            recovered[ordinal] = self._launch_decision_task(
                decision.task, ordinal=ordinal)
        return recovered

    def reconcile_child_registry_links(self) -> tuple[dict[str, Any], ...]:
        """Index every session child and attach readable Registry identities."""

        self._assert_owner_lease()
        projection = self._refresh_from_authority()
        origins: dict[Path, tuple[VersionRef, str]] = {}
        for item in projection["committed_history"]:
            decision = self._decision_from_document(item["answer"])
            if decision.task is None:
                continue
            ordinal = int(item["ordinal"])
            run_dir = (
                self._main_thread.child_path_root / "tasks" / "runs"
                / f"main-turn-{ordinal:04d}-child").resolve()
            origins[run_dir] = (
                self._version_ref(item["turn_ref"]), decision.task.kind)

        for status in self.task_control.list():
            if (not isinstance(status, Mapping)
                    or not isinstance(status.get("task_id"), str)
                    or not isinstance(status.get("run_dir"), str)):
                raise RuntimeError(
                    "task control returned a malformed child index")
            task_id = status["task_id"]
            run_dir = Path(status["run_dir"]).resolve()
            handle = self.task_control.get(task_id)
            origin = origins.get(run_dir)
            if origin is not None and handle.kind != origin[1]:
                raise RuntimeError(
                    "child task kind differs from its committed main decision")
            self._index_child_registry(
                handle,
                run_dir=run_dir,
                origin_main_turn_ref=(
                    None if origin is None else origin[0]),
            )

        links = self._main_thread.reconcile_child_registry_links()
        self._refresh_from_authority()
        return links

    def activate_for_execution(self) -> dict[int, TaskHandle | None]:
        """Recover pending workers and committed launches before explicit execution."""

        self._assert_owner_lease()
        self.task_control._recover_pending_launches()
        recovered = self.reconcile_committed_launches()
        self.reconcile_child_registry_links()
        return recovered

    def _registered_decision_child(
            self, task: MainTaskDecision | None, *, ordinal: int,
    ) -> TaskHandle | None:
        """Observe an existing child handle without launching or indexing it."""

        if task is None:
            return None
        cached = self._launched_children.get(ordinal)
        if cached is not None:
            return cached

        turn_ref = self._committed_turn_ref(ordinal)
        turn_ref_payload = {
            "entity_type": turn_ref.entity_type,
            "logical_id": str(turn_ref.entity_id),
            "version_id": str(turn_ref.version_id),
        }
        linked_ids = [
            link["task_control_id"]
            for link in self._authority_projection["child_registry_links"]
            if link.get("origin_main_turn_ref") == turn_ref_payload
        ]
        if len(linked_ids) > 1:
            raise RuntimeError(
                "multiple child links match one committed main turn")
        if linked_ids:
            handle = self.task_control.get(linked_ids[0])
            if handle.kind != task.kind:
                raise RuntimeError(
                    "registered child task kind differs from main decision")
            return handle

        run_dir = (
            self._main_thread.child_path_root / "tasks" / "runs"
            / f"main-turn-{ordinal:04d}-child").resolve()
        matching_ids: list[str] = []
        for status in self.task_control.list():
            if (not isinstance(status, Mapping)
                    or not isinstance(status.get("task_id"), str)
                    or not isinstance(status.get("run_dir"), str)):
                raise RuntimeError(
                    "task control returned a malformed child index")
            if Path(status["run_dir"]).resolve() == run_dir:
                matching_ids.append(status["task_id"])
        if len(matching_ids) > 1:
            raise RuntimeError(
                "multiple child launches match one committed main turn")
        if not matching_ids:
            return None
        handle = self.task_control.get(matching_ids[0])
        if handle.kind != task.kind:
            raise RuntimeError(
                "registered child task kind differs from main decision")
        return handle

    def latest_committed_reconciliation(self) -> MainTurnReconciliation:
        """Return the latest committed decision and any registered child."""
        projection = self._refresh_from_authority()
        history = projection["committed_history"]
        if not history:
            return MainTurnReconciliation("idle", None)
        item = history[-1]
        decision = self._decision_from_document(item["answer"])
        child = self._registered_decision_child(
            decision.task, ordinal=int(item["ordinal"]))
        return MainTurnReconciliation(
            "committed", None, decision, child)

    @property
    def display_history(self) -> list[tuple[str, str]]:
        """Registry-derived transcript including durable launch annotations."""
        projection = self._refresh_from_authority()
        history: list[tuple[str, str]] = []
        for item in projection["committed_history"]:
            ordinal = int(item["ordinal"])
            user_text, _required = self._turn_input(item["user_input"])
            decision = self._decision_from_document(item["answer"])
            child = self._registered_decision_child(
                decision.task, ordinal=ordinal)
            history.extend((
                ("user", user_text),
                ("assistant", render_main_decision(decision, child)),
            ))
        return history

    def display_history_at(
            self, cut: MainThreadReadCut, turn_refs: tuple[VersionRef, ...], *,
            max_object_bytes: int | None = None,
    ) -> tuple[MainDisplayTurn, ...]:
        """Pure safe projection under the existing bound local-owner session.

        The caller rechecks its existing owner/source binding before read and
        delivery. A cut/ref never grants access. No TaskControl, profile,
        refresh, child read, persistence, activation, or reconciliation occurs.
        """
        projection, documents = self.history_registry._committed_turn_documents_at(
            cut, turn_refs, max_object_bytes=max_object_bytes)
        result = []
        for ref, document in zip(turn_refs, documents, strict=True):
            user_text, _required = self._turn_input(document["user_input"])
            decision = self._decision_from_document(document["answer"])
            text = render_main_decision(decision, None)
            links = [link for link in projection.child_links
                     if link.origin_turn_ref == ref]
            if len(links) > 1:
                raise RuntimeError("multiple child links match one committed turn")
            if links:
                link = links[0]
                if (decision.task is None or decision.task.kind != link.task_kind
                        or link.state not in {"launch_registered", "registry_attached"}):
                    raise RuntimeError("history launch fact differs from its decision")
                text += f"\n\n[launched {link.task_control_id}: {link.task_kind}]"
            result.append(MainDisplayTurn(
                ref, int(document["ordinal"]), user_text, text))
        return tuple(result)

    @property
    def history_registry(self) -> MainThreadRegistry:
        """A native read-only handle to this same Registry, never a second store.

        Owner live execution retains its writer. History uses mode=ro/query_only
        connections so SELECT-only requests cannot checkpoint its WAL on close.
        This handle creates neither a Registry nor an authority/grant/epoch.
        """
        if self._history_reader is None:
            core = _RegistryCore(self._registry_core.run_dir, create=False,
                                 read_only=True, catalog=self._registry_core.catalog)
            self._history_reader = MainThreadRegistry(core, session_root=self.root)
        return self._history_reader

    def prepare_turn(
            self, user_text: str, *,
            required_task_kind: str | None = None,
            idempotency_key: str | None = None,
    ) -> AgentTaskSpec:
        """Admit and prepare one turn through the existing Registry authority.

        Frontends may supply their exact acceptance identity. Only fresh
        admission captures owner-selected plugins; retries use the registered
        input, including the empty capability set of older turns. Completed
        request receipts remain the caller's observational lookup, not a new
        preparation or execution.
        """
        self._assert_owner_lease()
        if not isinstance(user_text, str) or not user_text.strip():
            raise ValueError("main-session input must be nonempty text")
        if required_task_kind not in {None, "workflow"}:
            raise ValueError("required main task kind is invalid")
        if idempotency_key is not None and (
                not isinstance(idempotency_key, str) or not idempotency_key):
            raise ValueError("main-turn acceptance identity must be nonempty text")
        self._refresh_from_authority()
        active = self._active_turn()
        if active is not None:
            snapshot = self.active_turn_snapshot()
            if snapshot is None:
                raise RuntimeError(
                    "active main turn disappeared during reconciliation")
            if snapshot.state == "stopped_by_owner":
                raise MainSessionPaused(
                    "RPNH main turn is paused at a Registry checkpoint; "
                    "resume or roll it back before starting another turn")
            turn_ref, turn = active
            self._assert_turn_input(
                turn, user_text, required_task_kind)
            if turn["state"] not in {"accepted", "running"}:
                raise RuntimeError("active main turn is not preparable")
            if idempotency_key is not None:
                # A running turn has exactly one predecessor: its accepted
                # version. Read the original command, never reconstruct an
                # older frontend identity using today's wire format.
                accepted_ref = (
                    turn_ref if turn["state"] == "accepted" else
                    self._version_ref(turn["previous_version_ref"]))
                accepted_turn = self._turn_document(accepted_ref)
                event = self._registry_core.event_store.object_publication_event(
                    accepted_ref.version_id, object_type=accepted_ref.entity_type)
                command = json.loads(event.command_id)
                if (not isinstance(command, dict)
                        or command.get("authority") != "main_thread/v1"
                        or command.get("command") != "accept_turn"
                        or command.get("caller_idempotency_key") != idempotency_key
                        or not isinstance(command.get("material"), dict)
                        or command["material"].get("user_input")
                        != accepted_turn["user_input"]):
                    raise ValueError(
                        "request identity differs from the active main-session turn")
            self.activate_for_execution()
            if turn["state"] == "accepted":
                ordinal = int(turn["ordinal"])
                attached = self._main_thread.attach_attempt(
                    thread_ref=self._thread_ref,
                    turn_ref=turn_ref,
                    attempt_relative_path=self._main_turn_path_ref(ordinal),
                    idempotency_key=f"main-turn-{ordinal}:attempt",
                )
                turn_ref = attached.turn_ref
                turn = self._turn_document(turn_ref)
            self._refresh_from_authority()
            self._persist_state()
            spec = self._task_spec(
                turn, user_text=user_text,
                required_task_kind=required_task_kind)
            self._release_empty_attempt_directory(spec.run_dir)
            return spec

        self.activate_for_execution()
        self._refresh_from_authority()
        ordinal = int(self._authority_projection["next_turn_ordinal"])
        capabilities = self._selected_task_capabilities()
        user_input = {
            **capabilities,
            "text": user_text,
            "required_task_kind": required_task_kind,
        }
        # Validate the existing task contract before publishing an active turn.
        # The same detached input and exact prospective path are then admitted
        # below; no execution may begin before the Registry attachment succeeds.
        spec = self._task_spec(
            {"user_input": user_input,
             "attempt_relative_path": self._main_turn_path_ref(ordinal)},
            user_text=user_text, required_task_kind=required_task_kind)
        accepted = self._main_thread.accept_turn(
            thread_ref=self._thread_ref,
            user_input=user_input,
            expected_ordinal=ordinal,
            idempotency_key=(
                idempotency_key if idempotency_key is not None else
                f"main-turn-{ordinal}:accept"),
        )
        attached = self._main_thread.attach_attempt(
            thread_ref=accepted.thread_ref,
            turn_ref=accepted.turn_ref,
            attempt_relative_path=self._main_turn_path_ref(ordinal),
            idempotency_key=f"main-turn-{ordinal}:attempt",
        )
        self._refresh_from_authority()
        self._persist_state()
        if spec.run_dir != attached.attempt_path:
            raise RuntimeError(
                "main-turn task path differs from Registry attachment")
        self._release_empty_attempt_directory(spec.run_dir)
        return spec

    def _main_turn_path_ref(self, ordinal: int) -> str:
        name = f"turn-{ordinal:04d}"
        if (self._main_thread.child_path_base
                == MainThreadRegistry.REGISTRY_ROOT_PATH_BASE):
            return name
        return f"main/{name}"

    def _completed_retry(
            self, *, user_text: str, required_task_kind: str | None,
            decision: MainDecision,
    ) -> tuple[MainDecision, TaskHandle | None]:
        latest_payload = self._authority_projection["latest_turn_ref"]
        if latest_payload is None:
            raise RuntimeError("no active main-session turn to complete")
        latest = self._turn_document(self._version_ref(latest_payload))
        self._assert_turn_input(latest, user_text, required_task_kind)
        if (latest["state"] != "committed"
                or latest.get("answer") != self._decision_document(decision)):
            raise RuntimeError("main-session turn is already finalized differently")
        ordinal = int(latest["ordinal"])
        return decision, self._launch_decision_task(
            decision.task, ordinal=ordinal)

    def _interrupted_retry(self, *, user_text: str) -> None:
        latest_payload = self._authority_projection["latest_turn_ref"]
        if latest_payload is None:
            raise RuntimeError("no active main-session turn to interrupt")
        latest = self._turn_document(self._version_ref(latest_payload))
        stored_text, _required = self._turn_input(latest.get("user_input"))
        if latest["state"] != "interrupted" or stored_text != user_text:
            raise RuntimeError("main-session turn is already finalized differently")

    def complete_interrupted_turn(self, user_text: str) -> None:
        """Explicitly roll a paused main turn out of conversation history.

        This changes only main-thread authority.  The child Registry named by
        the execution receipt is retained and is never deleted or rewound.
        """
        self._assert_owner_lease()
        if not isinstance(user_text, str) or not user_text.strip():
            raise ValueError("main-session input must be nonempty text")
        self._refresh_from_authority()
        active = self._active_turn()
        if active is None:
            self._interrupted_retry(user_text=user_text)
            return
        turn_ref, turn = active
        stored_text, _required = self._turn_input(turn.get("user_input"))
        if stored_text != user_text:
            raise ValueError(
                "request differs from the active main-session turn")
        if turn["state"] != "running":
            raise RuntimeError("main-session turn has no running attempt")
        ordinal = int(turn["ordinal"])
        receipt = self._main_thread.record_execution_receipt(
            turn_ref=turn_ref,
            idempotency_key=f"main-turn-{ordinal}:receipt",
        )
        self._main_thread.commit_interruption(
            thread_ref=self._thread_ref,
            turn_ref=turn_ref,
            receipt_ref=receipt,
            idempotency_key=f"main-turn-{ordinal}:interrupt",
        )
        self._refresh_from_authority()
        self._persist_state()

    def rollback_paused_turn(self) -> MainTurnReconciliation:
        """Return to the previous completed main turn without deleting child state."""

        self._assert_owner_lease()
        snapshot = self.active_turn_snapshot()
        if snapshot is None or snapshot.state != "stopped_by_owner":
            raise RuntimeError(
                "main-session rollback requires a paused main turn")
        self.complete_interrupted_turn(snapshot.user_text)
        return MainTurnReconciliation("interrupted", snapshot)

    def resume_paused_turn(
            self,
    ) -> tuple[MainDecision, TaskHandle | None]:
        """Resume the paused turn from its child Registry's latest checkpoint."""

        self._assert_owner_lease()
        snapshot = self.active_turn_snapshot()
        if snapshot is None or snapshot.state != "stopped_by_owner":
            raise RuntimeError(
                "main-session resume requires a paused main turn")
        active = self._active_turn()
        if active is None:
            raise RuntimeError("paused main turn has no active Registry turn")
        _turn_ref, turn = active
        self.activate_for_execution()
        spec = self._task_spec(
            turn,
            user_text=snapshot.user_text,
            required_task_kind=snapshot.required_task_kind,
        )
        self._release_empty_attempt_directory(spec.run_dir)
        result = resume_agent_task(spec)
        if result.get("stop_reason") == "stopped_by_owner":
            raise MainSessionPaused(
                "RPNH main turn was paused again at its Registry checkpoint")
        if result.get("stop_reason") != "terminal":
            self.fail_active_turn()
            raise MainSessionExecutionFailed(
                "RPNH main turn ended without terminal Registry evidence "
                f"(stop_reason={result.get('stop_reason')!r}); the child "
                "Registry was retained and the main thread is ready for "
                "another turn")
        return self.complete_turn(
            snapshot.user_text,
            result.get("output"),
            required_task_kind=snapshot.required_task_kind,
        )

    def complete_turn(
            self, user_text: str, output: object, *,
            required_task_kind: str | None = None,
    ) -> tuple[MainDecision, TaskHandle | None]:
        """Apply one registered main-turn output to logical session state."""
        self._assert_owner_lease()
        if not isinstance(user_text, str) or not user_text.strip():
            raise ValueError("main-session input must be nonempty text")
        self._refresh_from_authority()
        active = self._active_turn()
        if active is None:
            decision = self._parse_output(
                output, required_task_kind=required_task_kind)
            return self._completed_retry(
                user_text=user_text,
                required_task_kind=required_task_kind,
                decision=decision)
        turn_ref, turn = active
        self._assert_turn_input(turn, user_text, required_task_kind)
        if turn["state"] != "running":
            raise RuntimeError("main-session turn has no running attempt")
        ordinal = int(turn["ordinal"])
        receipt = self._main_thread.record_execution_receipt(
            turn_ref=turn_ref,
            idempotency_key=f"main-turn-{ordinal}:receipt",
        )
        decision = self._parse_output(
            output, required_task_kind=required_task_kind)
        self._main_thread.commit_terminal_answer(
            thread_ref=self._thread_ref,
            turn_ref=turn_ref,
            receipt_ref=receipt,
            answer=self._decision_document(decision),
            idempotency_key=f"main-turn-{ordinal}:commit",
        )
        self._refresh_from_authority()
        self._persist_state()
        handle = self._launch_decision_task(
            decision.task, ordinal=ordinal)
        return decision, handle

    def fail_active_turn(self) -> MainTurnReconciliation:
        """Close a running main turn while retaining its nonterminal child."""

        self._assert_owner_lease()
        snapshot = self.active_turn_snapshot()
        if snapshot is None or snapshot.state != "running":
            raise RuntimeError(
                "main-session failure requires a running child Registry")
        active = self._active_turn()
        if active is None:
            raise RuntimeError("failed main turn has no active Registry turn")
        turn_ref, turn = active
        ordinal = int(turn["ordinal"])
        receipt = self._main_thread.record_execution_receipt(
            turn_ref=turn_ref,
            idempotency_key=f"main-turn-{ordinal}:failure-receipt",
            allow_running=True,
        )
        self._main_thread.commit_execution_failure(
            thread_ref=self._thread_ref,
            turn_ref=turn_ref,
            receipt_ref=receipt,
            idempotency_key=f"main-turn-{ordinal}:fail",
        )
        self._refresh_from_authority()
        self._persist_state()
        return MainTurnReconciliation("failed", snapshot)

    def turn(
            self, user_text: str, *,
            required_task_kind: str | None = None,
    ) -> tuple[MainDecision, TaskHandle | None]:
        self._assert_owner_lease()
        spec = self.prepare_turn(
            user_text, required_task_kind=required_task_kind)
        if (self._managed_account_resolver_factory is None
                or not spec.managed_bindings):
            result = run_agent_task(spec)
        else:
            result = run_agent_task(
                spec, managed_account_resolver_factory=self._managed_account_resolver_factory)
        if result.get("stop_reason") == "stopped_by_owner":
            raise MainSessionPaused(
                "RPNH main turn is paused at its Registry checkpoint")
        if result.get("stop_reason") != "terminal":
            self.fail_active_turn()
            raise MainSessionExecutionFailed(
                "RPNH main turn ended without terminal Registry evidence "
                f"(stop_reason={result.get('stop_reason')!r}); the child "
                "Registry was retained and the main thread is ready for "
                "another turn")
        return self.complete_turn(
            user_text, result.get("output"),
            required_task_kind=required_task_kind)


__all__ = (
    "MainDecision", "MainSession", "MainSessionExecutionFailed",
    "MainSessionInterrupted", "MainSessionPaused",
    "MainTaskDecision", "MainTurnReconciliation", "MainTurnSnapshot",
    "parse_main_decision", "render_main_decision",
)
