"""User-generated provider profiles with exact, opaque model identities."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Mapping


SCHEMA_VERSION = "rpnh/provider_profiles/v3"
LEGACY_SCHEMA_VERSION = "rpnh/provider_profiles/v2"
_PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9._-]*")


@dataclass(frozen=True, slots=True)
class ProviderModelRegistration:
    profile: str
    provider: str
    display_name: str
    model_condition: str
    logical_selection_id: str | None = None
    reasoning_effort: str | None = None
    supported_reasoning_efforts: tuple[str, ...] = ()
    default_reasoning_effort: str | None = None

    @property
    def selection_id(self) -> str:
        """Stable frontend/user-config ID, deliberately separate from model text."""
        return self.logical_selection_id or self.profile

    @property
    def selectable(self) -> bool:
        return True


def provider_manifest_path(execution_directory: Path) -> Path:
    return execution_directory.resolve().parent / "profiles.json"


def _text(value: object, *, label: str) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127
                   for character in value)):
        raise ValueError(f"{label} must be nonempty trimmed text")
    return value


def _optional_identifier(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    text = _text(value, label=label)
    if _PROFILE_ID.fullmatch(text) is None:
        raise ValueError(f"{label} identifier is invalid: {text!r}")
    return text


def discover_provider_models(
        execution_directory: Path,
) -> tuple[ProviderModelRegistration, ...]:
    path = provider_manifest_path(execution_directory)
    if not path.is_file():
        return ()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"provider profile manifest is unavailable or invalid: {path}") from exc
    if (not isinstance(document, Mapping)
            or set(document) != {"schema_version", "profiles"}
            or document.get("schema_version") not in {
                SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}
            or not isinstance(document.get("profiles"), list)):
        raise ValueError(f"provider profile manifest is not current: {path}")
    current = document["schema_version"] == SCHEMA_VERSION
    registrations: list[ProviderModelRegistration] = []
    for raw in document["profiles"]:
        expected_fields = {
            "profile", "provider", "display_name", "model_condition"}
        if current:
            expected_fields |= {
                "logical_selection_id", "reasoning_effort",
                "supported_reasoning_efforts", "default_reasoning_effort"}
        if not isinstance(raw, Mapping) or set(raw) != expected_fields:
            raise ValueError(f"provider profile registration is invalid: {path}")
        profile = _text(raw.get("profile"), label="profile")
        if _PROFILE_ID.fullmatch(profile) is None:
            raise ValueError(f"profile identifier is invalid: {profile!r}")
        logical_selection_id = None
        reasoning_effort = None
        supported_reasoning_efforts: tuple[str, ...] = ()
        default_reasoning_effort = None
        if current:
            logical_selection_id = _optional_identifier(
                raw.get("logical_selection_id"),
                label="logical_selection_id")
            if logical_selection_id is None:
                raise ValueError(
                    "logical_selection_id must be a safe identifier")
            reasoning_effort = _optional_identifier(
                raw.get("reasoning_effort"), label="reasoning_effort")
            raw_supported = raw.get("supported_reasoning_efforts")
            if not isinstance(raw_supported, list):
                raise ValueError(
                    "supported_reasoning_efforts must be an array")
            supported_reasoning_efforts = tuple(
                _optional_identifier(item, label="supported_reasoning_effort")
                for item in raw_supported)
            if (any(item is None for item in supported_reasoning_efforts)
                    or len(set(supported_reasoning_efforts))
                    != len(supported_reasoning_efforts)):
                raise ValueError(
                    "supported_reasoning_efforts must contain unique safe identifiers")
            default_reasoning_effort = _optional_identifier(
                raw.get("default_reasoning_effort"),
                label="default_reasoning_effort")
            if bool(supported_reasoning_efforts) != (
                    reasoning_effort is not None
                    and default_reasoning_effort is not None):
                raise ValueError(
                    "reasoning effort manifest fields are inconsistent")
            if supported_reasoning_efforts and (
                    reasoning_effort not in supported_reasoning_efforts
                    or default_reasoning_effort
                    not in supported_reasoning_efforts):
                raise ValueError(
                    "reasoning effort manifest selection/default is unsupported")
        registrations.append(ProviderModelRegistration(
            profile=profile,
            provider=_text(raw.get("provider"), label="provider"),
            display_name=_text(
                raw.get("display_name"), label="provider display_name"),
            model_condition=_text(
                raw.get("model_condition"), label="model_condition"),
            logical_selection_id=logical_selection_id,
            reasoning_effort=reasoning_effort,
            supported_reasoning_efforts=tuple(
                item for item in supported_reasoning_efforts
                if item is not None),
            default_reasoning_effort=default_reasoning_effort,
        ))
    profiles = [item.profile for item in registrations]
    if len(set(profiles)) != len(profiles):
        raise ValueError("provider profile registrations must be unique")
    return tuple(sorted(registrations, key=lambda item: item.profile))


__all__ = (
    "LEGACY_SCHEMA_VERSION", "ProviderModelRegistration", "SCHEMA_VERSION",
    "discover_provider_models", "provider_manifest_path",
)
