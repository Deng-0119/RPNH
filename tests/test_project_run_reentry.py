"""Original project launch provenance survives explicit native reentry."""
from pathlib import Path
import json
import sys

import pytest

from cpn.rpnh.agent_tasks import AgentStage, build_agent_task_module, TEXT_SCHEMA
from cpn.rpnh.collaboration.project_local import initialize, LocalProjectApplication
from cpn.rpnh.collaboration.project_native import registration, catalog, execute_task
from cpn.rpnh.llm_contracts import LLMInputPortFailure
from cpn.rpnh.registry.checkpoint_reentry import committed_checkpoint_refs
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.run import OwnerInput, resume_run


def test_project_run_reuses_exact_launch_only_after_original_checkpoint_reentry(tmp_path: Path, monkeypatch):
    root = tmp_path / 'project'
    adapter = tmp_path / 'adapter.json'
    adapter.write_text(json.dumps({'schema_version': 'local_process_adapter_config/v1',
        'adapter_kind': 'local_process', 'model_condition': 'offline-project-reentry',
        'argv': [sys.executable, '-c', 'pass'], 'probe_argv': [sys.executable, '-c', 'pass'],
        'env': {}, 'inherit_env': []}))
    profile = tmp_path / 'execution.json'
    profile.write_text(json.dumps({'schema_version': 'llm_execution_selection/v1',
        'adapter_kind': 'local_process', 'model_condition': 'offline-project-reentry',
        'adapter_config_path': str(adapter), 'timeout_seconds': 30,
        'max_output_tokens': 1024, 'max_response_bytes': 65536}))
    module = build_agent_task_module([AgentStage('worker', 'Report the exact input.')])
    initialize(root, module, {'request': OwnerInput(TEXT_SCHEMA, canonical_json('test'), 'input')}, 1,
        execution_profile=profile)
    app = LocalProjectApplication(root)
    # Request admission and an offline failing port; no external model/network.
    app.execution_loop = object()
    command = {'op': 'run', 'command_id': 'original-owner-run'}
    launch = app.process('owner', command)
    session = ref_payload(app.endpoints['owner'].session.ref)
    principal = ref_payload(app.owner.principal_ref)
    app.run_requested = None
    app.run_state = None
    head = app.owner._core.event_store.max_ordinal()
    with pytest.raises(ValueError, match='previously admitted outside this process'):
        app.process('owner', command)
    assert app.owner._core.event_store.max_ordinal() == head
    with pytest.raises(PermissionError):
        app.process('contributor-1', command)

    selected = committed_checkpoint_refs(app.owner._core)[-1]
    class NotSubmittedPort:
        calls = 0
        execution_policy = app.selection.as_registry_policy()

        def request_once(self, _attempt):
            self.calls += 1
            raise LLMInputPortFailure('adapter_not_submitted',
                submission_state='not_submitted', failure_code='offline_test_failure')

        def close(self):
            pass

    port = NotSubmittedPort()
    monkeypatch.setattr('cpn.llm_adapters.build_llm_input_port', lambda *_args, **_kwargs: port)
    result = execute_task(app.owner, tmp_path / 'dispatcher.sock', app.selection)
    assert result['stop_reason'] == 'blocked_or_waiting' and port.calls == 1
    failures = app.owner._core.event_store.list_events_by_type(('llm_invocation_failed/v1',))
    assert len(failures) == 1 and failures[0].payload['submission_state'] == 'not_submitted'
    assert len(app.owner.snapshot()['active_firings']) == 1
    app.owners['contributor-1'].record_owner_stop(idempotency_key='test-contributor-stop')
    app.close_readers()
    # The old owner is no longer used; the original recovery validates then
    # fences it and settles interruption before creating the successor cut.
    recovered = resume_run(registration(), run_dir=root / 'owner', model_condition=app.selection.input_target.model_condition,
        catalog=catalog(), checkpoint_version_id=str(selected.version_id),
        reopen_command_id='test-project-reentry', reopen_reason='Resume original owner task after interruption')
    assert recovered.snapshot()['active_firings'] == []
    recovered.record_owner_stop(idempotency_key='test-recovered-stop')
    reopened = LocalProjectApplication(root)
    reopened.execution_loop = object()
    try:
        assert ref_payload(reopened.endpoints['owner'].session.ref) == session
        assert ref_payload(reopened.owner.principal_ref) == principal
        head = reopened.owner._core.event_store.max_ordinal()
        resumed = reopened.process('owner', command)
        assert resumed['execution_ref'] == launch['execution_ref']
        assert reopened.owner._core.event_store.max_ordinal() == head
        assert reopened.process('owner', command) == resumed
        assert reopened.owner._core.event_store.actual_model_call_counts() == (0, 0)
        assert len(reopened.owner._core.event_store.object_rows_by_type('llm_call_spec/v2')) == 1
        retained = reopened.owner._core.event_store.list_events_by_type(('llm_invocation_failed/v1',))
        assert [e.event_id for e in retained] == [e.event_id for e in failures]
    finally:
        reopened.process('owner', {'op': 'stop-host', 'command_id': 'test-final-stop'})
        reopened.close_readers()
