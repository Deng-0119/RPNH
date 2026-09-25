"""Interactive model setup and offline configuration diagnostics."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Callable
from urllib.parse import urlsplit

from cpn.rpnh import provider_setup
from cpn.rpnh.user_config import (
    ExecutionProfile, discover_profiles, load_profile, profile_directory,
    read_selected_path, resolve_execution_path, save_selected_path,
)


class SetupCancelled(ValueError):
    """The user cancelled setup before configuration was written."""


def _ask(read, write, label, *, default=None, validate=None):
    while True:
        try:
            value = read(label + (f" [{default}]" if default is not None else "") + ": ").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise SetupCancelled("Setup cancelled; no new model was saved.") from exc
        if not value and default is not None:
            value = str(default)
        try:
            if validate is not None:
                return validate(value)
            if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError("Enter a nonempty value without control characters.")
            return value
        except ValueError as exc:
            write(str(exc))


def _choice(*choices):
    def validate(value):
        if value not in choices:
            raise ValueError("Choose " + ", ".join(choices) + ".")
        return value
    return validate


def _yes(value):
    lowered = value.casefold()
    if lowered not in {"y", "yes", "n", "no"}:
        raise ValueError("Enter y or n.")
    return lowered in {"y", "yes"}


def _positive(value):
    if not value.isascii() or not value.isdecimal() or int(value) < 1:
        raise ValueError("Enter a positive whole number.")
    return int(value)


def _environment(value):
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
        raise ValueError("Enter an environment VARIABLE NAME, not an API key.")
    return value


def _endpoint(value):
    parsed = urlsplit(value)
    if (len(value) > 2048 or parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("Use the full HTTPS endpoint without credentials, query, or fragment.")
    return value


def _argv(value):
    values = shlex.split(value)
    if not values or any("\x00" in item for item in values):
        raise ValueError("Enter a command and its arguments.")
    return values


def _slug(value):
    return re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-._")[:64] or "my-model"


def _next_steps(profile: ExecutionProfile, write):
    write(f"Selected {profile.provider} / {profile.model_condition} ({profile.name}).")
    missing = profile.as_public_dict()["missing_credential_environment"]
    if missing:
        write("Set the API key in your shell before starting RPNH (Bash):")
        for name in missing:
            write(f"  read -rsp 'API key: ' {name}; echo; export {name}")
        write("The key is not saved in RPNH configuration. Repeat in a new shell, or use your secret manager.")
    write("Next: rpnh doctor   (local checks only), then rpnh")
    write("No provider request was sent. Model availability is checked only when you run a task.")


def _add_profile(read, write, *, directory=None):
    write("Add a model. Enter the identifiers and endpoint supplied by your provider.")
    write("1. HTTPS API (OpenAI-compatible Chat Completions)\n2. Local adapter command (advanced)")
    kind = _ask(read, write, "Connection", default="1", validate=_choice("1", "2"))
    provider = _ask(read, write, "Provider name (your label)")
    model = _ask(read, write, "Model ID (exact provider identifier)")
    existing = {p.name for p in discover_profiles(directory)}
    suggestion = _slug(provider + "-" + model)
    stem, suffix = suggestion, 2
    while suggestion in existing:
        suggestion = f"{stem}-{suffix}"
        suffix += 1

    def profile_name(value):
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value) is None:
            raise ValueError("Use lowercase letters, digits, dots, hyphens or underscores.")
        if value in existing:
            raise ValueError("That profile already exists; choose another name.")
        return value

    name = _ask(read, write, "Profile name", default=suggestion, validate=profile_name)
    if kind == "1":
        endpoint = _ask(read, write, "Full endpoint (https://.../v1/chat/completions)", validate=_endpoint)
        write("1. Bearer API key\n2. API key in a custom header\n3. No authentication")
        auth = _ask(read, write, "Authentication", default="1", validate=_choice("1", "2", "3"))
        credential = None
        if auth != "3":
            environment = _ask(read, write, "API key environment variable (NOT the key)",
                               default="RPNH_API_KEY", validate=_environment)
            header, prefix = "Authorization", "Bearer "
            if auth == "2":
                from cpn.llm_adapters._external_provider_credentials import ProviderCredentialBinding

                def header_name(value):
                    ProviderCredentialBinding(environment, value, "")
                    return value

                header = _ask(read, write, "Header name", default="X-API-Key", validate=header_name)
                prefix = ""
            credential = {"environment": environment, "header": header, "prefix": prefix}
        adapter = {
            "adapter_kind": "external_provider", "route_id": "primary",
            "backend": provider, "protocol": "openai_chat_completions/v1",
            "endpoint": endpoint, "credential": credential, "headers": {},
            "recovery": {"strategy": "bounded_same_route_health_probe/v1",
                         "max_probe_attempts": 3, "probe_timeout_budget_seconds": 300,
                         "max_probe_success_formal_failure_cycles": 3},
        }
    else:
        write("The adapter must read RPNH request JSON on stdin and return RPNH response JSON on stdout.")
        write("A plain chat CLI needs a wrapper. Commands are passed as argv, not through a shell.")
        argv = _ask(read, write, "Adapter command ({model} and {python} are supported)", validate=_argv)
        probe = _ask(read, write, "Probe command (not run during setup)",
                     default=shlex.join([argv[0], "--version"]), validate=_argv)

        def inherited(value):
            values = [v.strip() for v in value.split(",") if v.strip()]
            for item in values:
                _environment(item)
            return list(dict.fromkeys(values))

        inherit = _ask(read, write, "Environment variable names to pass (comma-separated; optional)",
                       default="", validate=inherited)
        adapter = {"adapter_kind": "local_process", "argv": argv, "probe_argv": probe,
                   "env": {}, "inherit_env": inherit}
    limits = {"timeout_seconds": 900, "max_output_tokens": 8192, "max_response_bytes": 16777216}
    write("Default limits: 900 seconds per request; 8192 output tokens; 16 MiB response.")
    if _ask(read, write, "Customize these limits?", default="n", validate=_yes):
        for field, label in (("timeout_seconds", "Request timeout (seconds)"),
                             ("max_output_tokens", "Maximum output tokens"),
                             ("max_response_bytes", "Maximum response bytes")):
            limits[field] = _ask(read, write, label, default=limits[field], validate=_positive)
    write(f"Provider: {provider}\nModel: {model}\nProfile: {name}")
    write("Endpoint: " + adapter["endpoint"] if kind == "1" else "Command: " + shlex.join(adapter["argv"]))
    write("Catalog: " + str(provider_setup.default_catalog_path()))
    if not _ask(read, write, "Save this profile and select it?", default="y", validate=_yes):
        raise SetupCancelled("Setup cancelled; no new model was saved.")
    root = None if directory is None else directory.resolve().parent
    provider_setup.add_provider_model(provider, {
        "profile": name, "model_condition": model, "adapter": adapter, **limits,
    }, output_root=root)
    profile = next(p for p in discover_profiles(directory) if p.name == name)
    save_selected_path(profile.path, directory=directory)
    _next_steps(profile, write)
    return profile


def setup_profile(*, input_fn: Callable[[str], str] | None = None,
                  output_fn: Callable[[str], None] | None = None,
                  directory: Path | None = None, add_new: bool = False) -> ExecutionProfile:
    """Create or select a profile without constructing an execution runtime."""
    read, write = input_fn or input, output_fn or print
    write("RPNH setup — Ctrl-C cancels. Configuration only; no model calls.")
    profiles = discover_profiles(directory)
    catalog = provider_setup.default_catalog_path()
    if not profiles and catalog.is_file():
        document = json.loads(catalog.read_bytes())
        provider_setup._validate_schema(document)
        if document["providers"]:
            if not _ask(read, write, "Build profiles from your existing catalog?", default="y", validate=_yes):
                raise SetupCancelled("Setup cancelled; existing catalog was not changed.")
            provider_setup.build_provider_catalog(
                output_root=None if directory is None else directory.resolve().parent)
            profiles = discover_profiles(directory)
    if add_new or not profiles:
        return _add_profile(read, write, directory=directory)
    write("0. Add a new model")
    for i, profile in enumerate(profiles, 1):
        missing = profile.as_public_dict()["missing_credential_environment"]
        suffix = "needs " + ", ".join(missing) if missing else "configured"
        write(f"{i}. {profile.name}: {profile.provider} / {profile.model_condition} ({suffix})")
    try:
        selected = read_selected_path()
    except (OSError, RuntimeError, TypeError, ValueError):
        selected = None
        write("The saved selection is unavailable. Choose a configured profile below.")
    default = next((str(i) for i, p in enumerate(profiles, 1) if p.path == selected), "1")
    choice = _ask(read, write, "Choose a profile", default=default,
                  validate=_choice(*(str(i) for i in range(len(profiles) + 1))))
    if choice == "0":
        return _add_profile(read, write, directory=directory)
    profile = profiles[int(choice) - 1]
    save_selected_path(profile.path, directory=directory)
    _next_steps(profile, write)
    return profile


def configuration_report(execution: Path | None = None) -> dict:
    """Read local files and validate transport configuration; never send a probe."""
    checks = []

    def check(name, status, detail):
        checks.append({"check": name, "status": status, "detail": detail})

    check("platform", "ok" if sys.platform == "linux" else "error",
          "Linux / WSL2" if sys.platform == "linux" else "Run RPNH on Linux or WSL2.")
    profile = None
    try:
        selected = resolve_execution_path(execution, save_default=False)
        profile = load_profile(selected)
        check("model", "ok", f"{profile.provider} / {profile.model_condition}")
        from cpn.llm_adapters import load_llm_execution_selection
        selection = load_llm_execution_selection(selected)
        if profile.adapter_kind == "external_provider":
            from cpn.llm_adapters.external_provider import _load_config
            _load_config(selection.adapter_config_path, profile.model_condition)
        else:
            from cpn.llm_adapters.local_process import _load_config
            argv, _environment_values = _load_config(selection.adapter_config_path, profile.model_condition)
            if not Path(argv[0]).is_file() or not os.access(argv[0], os.X_OK):
                raise ValueError("Local adapter executable is missing or not executable.")
        check("adapter", "ok", "Configuration is valid; no adapter command or provider request executed.")
        missing = profile.as_public_dict()["missing_credential_environment"]
        check("credentials", "error" if missing else "ok",
              "Set environment variable(s): " + ", ".join(missing) if missing
              else "Required API key variables are present (values are not displayed).")
        if any(p.path == selected for p in discover_profiles()):
            provider_setup.build_provider_catalog(check=True)
            check("generated profiles", "ok", "Generated files match the catalog.")
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        check("configuration", "error", str(exc) + " Run `rpnh init` or `rpnh config build`.")
    return {"ready": all(c["status"] != "error" for c in checks),
            "network_checked": False, "checks": checks}
