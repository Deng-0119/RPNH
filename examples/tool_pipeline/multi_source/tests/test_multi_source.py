"""Unit adversarial rows are synthetic; runtime checks use ONLY original fixtures."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path

import pytest
from cpn.rpnh.compiler import compile_module
from cpn.frontend.dashboard import RegistryDashboard
from cpn.frontend.server import handle_request
from cpn.rpnh.registry.schema_catalog import SchemaCatalog

from examples.tool_pipeline.multi_source import tools
from examples.tool_pipeline.multi_source.contracts import RULES
from examples.tool_pipeline.multi_source.host import registration
from examples.tool_pipeline.multi_source.build_module import declaration_dict
from examples.tool_pipeline.multi_source.run import ROOT, UPSTREAM, input_values, module, readback, run_pipeline, save_evidence
from examples.tool_pipeline.multi_source.tests.pipe_transport import PipeOwnerEventLoop


DATA = UPSTREAM / "multi-source-data-merger/environment/data"


def ref(name):
    return {"resource_id": "unit-only-resource-" + name, "resource_version_id": "unit-only-version-" + name}


def unit_record(user_id=1, name="Unit Person", email="a@unit.invalid", date="2024-01-01", **extra):
    return dict(user_id=user_id, name=name, email=email, created_at=date, **extra)


def unit_merge_inputs(rows):
    return {source: {"ref": ref(source + "-parsed"), "value": {
        "source_refs": {source: ref(source + "-input")},
        "data": {"records": tools.normalize_records(rows[source])}}}
        for source in reversed(RULES["priority"])}


def call(name, inputs):
    return asyncio.run(tools.TOOLS[name](inputs=inputs, rules=copy.deepcopy(RULES)))


def test_upstream_bytes_and_grader_are_unchanged():
    manifest = json.loads((UPSTREAM / "SOURCES.json").read_text())
    for record in manifest["files"]:
        raw = (UPSTREAM / record["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == record["sha256"]
        assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == record["git_blob_sha1"]
    raw = (DATA / "source_c/users.parquet").read_bytes()
    assert raw.startswith(b"PAR1") and raw.endswith(b"PAR1")
    assert len(raw) == 3639


def test_blob_round_trip():
    raw = (DATA / "source_c/users.parquet").read_bytes()
    assert tools.unpack_blob(tools.pack_blob(raw, "users.parquet", "application/vnd.apache.parquet")) == raw


@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("byte_length", 999), ("content", "not-base64!")])
def test_tampered_blob_rejected(field, value):
    blob = tools.pack_blob(b"data", "unit.bin", "application/octet-stream")
    blob[field] = value
    with pytest.raises(ValueError):
        tools.unpack_blob(blob)


def test_fixture_entry_rejects_forged_original(tmp_path):
    import shutil
    shutil.copytree(DATA, tmp_path / "data")
    (tmp_path / "data/source_a/users.json").write_text("[]")
    with pytest.raises(ValueError, match="pinned original"):
        input_values(tmp_path / "data")


@pytest.mark.parametrize("source", ["source_a", "source_b"])
def test_original_json_csv_parsed_from_bytes(source):
    values = input_values(DATA)
    result = call("read_" + source, {"source": {"ref": ref(source), "value": values[source]}})
    assert result.outcome == "complete"
    parsed = result.products["parsed"]
    assert parsed["source_refs"][source] == ref(source)
    assert len(parsed["data"]["records"]) == 2
    assert parsed["data"]["records"][0]["values"]["user_id"] == 101
    assert parsed["data"]["sha256"] == values[source]["blob"]["sha256"]


@pytest.mark.parametrize("value", [True, 1.25, "1.25", " 1", "01", "x", None])
def test_malformed_id_rejected(value):
    with pytest.raises(ValueError):
        tools.normalize_records([unit_record(user_id=value)])


@pytest.mark.parametrize("value", ["2024-02-30", "yesterday", None, ""])
def test_malformed_or_absent_date_rejected(value):
    with pytest.raises(ValueError):
        tools.normalize_records([unit_record(date=value)])


def test_duplicate_id_and_ambiguous_alias_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        tools.normalize_records([unit_record(), unit_record()])
    with pytest.raises(ValueError, match="ambiguous"):
        tools.normalize_records([unit_record(id=1)])


def test_optional_status_absent_and_null_are_allowed():
    for extra in ({}, {"status": None}, {"status": ""}):
        assert "status" not in tools.normalize_records([unit_record(**extra)])[0]["values"]


def test_unit_priority_multiple_conflicts_order_and_exact_locators():
    inputs = unit_merge_inputs({
        "source_a": [unit_record(name="Alpha", email="alpha@unit.invalid")],
        "source_b": [unit_record(name="Beta", email="beta@unit.invalid", status="active"),
                     unit_record(user_id=2, name="Beta Two")],
        "source_c": [unit_record(name="Gamma", email="gamma@unit.invalid", status="inactive"),
                     unit_record(user_id=2, name="Gamma Two"), unit_record(user_id=3)],
    })
    result = call("merge_users", inputs)
    data = result.products["merged"]["data"]
    assert [row["user_id"] for row in data["rows"]] == [1, 2, 3]
    assert data["rows"][0]["name"] == "Alpha"
    assert data["rows"][0]["status"] == "active"  # optional absent A falls back to B
    assert data["rows"][1]["name"] == "Beta Two"
    report = result.products["conflicts"]["data"]["report"]
    assert report["total_conflicts"] == len(report["conflicts"]) == 4
    selected = next(x for x in data["decisions"] if x["user_id"] == 1 and x["field"] == "email")
    assert selected["selected_source"] == "source_a"
    assert selected["origins"]["source_a"] == {"input_ref": ref("source_a-input"),
        "parsed_result_ref": ref("source_a-parsed"), "row_index": 0, "original_field": "email"}
    assert result.products["merged"]["parents"] == [x["ref"] for x in inputs.values()]


def test_incomplete_join_rejected():
    with pytest.raises(ValueError, match="three exact"):
        call("merge_users", {})


def test_declaration_compiles_without_custom_core_changes():
    assert json.loads((ROOT / "module.json").read_text()) == declaration_dict()
    compiled = compile_module(module(), registration())
    assert compiled is not None
    declaration = module().to_dict()
    merge = next(c for c in declaration["components"] if c["name"] == "merge_users")
    assert merge["operations"][0]["inputs"] == RULES["priority"]
    assert declaration["terminal"]["source"] == {"component": "finish", "port": "final"}


@pytest.fixture(scope="module")
def actual_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("multi-source-test-transport")
    evidence = run_pipeline(root / "run", data_dir=DATA, event_loop_factory=PipeOwnerEventLoop)
    save_evidence(evidence, root / "export")
    return root, evidence


def test_registry_fixture_run_and_honest_dependency_blocker(actual_run):
    root, evidence = actual_run
    assert evidence["actual_model_call_counts"] == [0, 0]
    assert evidence["transport"] == "explicit_test_transport"
    assert evidence["original_verifier"] == {"status": "not_run", "reward": None}
    values = input_values(DATA)
    for source, item in evidence["source_resources"].items():
        assert item["value"] == values[source]
    by_stage = {item["value"]["stage"]: item for item in evidence["resources"]}
    if "blocked" in by_stage:
        assert by_stage["blocked"]["value"]["data"]["step"] == "read_source_c"
        assert set(by_stage) == {"source_a_parsed", "source_b_parsed", "blocked"}
        assert evidence["terminal_evidence"] == [] and evidence["final"] is None
        assert not (root / "export/merged_users.parquet").exists()
        assert not (root / "export/conflicts.json").exists()
        assert len(evidence["firings"]) == 3
    else:
        assert evidence["terminal_evidence"][-1]["run_outcome"] == "complete"
        assert len(evidence["firings"]) == 7
        for filename in ("merged_users.parquet", "conflicts.json"):
            assert (root / "export" / filename).is_file()
    for source in ("source_a", "source_b"):
        item = by_stage[source + "_parsed"]
        assert item["value"]["parents"] == [evidence["source_resources"][source]["ref"]]
        provenance = item["metadata"]["reference_provenance"]["derived_from_refs"]
        assert [x["version_id"] for x in provenance] == [item["value"]["parents"][0]["resource_version_id"]]


def test_real_viewer_routes_read_only_and_saved_registry_readback(actual_run):
    root, evidence = actual_run
    before = readback(root / "run")["read_only_head_ordinal"]
    catalog = SchemaCatalog()
    registration().bind_schema_catalog(catalog)
    provider = RegistryDashboard(root / "run", catalog=catalog)
    for route in ("/", "/app.js", "/api/v1/net", "/api/v1/dashboard", "/api/v1/history"):
        response = handle_request(provider, "GET", route)
        assert response.status == 200, (route, response.body[:500])
    dashboard = provider.dashboard()
    assert dashboard["source"]["mode"] == "registry_current"
    assert readback(root / "run")["read_only_head_ordinal"] == before
    assert readback(root / "run")["final"] == evidence["final"]


def test_complete_original_three_source_artifacts_when_engine_available(actual_run):
    root, evidence = actual_run
    if evidence["final"] is None:
        pytest.skip("Original Parquet dependency is unavailable; full fixture run is NOT verified")
    import pandas as pd
    frame = pd.read_parquet(root / "export/merged_users.parquet")
    assert list(frame["user_id"]) == [101, 102, 103, 104]
    assert frame["user_id"].dtype.kind in "iu"
    report = json.loads((root / "export/conflicts.json").read_text())
    assert report["total_conflicts"] == len(report["conflicts"]) > 0
    stages = {item["value"]["stage"]: item for item in evidence["resources"]}
    merge = stages["merged"]
    assert set(x["resource_version_id"] for x in merge["value"]["parents"]) == {
        stages[source + "_parsed"]["ref"]["resource_version_id"] for source in RULES["priority"]}
    for decision in merge["value"]["data"]["decisions"]:
        conflict = report["conflicts"][decision["conflict_index"]]
        assert conflict["selected"] == decision["selected"]
        source = decision["selected_source"]
        origin = decision["origins"][source]
        assert origin["input_ref"] == evidence["source_resources"][source]["ref"]
        assert origin["parsed_result_ref"] == stages[source + "_parsed"]["ref"]
        original_row = stages[source + "_parsed"]["value"]["data"]["records"][origin["row_index"]]
        assert original_row["values"][decision["field"]] == decision["selected"]
    # This is an additional semantic check, not execution of the original grader.
    assert evidence["original_verifier"]["status"] == "not_run"
