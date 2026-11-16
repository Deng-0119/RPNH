"""Frontend UX preferences and a read-only view of managed installation facts.

Manual v1 documents retain their original schema. Manual DSH profile selection
uses v2; managed v2 stores only the selected UI and installation identity. Its
bindings and profiles always come from the sibling installed.json record.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile


FRONTENDS = ("auto", "basic", "codex", "opencode", "dsh")
CONFIG_SCHEMA_VERSION = "rpnh/frontend_preferences/v1"
CONFIG_SCHEMA_VERSION_V2 = "rpnh/frontend_preferences/v2"
_BINDINGS = ("dsh_source", "codex_binary", "opencode_binary")
_PROFILES = ("codex_compatibility_profile", "opencode_compatibility_profile",
             "dsh_compatibility_profile")
_FRONTEND_ENTRIES = {"basic": "basic", "codex": "codex",
                     "opencode": "opencode", "dsh": "dsh-web"}


@dataclass(frozen=True, slots=True)
class FrontendPreferences:
    frontend: str = "auto"
    dsh_source: Path | None = None
    codex_binary: Path | None = None
    opencode_binary: Path | None = None
    codex_compatibility_profile: str | None = None
    opencode_compatibility_profile: str | None = None
    dsh_compatibility_profile: str | None = None
    installation_id: str | None = None


class ReconfigurationRequired(ValueError):
    """A requested frontend change needs a new installation generation.

    This reports the requested fields; it does not claim to have prepared an
    installation plan or changed the immutable installation record.
    """

    def __init__(self, installation_id: str, frontend: str,
                 changes: tuple[str, ...]) -> None:
        self.installation_id = installation_id
        self.frontend = frontend
        self.changes = changes
        super().__init__(
            f"Frontend change requires reconfiguration into a new installation "
            f"generation: {', '.join(changes)}; installation {installation_id} "
            "was not changed")


def preferences_path() -> Path:
    # Managed releases isolate host bindings without changing provider/model config.
    override = os.environ.get("RPNH_FRONTEND_CONFIG")
    if override:
        return Path(override).expanduser().absolute()
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


def _validated_profile(frontend: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{frontend}_compatibility_profile must be a nonempty profile name")
    if frontend == "codex":
        from cpn.frontend.codex_app_server import _compatibility_profile
        try:
            _compatibility_profile(value)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc
    elif frontend == "opencode":
        from cpn.frontend.opencode_protocol import OPENCODE_VERSION, get_opencode_profile
        profile = get_opencode_profile(
            certification_version=None if value == OPENCODE_VERSION else value)
        # Other packaged profiles are protocol certification fixtures only;
        # the ordinary launcher supports the default and the 2.2.6 candidate.
        if profile.certification_only and profile.version != "2.2.6":
            raise ValueError("The OpenCode frontend supports only the default profile or 2.2.6")
    elif frontend == "dsh":
        from cpn.rpnh.install_contract import validate_profile
        validate_profile("dsh-web", value)
    else:
        raise ValueError(f"Unsupported frontend profile: {frontend}")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON field")
        document[key] = value
    return document


def _read_document(destination: Path) -> dict | None:
    try:
        content = destination.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise ValueError(
            f"RPNH frontend preferences are unreadable: {destination}") from exc
    try:
        document = json.loads(content, object_pairs_hook=_unique_object)
    except ValueError as exc:
        raise ValueError(
            f"RPNH frontend preferences contain invalid JSON: {destination}") from exc
    if not isinstance(document, dict):
        raise ValueError(
            f"RPNH frontend preferences have an invalid schema: {destination}")
    return document


def _installed_record(destination: Path, *, required: bool = False) -> dict | None:
    """Dispatch v2 to its single strict loader; retain the legacy v1 route."""
    record_path = destination.parent / "installed.json"
    try:
        header = json.loads(record_path.read_text(encoding="utf-8"),
                            object_pairs_hook=_unique_object)
    except FileNotFoundError:
        if not required:
            return None
        raise ValueError("Managed frontend preferences require installed.json") from None
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("RPNH installed record is unreadable or invalid") from exc
    if (not required and isinstance(header, dict)
            and header.get("protocol") == "managed-side-by-side-v1"
            and "schema_version" not in header
            and "installation_id" not in header):
        return None
    from cpn.rpnh.install_contract import read_installed
    record = read_installed(destination.parent)
    if destination.name != "frontend.json":
        raise ValueError("Managed frontend preferences must use the installation's frontend.json")
    return record


def _preferences_destination(path: Path | None) -> Path:
    requested = (preferences_path() if path is None else path).expanduser().absolute()
    # Observe the original installation before following a preferences link.
    # Otherwise an I/frontend.json link to a manual document could silently
    # discard I and allow save() to mutate the link's unrelated target.
    record = _installed_record(requested)
    resolved = requested.resolve()
    if requested != resolved and (record is not None or _installed_record(resolved) is not None):
        raise ValueError("Managed frontend preferences cannot use symlinks or aliased paths")
    return resolved


def _manual_preferences(document: dict, destination: Path) -> FrontendPreferences:
    required = {"schema_version", "frontend"}
    schema = document.get("schema_version")
    allowed = required | set(_BINDINGS) | set(_PROFILES[:2])
    if schema == CONFIG_SCHEMA_VERSION_V2:
        allowed.add("dsh_compatibility_profile")
    if (schema not in (CONFIG_SCHEMA_VERSION, CONFIG_SCHEMA_VERSION_V2)
            or not required <= set(document) or not set(document) <= allowed):
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
        **{f"{name}_compatibility_profile": _validated_profile(
            name, document[f"{name}_compatibility_profile"])
           for name in ("codex", "opencode", "dsh")
           if f"{name}_compatibility_profile" in document},
    )


def _managed_preferences(document: dict, record: dict,
                         directory: Path) -> FrontendPreferences:
    from cpn.rpnh.install_contract import validate_frontend_view
    validate_frontend_view(
        document, expected_id=record["installation_id"],
        selected_entries=record["components"],
    )
    values: dict = {"installation_id": record["installation_id"]}
    for frontend in ("codex", "opencode", "dsh"):
        component = record["components"].get(_FRONTEND_ENTRIES[frontend])
        if component is None:
            continue
        launch = component["launch"]
        entry = Path(launch["path"])
        if launch["scope"] == "installation":
            entry = directory / entry
        binding_name = "dsh_source" if frontend == "dsh" else f"{frontend}_binary"
        values[binding_name] = entry.resolve()
        values[f"{frontend}_compatibility_profile"] = component["profile"]
    return FrontendPreferences(frontend=document["frontend"], **values)


def _read_state(destination: Path, *, initializing_id: str | None = None
                ) -> tuple[FrontendPreferences, str, dict | None]:
    document = _read_document(destination)
    managed_view = document is not None and "installation_id" in document
    record = _installed_record(
        destination, required=managed_view or initializing_id is not None)
    if record is not None:
        if initializing_id is not None and initializing_id != record["installation_id"]:
            raise ValueError("Frontend preferences installation identity does not match installed.json")
        if document is None and initializing_id is not None:
            document = {"schema_version": CONFIG_SCHEMA_VERSION_V2,
                        "installation_id": initializing_id, "frontend": "auto"}
        if document is None or not managed_view and initializing_id is None:
            raise ValueError("Managed v2 installation requires its bound v2 frontend preferences")
        if "installation_id" not in document:
            raise ValueError("Existing manual preferences cannot be rebound in place; initialize a new installation")
        return (_managed_preferences(document, record, destination.parent),
                CONFIG_SCHEMA_VERSION_V2, record)
    if document is None:
        return FrontendPreferences(), CONFIG_SCHEMA_VERSION, None
    return (_manual_preferences(document, destination), document["schema_version"], None)


def read_frontend_preferences(path: Path | None = None) -> FrontendPreferences:
    destination = _preferences_destination(path)
    preferences, _, _ = _read_state(destination)
    return preferences


def save_frontend_preferences(
        frontend: str, *, dsh_source: Path | None = None,
        codex_binary: Path | None = None, opencode_binary: Path | None = None,
        codex_compatibility_profile: str | None = None,
        opencode_compatibility_profile: str | None = None,
        dsh_compatibility_profile: str | None = None,
        installation_id: str | None = None,
        path: Path | None = None,
) -> FrontendPreferences:
    """Save UX selection atomically; managed bindings require a new generation.

    ``installation_id`` is required only to initialize a new installation's
    missing preferences. Managed callers retain the lifecycle lease while
    reading installation facts and writing this small UX document.
    """
    selected = _frontend(frontend)
    bindings = {"dsh_source": dsh_source, "codex_binary": codex_binary,
                "opencode_binary": opencode_binary}
    for name, value in bindings.items():
        if value is not None and not isinstance(value, Path):
            raise ValueError(f"{name} must be a Path or None")
    profiles = {"codex_compatibility_profile": codex_compatibility_profile,
                "opencode_compatibility_profile": opencode_compatibility_profile,
                "dsh_compatibility_profile": dsh_compatibility_profile}
    for name, value in profiles.items():
        if value is not None:
            _validated_profile(name.removesuffix("_compatibility_profile"), value)
    destination = _preferences_destination(path)
    existing, schema, record = _read_state(destination, initializing_id=installation_id)
    if record is not None:
        changes = []
        if selected != "auto" and _FRONTEND_ENTRIES[selected] not in record["components"]:
            changes.append("frontend")
        for name, value in bindings.items():
            if value is not None and _source(str(value), name) != getattr(existing, name):
                changes.append(name)
        for name, value in profiles.items():
            if value is not None and value != getattr(existing, name):
                changes.append(name)
        if changes:
            raise ReconfigurationRequired(record["installation_id"], selected, tuple(changes))
        document = {"schema_version": CONFIG_SCHEMA_VERSION_V2,
                    "frontend": selected, "installation_id": record["installation_id"]}
        preferences = _managed_preferences(document, record, destination.parent)
    else:
        preferences = FrontendPreferences(selected, **{
            name: getattr(existing, name) if value is None else _source(str(value), name)
            for name, value in bindings.items()
        }, **{name: getattr(existing, name) if value is None else value
              for name, value in profiles.items()})
        if preferences.dsh_compatibility_profile is not None:
            schema = CONFIG_SCHEMA_VERSION_V2
        document = {"schema_version": schema, "frontend": preferences.frontend}
        for name in (*bindings, *profiles):
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
    "CONFIG_SCHEMA_VERSION", "CONFIG_SCHEMA_VERSION_V2", "ReconfigurationRequired",
    "read_frontend_preferences", "save_frontend_preferences",
)
