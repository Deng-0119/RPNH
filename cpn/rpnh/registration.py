"""Explicit trusted HOST registration; executable Python never enters JSON.

Registering a callable grants execution authority to that Python implementation.
JSON clients can select its registered key, but cannot supply or import code.
Bind the Registry gateway and schema catalog at the execution-owner entry.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from jsonschema import Draft7Validator
from .registry.schema_catalog import PROTECTED_SCHEMA_REFS, _SCHEMA_ID


class RegistrationError(ValueError):
    """A HOST declaration is absent, inconsistent, or not pure data."""


_LOCATOR_FIELDS = frozenset({
    "callable", "import_locator", "import_path", "module_path", "entrypoint",
})
_KINDS = ("schema", "component", "executor", "tool", "analyzer")


def _data(value: Any, *, no_locators: bool = False) -> Any:
    """Copy JSON data without coercing Python objects into strings."""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise RegistrationError("declaration keys must be strings")
        if no_locators and _LOCATOR_FIELDS.intersection(value):
            raise RegistrationError("executable locators are not declaration data")
        value = {key: _data(item, no_locators=no_locators)
                 for key, item in value.items()}
    elif isinstance(value, (list, tuple)):
        value = [_data(item, no_locators=no_locators) for item in value]
    elif value is not None and type(value) not in (str, bool, int, float):
        raise RegistrationError("declarations contain JSON data only")
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (ValueError, TypeError) as exc:
        raise RegistrationError("declarations contain JSON data only") from exc


class Registration:
    """One HOST-owned inventory of schemas and trusted implementations.

    Keys are immutable within this inventory. Revisions use distinct keys and
    explicit identity data; no source inspection is performed. Gateway signature
    is ``gateway(kind, key, declaration)``. It receives data only and is responsible
    for exact Registry persistence, not for reconstructing callables.
    """

    def __init__(self) -> None:
        self._declarations: dict[str, dict[str, dict[str, Any]]] = {
            kind: {} for kind in _KINDS}
        self._callables: dict[str, dict[str, Callable[..., Any]]] = {
            kind: {} for kind in _KINDS if kind != "schema"}
        self._schema_catalog: Any = None
        self._gateway: Callable[[str, str, dict[str, Any]], Any] | None = None

    def bind_schema_catalog(self, catalog: Any) -> None:
        """Publish schemas through the actual Registry schema authority."""
        if not callable(getattr(catalog, "register_schema", None)):
            raise RegistrationError("schema catalog must expose register_schema")
        for key, declaration in self._declarations["schema"].items():
            catalog.register_schema(key, _data(declaration["schema"]))
        self._schema_catalog = catalog

    def bind_gateway(
        self, gateway: Callable[[str, str, dict[str, Any]], Any],
    ) -> None:
        """Replay existing declarations, then persist prospective registration."""
        if not callable(gateway):
            raise RegistrationError("Registry gateway must be a HOST callable")
        for kind in _KINDS:
            for key, declaration in self._declarations[kind].items():
                gateway(kind, key, _data(declaration))
        self._gateway = gateway

    def register_schema(self, schema_id: str, schema: Mapping[str, Any]) -> None:
        if isinstance(schema_id, str) and schema_id in PROTECTED_SCHEMA_REFS:
            raise RegistrationError("mechanical Registry/PN schemas are protected")
        if not isinstance(schema_id, str) or _SCHEMA_ID.fullmatch(schema_id) is None:
            raise RegistrationError("schema id is not canonical")
        if not isinstance(schema, Mapping):
            raise RegistrationError("schema must be a JSON schema object")
        document = _data(schema)
        if (document.get("$id") != schema_id
                or document.get("$schema") != "http://json-schema.org/draft-07/schema#"):
            raise RegistrationError("schema $id/dialect must match its registered identity")
        try:
            Draft7Validator.check_schema(document)
        except Exception as exc:
            raise RegistrationError("registered schema is invalid") from exc
        if schema_id in self._declarations["schema"]:
            raise RegistrationError("schema key is immutable; use a new revision id")
        declaration = {"kind": "schema", "key": schema_id, "schema": document}
        if self._schema_catalog is not None:
            self._schema_catalog.register_schema(schema_id, _data(document))
        self._publish("schema", schema_id, declaration)

    def _publish(self, kind: str, key: str, declaration: dict[str, Any]) -> None:
        if self._gateway is not None:
            self._gateway(kind, key, _data(declaration))
        self._declarations[kind][key] = declaration

    def _register(
        self, kind: str, key: str, implementation: Callable[..., Any], *,
        identity: Mapping[str, Any], contracts: Mapping[str, Any],
    ) -> None:
        if not isinstance(key, str) or not key:
            raise RegistrationError("registered key must be a nonempty HOST identity")
        if not callable(implementation):
            raise RegistrationError("implementation must be a trusted HOST callable")
        if key in self._declarations[kind]:
            raise RegistrationError("implementation key is immutable; use a new revision key")
        if not isinstance(identity, Mapping) or not identity:
            raise RegistrationError("explicit implementation identity data is required")
        if not isinstance(contracts, Mapping):
            raise RegistrationError("explicit contracts data is required")
        declaration = {
            "kind": kind, "key": key,
            "identity": _data(identity, no_locators=True),
            "contracts": _data(contracts, no_locators=True),
        }
        self._publish(kind, key, declaration)
        self._callables[kind][key] = implementation

    def register_component(self, key: str, lower: Callable[..., Any], *,
                           identity: Mapping[str, Any], contracts: Mapping[str, Any]) -> None:
        self._register("component", key, lower, identity=identity, contracts=contracts)

    def register_executor(self, key: str, executor: Callable[..., Any], *,
                          identity: Mapping[str, Any], contracts: Mapping[str, Any]) -> None:
        self._register("executor", key, executor, identity=identity, contracts=contracts)

    def register_tool(self, key: str, tool: Callable[..., Any], *,
                      identity: Mapping[str, Any], contracts: Mapping[str, Any]) -> None:
        self._register("tool", key, tool, identity=identity, contracts=contracts)

    def register_analyzer(self, key: str, analyzer: Callable[..., Any], *,
                          identity: Mapping[str, Any], contracts: Mapping[str, Any]) -> None:
        self._register("analyzer", key, analyzer, identity=identity, contracts=contracts)

    def declaration(self, kind: str, key: str) -> dict[str, Any]:
        if kind == "schema" and key in PROTECTED_SCHEMA_REFS:
            # Read the sole mechanical schema; this is not registration or an
            # override, and imports no optional component/workflow library.
            from .registry.schema_catalog import SchemaCatalog
            document = json.loads(SchemaCatalog._mechanical_schema_path(key).read_text(encoding="utf-8"))
            return {"kind": "schema", "key": key, "schema": document}
        try:
            return _data(self._declarations[kind][key])
        except KeyError as exc:
            raise RegistrationError(f"unregistered {kind} key: {key!r}") from exc

    def declarations(self, kind: str | None = None) -> tuple[dict[str, Any], ...]:
        kinds = _KINDS if kind is None else (kind,)
        if any(item not in _KINDS for item in kinds):
            raise RegistrationError("unknown registration kind")
        return tuple(self.declaration(item, key) for item in kinds
                     for key in sorted(self._declarations[item]))

    def resolve(self, kind: str, key: str) -> Callable[..., Any]:
        try:
            return self._callables[kind][key]
        except KeyError as exc:
            raise RegistrationError(f"unregistered executable {kind} key: {key!r}") from exc


__all__ = ("Registration", "RegistrationError")
