"""Persistent frontend preferences, independent of provider/model selection."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile


FRONTENDS = ("auto", "basic", "codex", "opencode", "dsh")
CONFIG_SCHEMA_VERSION = "rpnh/frontend_preferences/v1"


@dataclass(frozen=True, slots=True)
class FrontendPreferences:
    frontend: str = "auto"
    dsh_source: Path | None = None
    codex_binary: Path | None = None
    opencode_binary: Path | None = None


def preferences_path() -> Path:
    from cpn.rpnh.user_config import config_path
    provider_config = config_path()
    return provider_config.with_name(provider_config.name + ".frontend.json")


def _frontend(value: object) -> str:
    if not isinstance(value, str) or value not in FRONTENDS:
        raise ValueError(
            f"frontend must be one of: {', '.join(FRONTENDS)}")
    return value


def _source(value: object, name: str = "dsh_source") -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty path string")
    return Path(value).expanduser().resolve()


def read_frontend_preferences(path: Path | None = None) -> FrontendPreferences:
    destination = (preferences_path() if path is None else path).expanduser().resolve()
    try:
        content = destination.read_text(encoding="utf-8")
    except FileNotFoundError:
        return FrontendPreferences()
    except (OSError, UnicodeError) as exc:
        raise ValueError(
            f"RPNH frontend preferences are unreadable: {destination}") from exc
    try:
        document = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"RPNH frontend preferences contain invalid JSON: {destination}") from exc
    required = {"schema_version", "frontend"}
    if (not isinstance(document, dict)
            or not required <= set(document)
            or not set(document) <= required | {"dsh_source", "codex_binary", "opencode_binary"}
            or document["schema_version"] != CONFIG_SCHEMA_VERSION):
        raise ValueError(
            f"RPNH frontend preferences have an invalid schema: {destination}")
    return FrontendPreferences(
        frontend=_frontend(document["frontend"]),
        dsh_source=(_source(document["dsh_source"])
                    if "dsh_source" in document else None),
        codex_binary=(_source(document["codex_binary"], "codex_binary")
                      if "codex_binary" in document else None),
        opencode_binary=(_source(document["opencode_binary"], "opencode_binary")
                         if "opencode_binary" in document else None),
    )


def save_frontend_preferences(
        frontend: str, *, dsh_source: Path | None = None,
        codex_binary: Path | None = None, opencode_binary: Path | None = None,
        path: Path | None = None,
) -> FrontendPreferences:
    selected = _frontend(frontend)
    bindings = {"dsh_source": dsh_source, "codex_binary": codex_binary,
                "opencode_binary": opencode_binary}
    for name, value in bindings.items():
        if value is not None and not isinstance(value, Path):
            raise ValueError(f"{name} must be a Path or None")
    destination = (preferences_path() if path is None else path).expanduser().resolve()
    existing = read_frontend_preferences(destination)
    preferences = FrontendPreferences(selected, **{
        name: getattr(existing, name) if value is None else _source(str(value), name)
        for name, value in bindings.items()
    })
    document = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "frontend": preferences.frontend,
    }
    for name in bindings:
        value = getattr(preferences, name)
        if value is not None:
            document[name] = str(value)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=destination.parent,
                prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return preferences


__all__ = (
    "FRONTENDS", "FrontendPreferences", "preferences_path",
    "read_frontend_preferences", "save_frontend_preferences",
)
