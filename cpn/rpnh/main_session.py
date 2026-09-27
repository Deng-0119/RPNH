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
    """Interactive controller whose reasoning turn is itself one RPNH agent."""

    STATE_FILE = "session_state.json"
    PROFILE_FILE = "execution_profile.json"
    REGISTRY_DIR = "main"

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
    ) -> None:
        if not isinstance(root, Path) or not isinstance(execution_config_path, Path):
            raise TypeError("MainSession paths require pathlib.Path")
        self.root = root.resolve()
        self.execution_config_path = execution_config_path.resolve()
        persisted_profile = (
            self._persisted_execution_profile(self.root)
            if resume else None)
        if resume:
            if not self.root.is_dir():
                raise ValueError(
                    "RPNH resume requires an existing session directory")
            try:
                self._registry_core = _RegistryCore(
                    self.root / self.REGISTRY_DIR, create=False)
            except Exception as exc:
                raise ValueError(
                    "RPNH session has no valid main-thread Registry") from exc
        else:
            if os.path.lexists(self.root):
                raise ValueError(
                    "RPNH main session requires an absent session directory")
            self.root.mkdir(parents=True)
            self._registry_core = _RegistryCore(
                self.root / self.REGISTRY_DIR, create=True)
        self._main_thread = MainThreadRegistry(
            self._registry_core, session_root=self.root,
            initialize_path_base=(
                MainThreadRegistry.REGISTRY_ROOT_PATH_BASE
                if not resume else None),
        )
        if not resume:
            self._main_thread.create_thread(
                idempotency_key="main-session-thread")
        self._launched_children: dict[int, TaskHandle | None] = {}
        self.task_control = task_control or TaskControl(
            self._main_thread.child_path_root / "tasks")
        self._refresh_from_authority()
        if resume and self._active_turn() is not None:
            if persisted_profile is None:
                raise ValueError(
                    "active main-session turn has no persisted execution "
                    "profile authority")
            if self.execution_config_path != persisted_profile[
                    "execution_config_path"]:
                raise ValueError(
                    "cannot change the execution profile while a main-session "
                    "turn is active")
            if persisted_profile["schema_version"] == (
                    "rpnh/main_session_profile/v2"):
                self._assert_execution_profile_identity(persisted_profile)
        self._persist_profile()
        self._persist_state()
        if resume:
            self.reconcile_committed_launches()
            self.reconcile_child_registry_links()

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
            "schema_version": "rpnh/main_session_profile/v2",
            "execution_config_path": str(self.execution_config_path),
            "adapter_config_path": str(selection.adapter_config_path),
            "selection_id": profile.selection_id,
            "provider": profile.provider,
            "model_condition": selection.input_target.model_condition,
            "adapter_kind": selection.adapter_kind,
            "registry_policy": selection.as_registry_policy(),
        }

    def _assert_execution_profile_identity(
            self, persisted: Mapping[str, object],
    ) -> None:
        expected = dict(persisted)
        expected["execution_config_path"] = str(
            expected["execution_config_path"])
        if self._execution_profile_document() != expected:
            raise ValueError(
                "execution profile identity changed while a main-session "
                "turn is active")

    @classmethod
    def resume(
            cls, root: Path, execution_config_path: Path | None = None, *,
            task_control: TaskControl | None = None,
    ) -> "MainSession":
        root = root.resolve()
        selected = execution_config_path
        if selected is None:
            selected = cls._persisted_execution_config_path(root)
            if selected is None:
                raise ValueError(
                    "RPNH resume requires execution_config_path when the "
                    "profile projection is unavailable")
        return cls(
            root, selected, task_control=task_control, resume=True)

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
                or set(value) not in ({"text", "required_task_kind"},
                                      {"text", "required_task_kind", "native_plugins"})
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
        return {"configuration": document, "catalog_digest": catalog.digest}

    @staticmethod
    def _plugin_settings(turn):
        from cpn.plugins.catalog import load_catalog
        native = turn.get("user_input", {}).get("native_plugins")
        if native is None:
            return {}, ()
        if not isinstance(native, Mapping) or set(native) != {"configuration", "catalog_digest"}:
            raise ValueError("invalid registered native plugin selection")
        catalog = load_catalog(native["configuration"])
        if catalog.digest != native["catalog_digest"]:
            raise ValueError("installed plugins differ from the registered main-turn catalog")
        return {"plugin_configuration": native["configuration"],
                "plugin_catalog_digest": native["catalog_digest"]}, catalog.describe()

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

        snapshot = self.active_turn_snapshot()
        if snapshot is None:
            self.reconcile_committed_launches()
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
        if persisted["schema_version"] == "rpnh/main_session_profile/v2":
            self._assert_execution_profile_identity(persisted)
        try:
            run_dir = self._main_thread.resolve_child_path(relative_path)
        except ValueError as exc:
            raise RuntimeError(
                "registered main-turn attempt escapes its parent Registry"
            ) from exc
        try:
            run_dir.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            # A nonempty attempt is already being initialized or has become
            # the child Registry.  Never remove any child-owned contents.
            pass
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
        if not isinstance(path, Path):
            raise TypeError("execution config path requires pathlib.Path")
        selected = path.resolve()
        self._refresh_from_authority()
        if (selected != self.execution_config_path
                and self._active_turn() is not None):
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
        run_dir = self._main_thread.child_path_root / "tasks" / "runs" / (
            "child-" + uuid4().hex[:12])
        native = self._selected_plugins()
        settings, _ = self._plugin_settings({"user_input": {"native_plugins": native}})
        from cpn.llm_adapters import load_llm_execution_selection
        runtime = load_llm_execution_selection(
            self.execution_config_path).runtime_policy
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
        settings, _ = self._plugin_settings(origin_turn)
        from cpn.llm_adapters import load_llm_execution_selection
        runtime = load_llm_execution_selection(
            self.execution_config_path).runtime_policy
        handle = self.task_control.start(AgentTaskSpec(
            **settings,
            run_dir=run_dir,
            prompt=task.prompt,
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

    def latest_committed_reconciliation(self) -> MainTurnReconciliation:
        """Return the latest committed decision with its recovered child."""
        projection = self._refresh_from_authority()
        history = projection["committed_history"]
        if not history:
            return MainTurnReconciliation("idle", None)
        item = history[-1]
        decision = self._decision_from_document(item["answer"])
        child = self._launch_decision_task(
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
            child = self._launch_decision_task(
                decision.task, ordinal=ordinal)
            history.extend((
                ("user", user_text),
                ("assistant", render_main_decision(decision, child)),
            ))
        return history

    def prepare_turn(
            self, user_text: str, *,
            required_task_kind: str | None = None,
    ) -> AgentTaskSpec:
        """Create the exact independent task used for one main-session turn."""
        if not isinstance(user_text, str) or not user_text.strip():
            raise ValueError("main-session input must be nonempty text")
        if required_task_kind not in {None, "workflow"}:
            raise ValueError("required main task kind is invalid")
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
            elif turn["state"] != "running":
                raise RuntimeError("active main turn is not preparable")
            self._refresh_from_authority()
            self._persist_state()
            return self._task_spec(
                turn, user_text=user_text,
                required_task_kind=required_task_kind)

        ordinal = int(self._authority_projection["next_turn_ordinal"])
        native = self._selected_plugins()
        accepted = self._main_thread.accept_turn(
            thread_ref=self._thread_ref,
            user_input={
                **({"native_plugins": native} if native is not None else {}),
                "text": user_text,
                "required_task_kind": required_task_kind,
            },
            expected_ordinal=ordinal,
            idempotency_key=f"main-turn-{ordinal}:accept",
        )
        attached = self._main_thread.attach_attempt(
            thread_ref=accepted.thread_ref,
            turn_ref=accepted.turn_ref,
            attempt_relative_path=self._main_turn_path_ref(ordinal),
            idempotency_key=f"main-turn-{ordinal}:attempt",
        )
        turn = self._turn_document(attached.turn_ref)
        self._refresh_from_authority()
        self._persist_state()
        spec = self._task_spec(
            turn, user_text=user_text,
            required_task_kind=required_task_kind)
        if spec.run_dir != attached.attempt_path:
            raise RuntimeError(
                "main-turn task path differs from Registry attachment")
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

        snapshot = self.active_turn_snapshot()
        if snapshot is None or snapshot.state != "stopped_by_owner":
            raise RuntimeError(
                "main-session resume requires a paused main turn")
        active = self._active_turn()
        if active is None:
            raise RuntimeError("paused main turn has no active Registry turn")
        _turn_ref, turn = active
        spec = self._task_spec(
            turn,
            user_text=snapshot.user_text,
            required_task_kind=snapshot.required_task_kind,
        )
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
        spec = self.prepare_turn(
            user_text, required_task_kind=required_task_kind)
        result = run_agent_task(spec)
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
