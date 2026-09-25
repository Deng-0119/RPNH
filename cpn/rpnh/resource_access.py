"""Pure-data registered read eligibility, not resource or Registry authority.

The Kernel must first validate the exact immutable resource, invocation context,
schema and reference graph, and compare the persisted binding policy with its
immutable registered operation contract. Only then may it call ``matches``.
No writer, policy callable, parser hook or permission callback is accepted here.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from .registry.models import VersionRef
from .registry.publication import _ref_payload, _resource_from_payload


class ResourceReadContext(Protocol):
    task_ref: VersionRef
    invocation_ref: VersionRef
    operation_binding_ref: VersionRef
    origin: str


def _freeze(value: Any) -> Any:
    """Copy only JSON data; registered arrays have no executable semantics."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and isfinite(value):
        return value
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("read contract object keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) in (list, tuple):
        return tuple(_freeze(item) for item in value)
    raise ValueError("read contract accepts only JSON data")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _equals(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    return all(key in actual and actual[key] == _thaw(value)
               for key, value in expected.items())


@dataclass(frozen=True, slots=True)
class ResourceReadContract:
    """One closed alternative in a registered operation's read-policy tuple.

    App selectors/values are opaque equality data. Exact task/context/producer
    and single-source reference checks are fixed mechanical Kernel semantics.
    Empty origin tuples impose no origin selector; metadata_only is always exact.
    """

    metadata_only: bool
    context_origins: tuple[str, ...] = ()
    origin_kinds: tuple[str, ...] = ()
    metadata_equals: Mapping[str, Any] = field(default_factory=dict)
    descriptor_equals: Mapping[str, Any] = field(default_factory=dict)
    extension_key: str | None = None
    extension_equals: Mapping[str, Any] = field(default_factory=dict)
    require_producer_invocation: bool = False
    require_provenance_binding: bool = False
    single_derived_source_key: str | None = None

    def __post_init__(self) -> None:
        for name in ("metadata_only", "require_producer_invocation",
                     "require_provenance_binding"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        for name in ("context_origins", "origin_kinds"):
            value = getattr(self, name)
            if (type(value) not in (tuple, list)
                    or any(type(item) is not str or not item for item in value)):
                raise ValueError(f"{name} must contain strings")
            object.__setattr__(self, name, tuple(value))
        for name in ("metadata_equals", "descriptor_equals", "extension_equals"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise ValueError(f"{name} must be an object")
            object.__setattr__(self, name, _freeze(value))
        for name in ("extension_key", "single_derived_source_key"):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or not value):
                raise ValueError(f"{name} must be a nonempty string or null")
        if self.extension_key is None and (
                self.extension_equals or self.single_derived_source_key is not None):
            raise ValueError("extension checks require an extension key")

    def to_dict(self) -> dict[str, Any]:
        return {item.name: _thaw(getattr(self, item.name)) for item in fields(self)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ResourceReadContract:
        if not isinstance(value, Mapping) or set(value) != {
                item.name for item in fields(cls)}:
            raise ValueError("read contract requires exactly its declared fields")
        return cls(**dict(value))

    def matches(self, metadata: Mapping[str, Any], context: ResourceReadContext,
                *, metadata_only: bool) -> bool:
        """Compare EXACT Kernel-validated metadata/context, never validate authority."""
        if (type(metadata_only) is not bool or metadata_only != self.metadata_only
                or metadata.get("task_ref") != _ref_payload(context.task_ref)
                or (self.context_origins and context.origin not in self.context_origins)
                or not _equals(metadata, self.metadata_equals)):
            return False
        origin = metadata.get("origin")
        if self.origin_kinds and (not isinstance(origin, Mapping)
                                 or origin.get("kind") not in self.origin_kinds):
            return False
        if self.descriptor_equals:
            descriptors = metadata.get("descriptors")
            if not isinstance(descriptors, Mapping) or not _equals(
                    descriptors, self.descriptor_equals):
                return False
        invocation = _ref_payload(context.invocation_ref)
        binding = _ref_payload(context.operation_binding_ref)
        provenance = metadata.get("reference_provenance")
        if self.require_producer_invocation and metadata.get("producer_ref") != invocation:
            return False
        if self.require_provenance_binding and (
                not isinstance(provenance, Mapping)
                or provenance.get("operation_binding_ref") != binding):
            return False
        if self.extension_key is not None:
            extensions = metadata.get("extensions")
            extension = (extensions.get(self.extension_key)
                         if isinstance(extensions, Mapping) else None)
            if (not isinstance(extension, Mapping)
                    or extension.get("invocation_ref") != invocation
                    or extension.get("operation_binding_ref") != binding
                    or not _equals(extension, self.extension_equals)):
                return False
            if self.single_derived_source_key is not None:
                source = extension.get(self.single_derived_source_key)
                if not isinstance(source, Mapping) or not isinstance(provenance, Mapping):
                    return False
                # Ref decoding/conversion stays mechanical, never app-defined.
                expected_source = _ref_payload(_resource_from_payload(source).as_version_ref())
                if provenance.get("derived_from_refs") != [expected_source]:
                    return False
        return True


__all__ = ("ResourceReadContract", "ResourceReadContext")
