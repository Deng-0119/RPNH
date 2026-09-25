"""User-generated provider profiles with exact, opaque model identities."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Mapping


SCHEMA_VERSION = "rpnh/provider_profiles/v2"
_PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9._-]*")


@dataclass(frozen=True, slots=True)
class ProviderModelRegistration:
    profile: str
    provider: str
    display_name: str
    model_condition: str

    @property
    def selection_id(self) -> str:
        """Stable frontend/user-config ID, deliberately separate from model text."""
        return self.profile

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
            or document.get("schema_version") != SCHEMA_VERSION
            or not isinstance(document.get("profiles"), list)):
        raise ValueError(f"provider profile manifest is not current: {path}")
    registrations: list[ProviderModelRegistration] = []
    for raw in document["profiles"]:
        if (not isinstance(raw, Mapping)
                or set(raw) != {
                    "profile", "provider", "display_name", "model_condition"}):
            raise ValueError(f"provider profile registration is invalid: {path}")
        profile = _text(raw.get("profile"), label="profile")
        if _PROFILE_ID.fullmatch(profile) is None:
            raise ValueError(f"profile identifier is invalid: {profile!r}")
        registrations.append(ProviderModelRegistration(
            profile=profile,
            provider=_text(raw.get("provider"), label="provider"),
            display_name=_text(
                raw.get("display_name"), label="provider display_name"),
            model_condition=_text(
                raw.get("model_condition"), label="model_condition"),
        ))
    profiles = [item.profile for item in registrations]
    if len(set(profiles)) != len(profiles):
        raise ValueError("provider profile registrations must be unique")
    return tuple(sorted(registrations, key=lambda item: item.profile))


__all__ = (
    "ProviderModelRegistration", "SCHEMA_VERSION",
    "discover_provider_models", "provider_manifest_path",
)
