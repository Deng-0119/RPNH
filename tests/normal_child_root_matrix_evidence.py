"""Dormant evidence helpers for explicitly pinned normal-child matrix nodes.

Importing this module performs no product imports or fixture construction.
These helpers neither grant runtime permission nor replace product validation.
"""


def collect_closed_normal_root_evidence(target, completed):
    """Retain the values used by the required same-TX and cold-read assertions.

    Every row, cut and object hash is captured at its assertion read. There is
    no second database or filesystem read solely to serialize a witness.
    """
    import hashlib

    from cpn.frontend.worksets import workset_view
    from cpn.rpnh.collaboration.root_terminals import read_root_terminal
    from cpn.rpnh.registry._registry import _RegistryCore

    root, workset, seal = completed["root"], completed["workset"], completed["seal"]
    references = [
        root["record_ref"]["ref"],
        workset["record_ref"]["ref"],
        seal["execution_child_seal_ref"],
        *[child["execution_terminal_mapping_ref"] for child in seal["children"]],
        root["body"]["completion_ref"],
        root["body"]["checkpoint_ref"],
        seal["operation_result_ref"],
    ]
    assert len({ref["version_id"] for ref in references}) == len(references)
    membership = []
    for reference in references:
        row = target._core.event_store.object_row(reference["version_id"])
        assert row is not None
        captured = dict(row)
        assert captured["object_type"] == reference["entity_type"]
        assert captured["logical_id"] == reference["logical_id"]
        assert captured["version_id"] == reference["version_id"]
        membership.append({"requested_ref": dict(reference), "object_row": captured})
    assert {item["object_row"]["transaction_id"] for item in membership} == {
        seal["success_transaction_id"]
    }

    object_root = target._core.object_store.root
    before = {
        "committed_ordinal": target._core.event_store.max_ordinal(),
        "writer_epoch": target._core.event_store.writer_epoch,
        "immutable_object_sha256": {
            str(path.relative_to(object_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in object_root.rglob("*") if path.is_file()
        },
    }
    readonly = _RegistryCore(
        target._core.run_dir, create=False, read_only=True, catalog=target._core.catalog
    )
    cold = read_root_terminal(readonly, root["record_ref"])
    view = workset_view(readonly)
    assert cold["root"] == root and cold["workset"] == workset and cold["seal"] == seal
    assert view["schema_version"] == "rpnh/workset_view/v2"
    row, = [item for item in view["current"] if item["workset_ref"] == workset["record_ref"]]
    assert row["root_child_closure"]["seal_ref"] == root["body"]["required_child_seal_ref"]
    assert row["required_child_seal_ref"] is None
    assert row["root_terminal_evidence_ref"] is None
    assert row["physical_coverage"] == "source_not_observed"
    assert row["acceptance_count"] == row["contribution_count"] == 1
    after = {
        "committed_ordinal": readonly.event_store.max_ordinal(),
        "writer_epoch": readonly.event_store.writer_epoch,
        "immutable_object_sha256": {
            str(path.relative_to(object_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in object_root.rglob("*") if path.is_file()
        },
    }
    assert after == before
    return {
        "schema_version": "normal-child-root-closed-evidence/v1",
        "completed": completed,
        "same_success_object_membership": membership,
        "before_cold": before,
        "cold_result": cold,
        "python_view": view,
        "after_cold": after,
        "physical_coverage": "source_not_observed",
        "root_success_transaction_id": seal["success_transaction_id"],
        "observation_contract": "captured_from_required_assertion_reads_without_extra_reads",
    }


def snapshot_authority(owner, *, workset_reference, parent_references):
    """Read the specified fresh fixture's rejection baseline on one SQL cut.

    Full selected authoritative tables are compared by canonical row hashes.
    The relevant Workset, parents and execution objects are retained as rows.
    This is a test witness, not a substitute for any product validator.
    """
    import hashlib
    import json

    tables = ("registry_meta", "transactions", "objects", "events", "relations",
              "firing_publications", "firing_temporary_members", "stream_heads", "snapshots")
    captured = {}
    with owner._core.event_store.connect() as database:
        database.execute("BEGIN")
        for table in tables:
            captured[table] = [dict(row) for row in database.execute(
                "SELECT * FROM " + table + " ORDER BY rowid").fetchall()]
    table_witness = {
        table: {"rows": len(rows), "sha256": hashlib.sha256(json.dumps(
            rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()}
        for table, rows in captured.items()
    }
    logical_id = workset_reference["ref"]["logical_id"]
    worksets = [row for row in captured["objects"] if row["logical_id"] == logical_id]
    assert worksets
    latest = max(worksets, key=lambda row: json.loads(row["metadata_json"])["body"]["sequence"])
    firing_versions = {str(parent.business_firing_ref.version_id) for parent in parent_references}
    invocation_ids = {str(parent.invocation_ref.entity_id) for parent in parent_references}
    execution_types = {
        "execution_instance/v1", "execution_net_definition/v1", "execution_checkpoint/v1",
        "execution_token/v1", "execution_transition_firing/v1", "execution_terminal_mapping/v1",
        "execution_child_seal/v1",
    }
    return {
        "schema_version": "normal-child-root-rejection-snapshot/v1",
        "committed_ordinal": max(row["ordinal"] for row in captured["events"]),
        "registry_meta": captured["registry_meta"],
        "table_witnesses": table_witness,
        "workset_latest_object": latest,
        "workset_stream_head": [row for row in captured["stream_heads"]
                                if row["stream_id"] == "object:" + logical_id],
        "parent_publications": [row for row in captured["firing_publications"]
                                if row["firing_version_id"] in firing_versions],
        "execution_objects": [row for row in captured["objects"]
                              if row["object_type"] in execution_types
                              and row["producer_invocation_id"] in invocation_ids],
        "ordinary_allocation_objects": [row for row in captured["objects"]
            if row["object_type"] in {"marking_delta/v1", "firing_completion/v2",
                "marking_checkpoint/v1", "petri_token/v1", "native_genesis_manifest/v1"}],
        "initial_catalog_objects": [{
            **{key: value for key, value in row.items() if key != "metadata_json"},
            "metadata_json_sha256": hashlib.sha256(row["metadata_json"].encode()).hexdigest(),
            "expected_catalog_payload_sha256": hashlib.sha256(json.dumps(
                json.loads(row["metadata_json"]), sort_keys=True,
                separators=(",", ":")).encode("utf-8")).hexdigest(),
            "marking_delta_schema_entry": json.loads(row["metadata_json"])["schemas"]["registry_v1/marking_delta/v1"],
        } for row in captured["objects"] if row["object_type"] == "registry_type_catalog/v1"],
        "child_stream_heads": [row for row in captured["stream_heads"]
                               if row["stream_id"] in {
                                   "execution-children:" + version for version in firing_versions}],
    }


def expect_rejected_without_commit(owner, *, case_id, workset_reference,
                                   parent_references, action, exception_type,
                                   message, guard_qualname):
    """Require the named product rejection and unchanged authoritative state."""
    before = snapshot_authority(owner, workset_reference=workset_reference,
                                parent_references=parent_references)
    try:
        action()
    except exception_type as error:
        assert type(error) is exception_type
        assert str(error) == message
        frames = []
        trace = error.__traceback__
        while trace is not None:
            code = trace.tb_frame.f_code
            frames.append({"filename": code.co_filename, "qualname": code.co_qualname,
                           "line": trace.tb_lineno})
            trace = trace.tb_next
        assert any(frame["qualname"] == guard_qualname for frame in frames)
        rejected = {"type": type(error).__name__, "message": str(error), "frames": frames}
    else:
        raise AssertionError(case_id + " unexpectedly succeeded")
    after = snapshot_authority(owner, workset_reference=workset_reference,
                               parent_references=parent_references)
    assert after == before
    assert owner.control.edits.queue == []
    return {"case_id": case_id, "status": "EXPECTED_PRODUCT_REJECTION",
            "error": rejected, "before": before, "after": after,
            "prewritten_file_rollback_claimed": False}
