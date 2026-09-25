from __future__ import annotations

import pickle
from dataclasses import FrozenInstanceError

import pytest

from cpn.rpnh.registration import Registration
from cpn.rpnh.registry import operation_repository
from cpn.rpnh.registry import operations
from cpn.rpnh.registry._operation import registration as operation_registration


_CONTRACTS = {
    "transport": "deterministic",
    "input_ports": [
        {
            "port_id": "request",
            "content_schema_id": "example/request/v1",
            "minimum": 1,
            "maximum": 1,
        },
    ],
    "output_ports": [],
}


def _executor() -> None:
    return None


def test_operations_facade_preserves_exact_objects_and_repository_seams() -> None:
    facade_names = (
        "OperationAuthorityError",
        "RegisteredOperationPortContract",
        "bind_operation_registration",
        "register_operation_contract",
        "registered_operation_contract",
        "registered_operation_executor",
        "registered_operation_ids",
        "registered_operation_port_contract",
        "registered_operation_transport",
        "_require_lexical_id",
    )
    for name in facade_names:
        assert getattr(operations, name) is getattr(operation_registration, name)
        assert getattr(operations, name).__module__ == operations.__name__

    assert (
        operation_repository._canonical_operation_fault_refs
        is operations._canonical_operation_fault_refs
    )
    assert (
        operation_repository._require_lexical_id
        is operations._require_lexical_id
    )
    repository = operation_repository.RegistryOperationAuthorityRepository
    assert callable(
        getattr(repository, "_RegistryOperationAuthorityRepository__reverify_input")
    )
    assert callable(
        getattr(
            repository,
            "_RegistryOperationAuthorityRepository__project_input_payload",
        )
    )


def test_facade_and_internal_api_share_one_registration_state(monkeypatch) -> None:
    monkeypatch.setattr(operation_registration, "_OPERATION_REGISTRATION", None)
    assert operations._OPERATION_REGISTRATION is None
    assert operations.registered_operation_ids() == ()

    inventory = Registration()
    operations.bind_operation_registration(inventory)
    operations.register_operation_contract(
        "example_executor",
        _executor,
        identity={"implementation_id": "test.example", "revision": "v1"},
        contracts=_CONTRACTS,
    )

    assert operation_registration._OPERATION_REGISTRATION is inventory
    assert operations._OPERATION_REGISTRATION is inventory
    assert operation_registration.registered_operation_ids() == (
        "example_executor",
    )
    assert operations.registered_operation_executor("example_executor") is _executor
    assert operations.registered_operation_transport(
        "example_executor"
    ) == "deterministic"
    assert operations.registered_operation_port_contract(
        "example_executor", "input"
    ) == (
        operations.RegisteredOperationPortContract(
            port_id="request",
            content_schema_id="example/request/v1",
            minimum=1,
            maximum=1,
        ),
    )

    replacement = Registration()
    operation_registration.bind_operation_registration(replacement)
    assert operations.registered_operation_ids() == ()


def test_registration_validation_order_and_dto_behavior_are_preserved(
    monkeypatch,
) -> None:
    monkeypatch.setattr(operation_registration, "_OPERATION_REGISTRATION", None)

    with pytest.raises(
        operations.OperationAuthorityError,
        match="^executor_key must be a nonempty registered key$",
    ):
        operations.register_operation_contract(
            "", _executor, identity={}, contracts={}
        )
    with pytest.raises(
        operations.OperationAuthorityError,
        match="^operation contract must declare transport and port ABI$",
    ):
        operations.register_operation_contract(
            "example_executor", _executor, identity={}, contracts={}
        )
    with pytest.raises(
        operations.OperationAuthorityError,
        match="^HOST operation registration is not bound$",
    ):
        operations.register_operation_contract(
            "example_executor", _executor, identity={}, contracts=_CONTRACTS
        )
    with pytest.raises(
        operations.OperationAuthorityError,
        match="^operation port direction is not closed$",
    ):
        operations.registered_operation_port_contract(
            "", "sideways"  # type: ignore[arg-type]
        )

    port = operations.RegisteredOperationPortContract(
        "request", "example/request/v1", 1, None
    )
    assert not hasattr(port, "__dict__")
    assert pickle.loads(pickle.dumps(port)) == port
    assert (
        pickle.loads(pickle.dumps(operations.OperationAuthorityError))
        is operations.OperationAuthorityError
    )
    with pytest.raises(FrozenInstanceError):
        port.minimum = 0  # type: ignore[misc]
