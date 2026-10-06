"""Pure operation spec data retains the real producer's exact wire contract."""
from dataclasses import replace

import pytest

from cpn.rpnh.registry import operations
from cpn.rpnh.registry.operations import OperationPortAuthority, OperationInputProjection, OperationFieldProjection
from cpn.rpnh.registry.publication import _version_from_payload, _content_schema_source_from_payload
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import _operation_port_payload, _operation_spec_metadata, StrictContractError
from test_module_graph_projection import graph_arguments


def typed_port(core, payload):
    schema = _version_from_payload(payload["schema_ref"])
    content = _content_schema_source_from_payload(payload["content_schema_ref"])
    authority = core.verify_registered_content_schema_ref(content, schema_document_ref=schema)
    return OperationPortAuthority(payload["port_id"], payload["place"], schema, authority,
        payload["cardinality"]["minimum"], payload["cardinality"]["maximum"],
        tuple(OperationInputProjection(item["producer_content_schema_ref"],
            tuple(OperationFieldProjection(**field) for field in item["field_projection"]))
            for item in payload.get("input_projections", ())),
        None if payload["lease_identity_ref"] is None else _version_from_payload(payload["lease_identity_ref"]))


@pytest.mark.parametrize("mode", ["basic", "hosts"])
def test_pure_spec_metadata_matches_real_publication_without_registration_lookup(tmp_path, monkeypatch, mode):
    core, compiled, registration, arguments = graph_arguments(tmp_path, mode)
    inputs = []
    for name, ref in arguments["operation_refs"].items():
        prepared = core.get_version(ref.version_id)
        expected = dict(prepared.metadata)
        ports_in = tuple(typed_port(core, p) for p in expected["input_ports"])
        ports_out = tuple(typed_port(core, p) for p in expected["output_ports"])
        declaration = registration.declaration("executor", expected["executor_key"])
        inputs.append((ref, expected, ports_in, ports_out, declaration, core.object_store.read_registered(prepared)))
    def forbidden(*args, **kwargs):
        raise AssertionError("pure spec metadata cannot consult HOST or Registry")
    monkeypatch.setattr(operations, "registered_operation_contract", forbidden)
    monkeypatch.setattr(operations, "registered_operation_transport", forbidden)
    monkeypatch.setattr(operations, "registered_operation_executor", forbidden)
    monkeypatch.setattr(registration, "resolve", forbidden)
    monkeypatch.setattr(core, "begin", forbidden)
    monkeypatch.setattr(core.event_store, "connect", forbidden)
    for ref, expected, ports_in, ports_out, declaration, payload in inputs:
        actual = _operation_spec_metadata(spec_ref=ref, operation_id=expected["operation_id"],
            executor_key=expected["executor_key"], declaration=declaration, transport=expected["transport"],
            llm_prompt_port_id=expected["llm_prompt_port_id"],
            input_payloads=[_operation_port_payload(p, input_port=True) for p in ports_in],
            output_payloads=[_operation_port_payload(p, input_port=False) for p in ports_out],
            allowed_tool_ids=expected["allowed_tool_ids"])
        assert canonical_json(actual) == canonical_json(expected) == payload


def test_pure_port_projection_preserves_typed_projection_and_output_restrictions(tmp_path):
    core, _, _, arguments = graph_arguments(tmp_path, "inputs")
    ref = next(iter(arguments["operation_refs"].values()))
    port = typed_port(core, core.get_version(ref.version_id).metadata["input_ports"][0])
    projected = replace(port, input_projections=(OperationInputProjection("application/other_input/v1",
        (OperationFieldProjection("/source", "/target"),)),))
    payload = _operation_port_payload(projected, input_port=True)
    assert payload["input_projections"] == [{"producer_content_schema_ref": "application/other_input/v1",
        "field_projection": [{"producer_path": "/source", "consumer_path": "/target"}]}]
    with pytest.raises(StrictContractError, match="output port cannot publish input projections"):
        _operation_port_payload(projected, input_port=False)
    lease = arguments["owner_input_resources"][0].as_version_ref()
    with pytest.raises(StrictContractError, match="output port cannot declare a lease identity"):
        _operation_port_payload(replace(port, lease_identity_ref=lease), input_port=False)
