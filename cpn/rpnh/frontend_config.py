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


def preferences_path() -> Path:
    from cpn.rpnh.user_config import config_path
    provider_config = config_path()
    return provider_config.with_name(provider_config.name + ".frontend.json")


def _frontend(value: object) -> str:
    if not isinstance(value, str) or value not in FRONTENDS:
        raise ValueError(
            f"frontend must be one of: {', '.join(FRONTENDS)}")
    return value


def _source(value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("dsh_source must be a nonempty path string")
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
            or set(document) not in (required, required | {"dsh_source"})
            or document["schema_version"] != CONFIG_SCHEMA_VERSION):
        raise ValueError(
            f"RPNH frontend preferences have an invalid schema: {destination}")
    return FrontendPreferences(
        frontend=_frontend(document["frontend"]),
        dsh_source=(_source(document["dsh_source"])
                    if "dsh_source" in document else None),
    )


def save_frontend_preferences(
        frontend: str, *, dsh_source: Path | None = None,
        path: Path | None = None,
) -> FrontendPreferences:
    selected = _frontend(frontend)
    if dsh_source is not None and not isinstance(dsh_source, Path):
        raise ValueError("dsh_source must be a Path or None")
    source = _source(str(dsh_source)) if dsh_source is not None else None
    destination = (preferences_path() if path is None else path).expanduser().resolve()
    existing = read_frontend_preferences(destination)
    preferences = FrontendPreferences(
        selected, existing.dsh_source if dsh_source is None else source)
    document = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "frontend": preferences.frontend,
    }
    if preferences.dsh_source is not None:
        document["dsh_source"] = str(preferences.dsh_source)
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
