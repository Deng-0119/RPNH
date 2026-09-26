"""RPNH-owned provider/exact-model selection and first-run onboarding."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Callable, Mapping

from cpn.llm_adapters import load_llm_execution_selection
from cpn.rpnh.provider_catalog import (
    ProviderModelRegistration,
    discover_provider_models,
)


CONFIG_SCHEMA_VERSION = "rpnh/cli_config/v3"


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    name: str
    selection_id: str
    provider: str
    provider_display_name: str
    path: Path
    model_condition: str
    adapter_kind: str
    required_environment: tuple[str, ...]
    recovery: Mapping[str, object] | None
    context_window_tokens: int | None
    context_compaction_retained_tokens: int | None
    runtime: Mapping[str, object]
    @property
    def selectable(self) -> bool:
        return True

    def as_public_dict(
            self, *, selected: bool = False,
            environ: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        current = os.environ if environ is None else environ
        missing = [
            name for name in self.required_environment
            if not current.get(name)
        ]
        result: dict[str, object] = {
            "profile": self.name,
            "selection_id": self.selection_id,
            "provider": self.provider,
            "provider_display_name": self.provider_display_name,
            "model_condition": self.model_condition,
            "adapter_kind": self.adapter_kind,
            "execution_config_path": str(self.path),
            "selectable": True,
            "selected": selected,
            "credential_environment": list(self.required_environment),
            "missing_credential_environment": missing,
            "ready": not missing,
        }
        if self.recovery is not None:
            result["recovery"] = dict(self.recovery)
        if self.context_window_tokens is not None:
            result["context_window_tokens"] = self.context_window_tokens
        if self.context_compaction_retained_tokens is not None:
            result["context_compaction_retained_tokens"] = (
                self.context_compaction_retained_tokens)
        result["runtime"] = dict(self.runtime)
        return result


def config_path() -> Path:
    override = os.environ.get("RPNH_CONFIG")
    return (Path(override).expanduser() if override else
            Path.home() / ".config" / "rpnh" / "config.json")


def profile_directory() -> Path:
    override = os.environ.get("RPNH_PROFILE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return config_path().expanduser().resolve().parent / "profiles" / "execution"


def _adapter_document(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"adapter configuration is unavailable or invalid: {path}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"adapter configuration must be an object: {path}")
    return value


def _required_environment(adapter: Mapping[str, object]) -> tuple[str, ...]:
    if adapter.get("adapter_kind") != "external_provider":
        return ()
    routes = adapter.get("routes")
    if not isinstance(routes, list) or not routes:
        raise ValueError("external provider profile has no routes")
    names: set[str] = set()
    for route in routes:
        if not isinstance(route, Mapping):
            raise ValueError("external provider route is malformed")
        credential = route.get("credential")
        if credential is None:
            continue
        if not isinstance(credential, Mapping):
            raise ValueError("external provider credential declaration is malformed")
        name = credential.get("environment")
        if not isinstance(name, str) or not name:
            raise ValueError("external provider credential environment is missing")
        names.add(name)
    return tuple(sorted(names))


def _inferred_provider(
        adapter: Mapping[str, object], *, fallback: str,
) -> tuple[str, str]:
    if adapter.get("adapter_kind") != "external_provider":
        return "local-process", "Local process"
    routes = adapter.get("routes")
    providers = {
        route.get("provider") for route in routes
        if isinstance(route, Mapping) and isinstance(route.get("provider"), str)
    } if isinstance(routes, list) else set()
    provider = next(iter(providers)) if len(providers) == 1 else fallback
    return provider, provider


def _validate_registered_adapter(
        adapter: Mapping[str, object], registration: ProviderModelRegistration,
) -> None:
    if adapter.get("adapter_kind") != "external_provider":
        return
    routes = adapter.get("routes")
    if not isinstance(routes, list) or len(routes) != 1:
        raise ValueError(
            "selectable external provider profiles require exactly one route")
    route = routes[0]
    if (not isinstance(route, Mapping)
            or route.get("provider") != registration.provider
            or route.get("outbound_model") != registration.model_condition):
        raise ValueError(
            "provider registration differs from its exact adapter route")


def _load_profile(
        path: Path,
        registration: ProviderModelRegistration | None = None,
) -> ExecutionProfile:
    selected = path.expanduser().resolve()
    selection = load_llm_execution_selection(selected)
    registry_policy = selection.as_registry_policy()
    adapter = _adapter_document(selection.adapter_config_path)
    name = selected.stem
    model = selection.input_target.model_condition
    if registration is None:
        provider, display_name = _inferred_provider(adapter, fallback=name)
        selection_id = f"{provider}/{model}"
    else:
        if registration.profile != name or registration.model_condition != model:
            raise ValueError(
                "provider registration differs from its execution profile")
        _validate_registered_adapter(adapter, registration)
        provider = registration.provider
        display_name = registration.display_name
        selection_id = registration.selection_id
    adapter_profile = registry_policy.get("adapter_profile")
    raw_recovery = (
        adapter_profile.get("recovery")
        if isinstance(adapter_profile, Mapping) else None)
    recovery = (
        dict(raw_recovery) if isinstance(raw_recovery, Mapping) else None)
    return ExecutionProfile(
        name=name,
        selection_id=selection_id,
        provider=provider,
        provider_display_name=display_name,
        path=selected,
        model_condition=model,
        adapter_kind=selection.adapter_kind,
        required_environment=_required_environment(adapter),
        recovery=recovery,
        context_window_tokens=selection.input_target.context_window_tokens,
        context_compaction_retained_tokens=(
            selection.input_target.context_compaction_retained_tokens),
        runtime=selection.runtime_policy.as_document(),
    )


def load_profile(path: Path) -> ExecutionProfile:
    """Load an explicit profile without requiring installed-catalog membership."""
    return _load_profile(path)


def discover_profiles(directory: Path | None = None) -> tuple[ExecutionProfile, ...]:
    root = profile_directory() if directory is None else directory.resolve()
    if not root.is_dir():
        return ()
    paths = tuple(sorted(root.glob("*.json")))
    registrations = discover_provider_models(root)
    if not paths and not registrations:
        return ()
    if not registrations:
        raise ValueError(
            "execution profiles require a generated profile manifest")
    by_name = {registration.profile: registration
               for registration in registrations}
    if {path.stem for path in paths} != set(by_name):
        raise ValueError(
            "provider catalog and installed execution profiles differ")
    profiles = tuple(
        _load_profile(path, by_name[path.stem]) for path in paths)
    if len({profile.name for profile in profiles}) != len(profiles):
        raise ValueError("RPNH profile names must be unique")
    return profiles


def profile_for_path(
        selected: Path, directory: Path | None = None,
) -> ExecutionProfile:
    resolved = selected.expanduser().resolve()
    for profile in discover_profiles(directory):
        if profile.path == resolved:
            return profile
    return load_profile(resolved)


def _selection_by_identity(
        profile_id: str, provider: str, model_condition: str,
) -> ExecutionProfile:
    matches = [
        profile for profile in discover_profiles()
        if (profile.selection_id == profile_id
            and profile.provider == provider
            and profile.model_condition == model_condition)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"saved RPNH profile is unavailable: {profile_id}")
    return matches[0]


def read_selected_path(path: Path | None = None) -> Path | None:
    selected_config = config_path() if path is None else path
    if not selected_config.is_file():
        return None
    try:
        document = json.loads(selected_config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"RPNH user config is invalid: {selected_config}") from exc
    if not isinstance(document, Mapping):
        raise ValueError(f"RPNH user config is not current: {selected_config}")
    if (document.get("schema_version") == CONFIG_SCHEMA_VERSION
            and set(document) == {
                "schema_version", "profile", "provider", "model_condition"}
            and all(isinstance(document.get(key), str) and document[key]
                    for key in ("profile", "provider", "model_condition"))):
        return _selection_by_identity(
            document["profile"], document["provider"],
            document["model_condition"]).path
    if (document.get("schema_version") == CONFIG_SCHEMA_VERSION
            and set(document) == {"schema_version", "execution_config_path"}
            and isinstance(document.get("execution_config_path"), str)
            and document["execution_config_path"]):
        return Path(document["execution_config_path"]).expanduser().resolve()
    raise ValueError(f"RPNH user config is not current: {selected_config}")


def save_selected_path(
        selected: Path, path: Path | None = None,
        directory: Path | None = None,
) -> Path:
    selected = selected.expanduser().resolve()
    if not selected.is_file():
        raise ValueError(f"execution selection does not exist: {selected}")
    load_profile(selected)
    destination = config_path() if path is None else path
    destination.parent.mkdir(parents=True, exist_ok=True)
    bundled = [
        profile for profile in discover_profiles(directory)
        if profile.path == selected
    ]
    if directory is None and len(bundled) == 1:
        selection_document = {
            "profile": bundled[0].selection_id,
            "provider": bundled[0].provider,
            "model_condition": bundled[0].model_condition,
        }
    else:
        selection_document = {"execution_config_path": str(selected)}
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA_VERSION,
        **selection_document,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    return selected


def select_profile(
        provider_or_profile: str,
        model_condition: str | None = None,
        directory: Path | None = None,
) -> ExecutionProfile:
    profiles = discover_profiles(directory)
    if model_condition is None:
        matches = [
            profile for profile in profiles
            if provider_or_profile == profile.name
        ]
    else:
        matches = [
            profile for profile in profiles
            if profile.provider == provider_or_profile
            and profile.model_condition == model_condition
        ]
    if len(matches) != 1:
        choices = ", ".join(
            profile.selection_id for profile in profiles
        ) or "(none)"
        raise ValueError(
            f"unknown RPNH provider/model selection; choose one of: {choices}")
    profile = matches[0]
    save_selected_path(profile.path, directory=directory)
    return profile


def interactive_setup(
        *, input_fn: Callable[[str], str] | None = None,
        output_fn: Callable[[str], None] | None = None,
        directory: Path | None = None,
) -> ExecutionProfile:
    """Create the first model or select an existing user profile."""
    from cpn.rpnh.onboarding import setup_profile
    return setup_profile(input_fn=input_fn, output_fn=output_fn, directory=directory)


def resolve_execution_path(
        explicit: Path | None, *, save_default: bool,
        allow_interactive_setup: bool = False,
) -> Path:
    if save_default and explicit is None:
        raise ValueError("--save-default requires --execution")
    selected = explicit
    if selected is None:
        environment = os.environ.get("RPNH_EXECUTION_CONFIG")
        if environment:
            selected = Path(environment).expanduser()
    if selected is None:
        selected = read_selected_path()
    if selected is None and allow_interactive_setup:
        selected = interactive_setup().path
    if selected is None:
        raise ValueError(
            "no model configured; run `rpnh init` in a terminal. "
            "For scripted setup, use `rpnh config init`, edit the catalog, "
            "then run `rpnh config build` and `rpnh config use PROFILE`.")
    selected = selected.expanduser().resolve()
    if not selected.is_file():
        raise ValueError(f"execution selection does not exist: {selected}")
    profile = profile_for_path(selected)
    if save_default:
        if explicit is None:
            raise ValueError("--save-default requires --execution")
        save_selected_path(selected)
    return selected


def missing_credentials(
        execution_path: Path,
        environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    profile = profile_for_path(execution_path)
    current = os.environ if environ is None else environ
    return tuple(
        name for name in profile.required_environment if not current.get(name))


__all__ = (
    "CONFIG_SCHEMA_VERSION",
    "ExecutionProfile", "config_path", "discover_profiles",
    "interactive_setup", "load_profile", "missing_credentials",
    "profile_directory", "profile_for_path", "read_selected_path",
    "resolve_execution_path", "save_selected_path", "select_profile",
)
