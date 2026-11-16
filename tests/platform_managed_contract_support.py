"""Real Registry fixture for selected managed-binding resume admission.

The HOST publisher supplies deliberate version/field fixtures through its
normal resource publication, without editing SQL or object bytes. No model,
managed worker, Owner listener or workspace command is executed. The declared
tools are explicitly narrow and exclude workspace capability requirements.
This file uses only stdlib and product cpn, so an installed Python can run it
with -I; it never puts the repository on sys.path.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from unittest.mock import patch

from cpn.components.agent_loop.optional_execution import OptionalAgentLoopRegistryService
from cpn.components.agent_loop.optional_host_bindings import make_optional_agent_host_bindings
from cpn.plugins import API_VERSION_V2, PluginDefinition, PluginOperation, plugin_implementation
from cpn.plugins.catalog import BoundPlugin, PluginCatalog
from cpn.plugins.managed_tools import ManagedPluginToolCatalog
from cpn.rpnh.agent_tasks import AgentStage, TEXT_SCHEMA, agent_task_catalog, agent_task_registration, build_agent_task_module
from cpn.rpnh.llm_contracts import LLMInputTarget
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, resume_run, start_run


MODEL = "offline-selected-managed-contract"
ROLE = "optional_agent_managed_tool_bindings"


@plugin_implementation("tests.selected_managed_contract", "1")
def unused_tool(context, arguments):
    raise AssertionError("managed-contract admission cannot dispatch a tool")


def selected_catalog():
    definition = PluginDefinition("contract_fixture", "1", (
        PluginOperation("read", "Unused local pure fixture", {"type": "object"},
                        {"type": "object"}, unused_tool,
                        identity_version="v2", declaration_revision="1"),
    ), api_version=API_VERSION_V2)
    return PluginCatalog((BoundPlugin(definition, {}, binding_revision="1"),),
                         catalog_revision="1")


def unknown_version(document):
    document["schema_version"] = "rpnh/agent_loop_managed_tool_bindings/v999"
    return document


def unknown_registration_key(document):
    document["bindings"]["contract_read"]["registration_key"] = "future/unregistered_tool/v1"
    return document


def make_stopped_run(root: Path, *, mutate=None, unselected_unknown=False,
                     unselected_mutate=None, stop=True, empty_tools=False):
    from cpn.components.agent_loop import optional_host_bindings as host

    selected = selected_catalog()
    managed = ManagedPluginToolCatalog(selected, {
        "contract_read": {"selector": "contract_fixture/read", "descriptor_revision": "1"}},
        protocol_version="v3")
    registration = agent_task_registration(selected, (managed,))
    module = build_agent_task_module((AgentStage("worker", "Read-only admission fixture"),),
        max_attempts_per_stage=2,
        managed_tools={"worker": tuple(tool.registration_key for tool in managed.tools)},
        managed_tool_names={"worker": tuple(tool.name for tool in managed.tools)})
    document = module.to_dict()
    document["components"][0]["operations"][0]["tools"] = ([] if empty_tools else sorted([
        "write_file", "complete_interaction", *[tool.registration_key for tool in managed.tools]]))
    module = ModuleDeclaration.from_dict(document)
    original_publish = host._publish_private_system
    published = []
    original_command = None

    def publish(core, task_ref, command):
        nonlocal original_command
        if command.descriptors.get("content_role") == ROLE:
            original_command = command
            payload = json.loads(command.payload)
            if mutate is not None:
                payload = mutate(payload)
                command = replace(command, payload=canonical_json(payload))
            reference = original_publish(core, task_ref, command)
            published.append({"resource_ref": reference.as_version_ref(), "document": payload})
            return reference
        return original_publish(core, task_ref, command)

    request = OwnerInput(TEXT_SCHEMA, canonical_json("Never dispatch"), "fixture")
    with patch.object(host, "_publish_private_system", publish):
        owner = start_run(module, registration, run_dir=root / "run",
            task_input=request, entry_inputs={"request": request},
            budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
                ("rpnh/module_declaration/v1",), 2, 0, 2, 0),
            model_condition=MODEL, owner_statement="Selected contract admission fixture",
            command_id="managed-contract:create", catalog=agent_task_catalog(selected),
            host_execution_bindings=make_optional_agent_host_bindings(
                LLMInputTarget(MODEL, 1024, 65536), managed_catalogs={"worker": managed}))
    if unselected_unknown or unselected_mutate is not None:
        assert original_command is not None
        change = unselected_mutate or unknown_version
        original_publish(owner._core, owner.identity.task_ref, replace(original_command,
            payload=canonical_json(change(json.loads(original_command.payload))),
            idempotency_key="managed-contract:unselected-history"))
    # Pending-edit tests keep the original initial authority running. Ordinary
    # resume fixtures take the real original owner-stop boundary.
    if stop:
        owner.record_owner_stop(idempotency_key="managed-contract:owner-stop")
    assert len(published) == 1
    return owner, selected, managed, published[0]


def registry_state(run_dir):
    database = run_dir / ".registry_v1/registry.sqlite3"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        facts = {"meta": dict(db.execute("SELECT key,value FROM registry_meta ORDER BY key")),
            "events": db.execute("SELECT count(*) FROM events").fetchone()[0],
            "objects": db.execute("SELECT count(*) FROM objects").fetchone()[0],
            "run_authorities": db.execute("SELECT count(*) FROM objects WHERE object_type='run_execution_authority/v1'").fetchone()[0]}
        sql = tuple(db.iterdump())
    directory = database.parent / "objects"
    objects = tuple((p.relative_to(directory).as_posix(), p.read_bytes())
                    for p in sorted(directory.rglob("*")) if p.is_file())
    facts.update(object_files=len(objects), object_bytes=sum(len(raw) for _, raw in objects))
    return facts, (sql, objects)


def reopen(owner, selected, managed):
    return resume_run(agent_task_registration(selected, (managed,)),
        run_dir=owner._core.run_dir, model_condition=MODEL, catalog=agent_task_catalog(selected))


def original_execution_reader(owner, *, validate_catalog=False):
    admitted = owner.admit("worker.run", logical_tau=1, command_id="managed-contract:admit")
    if admitted is None:
        raise AssertionError("fixture operation was not enabled")
    execution = owner.start(admitted, command_id="managed-contract:start")
    kernel, repository = owner.operation_repository()
    service = OptionalAgentLoopRegistryService(owner=owner, kernel=kernel,
        repository=repository, provider_attempts=None, invoke_tool=unused_tool)
    context = execution.operation.canonical.context
    if validate_catalog:
        service._catalog(context, service.current_agent_tool_catalog_v1(execution))
    return service._managed_tool_bindings(context)


def reproduce(output: Path, source_reference: str, expect: str, *, case="unknown-version"):
    if output.exists():
        raise ValueError("evidence output must not already exist")
    began = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="rpnh-managed-contract-") as directory:
        mutate = {"unknown-version": unknown_version,
                  "registration-key": unknown_registration_key}[case]
        owner, selected, managed, resource = make_stopped_run(Path(directory), mutate=mutate)
        before, exact_before = registry_state(owner._core.run_dir)
        failure = None
        resumed = None
        try:
            resumed = reopen(owner, selected, managed)
        except Exception as error:
            failure = {"type": type(error).__name__, "message": str(error)}
        after, exact_after = registry_state(owner._core.run_dir)
        late_error = None
        if resumed is not None:
            try:
                original_execution_reader(resumed, validate_catalog=case == "registration-key")
            except Exception as error:
                late_error = {"type": type(error).__name__, "message": str(error)}
        facts = {"source_reference": source_reference, "case": case, "python": sys.executable,
            "isolated": bool(sys.flags.isolated), "elapsed_seconds": time.monotonic() - began,
            "before_resume": before, "after_resume": after,
            "full_sql_and_object_bytes_unchanged": exact_before == exact_after,
            "resume_error": failure, "original_post_start_reader_error": late_error,
            "selected_resource": {"entity_type": resource["resource_ref"].entity_type,
                "logical_id": str(resource["resource_ref"].entity_id),
                "version_id": str(resource["resource_ref"].version_id)},
            "selected_contract": resource["document"]["schema_version"],
            "selected_registration_key": resource["document"]["bindings"]["contract_read"]["registration_key"],
            "selected_workspace_tool": False, "native_owner_listener_started": False,
            "real_model_or_plugin_calls": 0,
            "qualifier": "real Registry/PN admission with deliberate HOST-published metadata; no native/complete-business qualification"}
        late_message = ("managed tool binding resource is malformed" if case == "unknown-version"
                        else "optional tool catalog differs from the exact admitted declaration")
        expected_late = (resumed is not None
            and late_error == {"type": "ResourceIntegrityFault",
                               "message": late_message}
            and not facts["full_sql_and_object_bytes_unchanged"])
        expected_early = (failure is not None
            and failure["type"] == ("ManagedContractAdmissionError" if case == "unknown-version"
                                    else "ToolCatalogAdmissionError")
            and str(resource["resource_ref"].version_id) in failure["message"]
            and late_message in failure["message"]
            and exact_before == exact_after)
        facts["status"] = ("EXPECTED_LATE_REJECTION" if expected_late else
            "EXPECTED_PREWRITE_REJECTION" if expected_early else "UNEXPECTED")
        expected = "EXPECTED_LATE_REJECTION" if expect == "late-rejection" else "EXPECTED_PREWRITE_REJECTION"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n")
        return facts, facts["status"] == expected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-reference", required=True)
    parser.add_argument("--expect", choices=("late-rejection", "early-rejection"), required=True)
    parser.add_argument("--case", choices=("unknown-version", "registration-key"), default="unknown-version")
    options = parser.parse_args()
    result, passed = reproduce(options.output, options.source_reference, options.expect, case=options.case)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if passed else 1)
