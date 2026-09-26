"""Build the installed provider/exact-model profiles from one catalog."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from jsonschema import Draft7Validator

from cpn.llm_adapters._external_provider_credentials import (
    credential_binding_from_document,
)
from cpn.llm_adapters._external_provider_recovery import (
    ExternalProviderRecoveryConfigError,
    recovery_policy_from_document,
)
from cpn.rpnh.user_config import (
    config_path, discover_profiles, profile_directory,
)
from cpn.rpnh.runtime_policy import runtime_policy_from_document


CATALOG_SCHEMA_VERSION = "rpnh/provider_model_catalog/v2"
GENERATED_INDEX_SCHEMA_VERSION = "rpnh/generated_provider_files/v1"
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]*")
_SAFE_FILE = re.compile(r"[a-z0-9][a-z0-9._-]*\.json")
_GENERATED_INDEX = "generated_provider_files.json"
_FORBIDDEN_STATIC_HEADERS = {
    "authorization", "connection", "content-length", "content-type", "host",
    "proxy-authorization", "transfer-encoding",
}


def default_catalog_path() -> Path:
    override = os.environ.get("RPNH_PROVIDER_CATALOG")
    return (Path(override).expanduser().resolve() if override else
            config_path().parent / "provider_models.json")


def catalog_template_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "provider_models.json"


def default_output_root() -> Path:
    return profile_directory().parent


def _read_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unavailable or invalid: {path}") from exc


def _validate_schema(document: Any) -> None:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "schemas" / "runtime" / "provider_model_catalog.v2.schema.json"
    )
    schema = _read_json(schema_path, label="provider catalog schema")
    errors = sorted(
        Draft7Validator(schema).iter_errors(document),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise ValueError(f"provider catalog {location}: {error.message}")


def _identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase identifier")
    return value


def _opaque_text(value: object, *, label: str) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127
                   for character in value)):
        raise ValueError(f"{label} must be nonempty trimmed text")
    return value


def _adapter_file(value: object) -> str:
    if not isinstance(value, str) or _SAFE_FILE.fullmatch(value) is None:
        raise ValueError("adapter_file must be one safe JSON filename")
    return value


def _replace_model(values: object, *, model: str, label: str) -> list[str]:
    if (not isinstance(values, list) or not values
            or any(not isinstance(item, str) or "\x00" in item for item in values)):
        raise ValueError(f"{label} must be a nonempty string array")
    return [item.replace("{model}", model) for item in values]


def _external_adapter(
        *, provider: str, model: str, adapter: Mapping[str, Any],
) -> dict[str, object]:
    endpoint = _opaque_text(adapter["endpoint"], label="endpoint")
    parsed = urlsplit(endpoint)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ValueError(
            f"{provider}/{model} endpoint must be HTTPS, or loopback HTTP, "
            "without credentials, query, or fragment")
    if parsed.scheme == "http":
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname.lower() == "localhost"
        if not loopback:
            raise ValueError(
                f"{provider}/{model} plaintext HTTP endpoint must be loopback")
    backend = _opaque_text(adapter["backend"], label="backend")
    route_id = _identifier(adapter["route_id"], label="route_id")
    credential = {
        **adapter["credential"],
    } if adapter["credential"] is not None else None
    try:
        binding = credential_binding_from_document(credential)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{provider}/{model} credential mapping is invalid") from exc
    headers = adapter.get("headers", {})
    lowered_headers = [name.lower() for name in headers]
    if (len(lowered_headers) != len(set(lowered_headers))
            or any(name in _FORBIDDEN_STATIC_HEADERS
                   for name in lowered_headers)
            or any(any(ord(character) < 32 or ord(character) == 127
                       for character in value)
                   for value in headers.values())):
        raise ValueError(
            f"{provider}/{model} static headers are invalid")
    if binding is not None and binding.header.lower() in lowered_headers:
        raise ValueError(
            f"{provider}/{model} credential header duplicates a static header")
    try:
        recovery = recovery_policy_from_document(adapter["recovery"])
    except ExternalProviderRecoveryConfigError as exc:
        raise ValueError(f"{provider}/{model} {exc}") from exc
    return {
        "schema_version": "external_provider_adapter_config/v2",
        "adapter_kind": "external_provider",
        "model_condition": model,
        "recovery": recovery.as_document(),
        "routes": [{
            "route_id": route_id,
            "provider": provider,
            "backend": backend,
            "protocol": adapter["protocol"],
            "endpoint": endpoint,
            "outbound_model": model,
            "credential": credential,
            "headers": headers,
        }],
    }


def _local_adapter(
        *, model: str, adapter: Mapping[str, Any],
) -> dict[str, object]:
    return {
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": model,
        "argv": _replace_model(
            adapter["argv"], model=model, label="local adapter argv"),
        "probe_argv": _replace_model(
            adapter["probe_argv"], model=model,
            label="local adapter probe_argv"),
        "env": adapter.get("env", {}),
        "inherit_env": adapter.get("inherit_env", []),
    }


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def _render(document: Mapping[str, Any]) -> dict[str, bytes]:
    if document.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError("provider catalog schema_version is not current")
    outputs: dict[str, bytes] = {}
    profiles: set[str] = set()
    providers: set[str] = set()
    registrations: list[dict[str, object]] = []
    for raw_provider in document["providers"]:
        provider = _opaque_text(raw_provider["provider"], label="provider")
        display_name = _opaque_text(
            raw_provider["display_name"], label="display_name")
        if provider in providers:
            raise ValueError(f"duplicate provider: {provider}")
        providers.add(provider)
        for raw_model in raw_provider["models"]:
            profile = _identifier(raw_model["profile"], label="profile")
            model = _opaque_text(
                raw_model["model_condition"], label="model_condition")
            context_window = raw_model.get("context_window_tokens")
            retained_context = raw_model.get(
                "context_compaction_retained_tokens")
            if (context_window is not None
                    and retained_context is not None
                    and retained_context >= context_window):
                raise ValueError(
                    "context_compaction_retained_tokens must be smaller "
                    "than context_window_tokens")
            if profile in profiles:
                raise ValueError(f"duplicate profile: {profile}")
            profiles.add(profile)
            adapter_file = _adapter_file(profile + ".json")
            adapter = raw_model["adapter"]
            adapter_document = (
                _external_adapter(
                    provider=provider, model=model, adapter=adapter)
                if adapter["adapter_kind"] == "external_provider"
                else _local_adapter(model=model, adapter=adapter)
            )
            execution_document = {
                "schema_version": "llm_execution_selection/v1",
                "adapter_kind": adapter["adapter_kind"],
                "model_condition": model,
                "adapter_config_path": f"../adapters/{adapter_file}",
                "timeout_seconds": raw_model["timeout_seconds"],
                "max_output_tokens": raw_model["max_output_tokens"],
                "max_response_bytes": raw_model["max_response_bytes"],
                "runtime": runtime_policy_from_document(
                    raw_model.get("runtime")).as_document(),
            }
            if "context_window_tokens" in raw_model:
                execution_document["context_window_tokens"] = (
                    raw_model["context_window_tokens"])
            if "context_compaction_retained_tokens" in raw_model:
                execution_document["context_compaction_retained_tokens"] = (
                    raw_model["context_compaction_retained_tokens"])
            adapter_relative = f"adapters/{adapter_file}"
            execution_relative = f"execution/{profile}.json"
            if adapter_relative in outputs:
                raise ValueError(f"duplicate adapter_file: {adapter_file}")
            outputs[adapter_relative] = _json_bytes(adapter_document)
            outputs[execution_relative] = _json_bytes(execution_document)
            registrations.append({
                "profile": profile,
                "provider": provider,
                "display_name": display_name,
                "model_condition": model,
            })
    outputs["profiles.json"] = _json_bytes({
        "schema_version": "rpnh/provider_profiles/v2",
        "profiles": registrations,
    })
    return outputs


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _prior_generated_files(root: Path) -> set[str]:
    path = root / _GENERATED_INDEX
    if not path.is_file():
        return set()
    document = _read_json(path, label="generated provider index")
    if (not isinstance(document, Mapping)
            or document.get("schema_version")
            != GENERATED_INDEX_SCHEMA_VERSION
            or not isinstance(document.get("generated_files"), list)
            or any(not isinstance(item, str) for item
                   in document["generated_files"])):
        raise ValueError("generated provider index is invalid")
    return set(document["generated_files"])


def initialize_provider_catalog(
        catalog_path: Path | None = None,
) -> dict[str, object]:
    """Create an empty user-owned catalog without choosing any model."""
    destination = (default_catalog_path() if catalog_path is None
                   else catalog_path.expanduser().resolve())
    if destination.exists():
        document = _read_json(destination, label="provider model catalog")
        _validate_schema(document)
        return {"catalog": str(destination), "status": "already_exists"}
    template = _read_json(catalog_template_path(), label="provider catalog template")
    _validate_schema(template)
    _write_atomic(destination, _json_bytes(template))
    return {"catalog": str(destination), "status": "created"}


def build_provider_catalog(
        catalog_path: Path | None = None, output_root: Path | None = None, *,
        check: bool = False,
) -> dict[str, object]:
    """Render or verify all runtime profile files declared by one catalog."""
    catalog = (default_catalog_path() if catalog_path is None
               else catalog_path.expanduser().resolve())
    root = (default_output_root() if output_root is None
            else output_root.expanduser().resolve())
    document = _read_json(catalog, label="provider model catalog")
    _validate_schema(document)
    outputs = _render(document)
    generated = sorted(outputs)
    index_bytes = _json_bytes({
        "schema_version": GENERATED_INDEX_SCHEMA_VERSION,
        "generated_files": generated,
    })
    mismatches = [
        relative for relative, payload in outputs.items()
        if not (root / relative).is_file()
        or (root / relative).read_bytes() != payload
    ]
    index_path = root / _GENERATED_INDEX
    if not index_path.is_file() or index_path.read_bytes() != index_bytes:
        mismatches.append(_GENERATED_INDEX)
    if check:
        if mismatches:
            raise ValueError(
                "generated provider files are out of date: "
                + ", ".join(sorted(mismatches)))
    else:
        prior = _prior_generated_files(root)
        for relative in sorted(prior - set(generated)):
            path = root / relative
            if (path.resolve().is_relative_to(root.resolve())
                    and path.suffix == ".json" and path.is_file()):
                path.unlink()
        for relative, payload in outputs.items():
            _write_atomic(root / relative, payload)
        _write_atomic(index_path, index_bytes)
    profiles = discover_profiles(root / "execution")
    expected_profile_count = sum(
        len(provider["models"]) for provider in document["providers"])
    if len(profiles) != expected_profile_count:
        # This guard should never be reached after catalog and runtime
        # validation agree.
        raise ValueError("generated provider profile count is inconsistent")
    return {
        "catalog": str(catalog),
        "output_root": str(root),
        "status": "ok" if not check else "in_sync",
        "profile_count": len(profiles),
        "selection_ids": [profile.selection_id for profile in profiles],
        "generated_files": generated,
    }



def add_provider_model(
        provider: str, model: Mapping[str, Any], *,
        catalog_path: Path | None = None, output_root: Path | None = None,
) -> dict[str, object]:
    """Append one user profile using the existing catalog compiler.

    Validate generated profiles in a temporary directory first. Existing profile
    names cannot be replaced here; manual catalog editing remains explicit.
    On an ordinary write error, restore the files touched by this command. This
    is not a cross-file crash transaction; `config build` can regenerate files.
    """
    catalog = (default_catalog_path() if catalog_path is None
               else catalog_path.expanduser().resolve())
    root = (default_output_root() if output_root is None
            else output_root.expanduser().resolve())
    catalog, root = catalog.expanduser().resolve(), root.expanduser().resolve()
    original = catalog.read_bytes() if catalog.exists() else None
    document = (json.loads(original) if original is not None else
                {"schema_version": CATALOG_SCHEMA_VERSION, "providers": []})
    _validate_schema(document)
    provider = _opaque_text(provider, label="provider")
    model = json.loads(json.dumps(dict(model), allow_nan=False))
    name = _identifier(model.get("profile"), label="profile")
    if any(item["profile"] == name for entry in document["providers"]
           for item in entry["models"]):
        raise ValueError(f"profile already exists: {name}; choose a new name")
    matches = [entry for entry in document["providers"]
               if entry["provider"] == provider]
    if matches:
        matches[0]["models"].append(model)
    else:
        document["providers"].append({
            "provider": provider, "display_name": provider, "models": [model]})
    _validate_schema(document)
    outputs = _render(document)
    # Check the real generated-profile loader, not just the input schema.
    with tempfile.TemporaryDirectory(prefix="rpnh-config-check-") as directory:
        staging = Path(directory)
        (staging / "catalog.json").write_bytes(_json_bytes(document))
        build_provider_catalog(staging / "catalog.json", staging / "profiles")
    prior = _prior_generated_files(root)
    unknown = {path.relative_to(root).as_posix()
               for path in (root / "execution").glob("*.json")} - prior
    if unknown:
        raise ValueError("unmanaged execution profiles exist; use a separate "
                         "RPNH_PROFILE_DIR or reconcile the catalog first")
    for relative in outputs:
        if (root / relative).exists() and relative not in prior:
            raise ValueError(f"will not overwrite an unmanaged profile file: {relative}")
    managed = {root / relative for relative in prior
               if (root / relative).resolve().is_relative_to(root)
               and (root / relative).suffix == ".json"}
    paths = {catalog, root / _GENERATED_INDEX,
             *(root / relative for relative in outputs), *managed}
    if catalog in {root / relative for relative in outputs} | {root / _GENERATED_INDEX}:
        raise ValueError("catalog must be separate from generated profile files")
    before = {path: path.read_bytes() if path.exists() else None for path in paths}
    if before[catalog] != original:
        raise ValueError("catalog changed during setup; run setup again")
    try:
        _write_atomic(catalog, _json_bytes(document))
        return build_provider_catalog(catalog, root)
    except BaseException as exc:
        for path, payload in before.items():
            try:
                if payload is None:
                    path.unlink(missing_ok=True)
                else:
                    _write_atomic(path, payload)
            except OSError:
                exc.add_note(f"Could not restore {path}; check the catalog and run `rpnh config build`.")
        raise

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rpnh-configure-providers",
        description=(
            "Generate RPNH provider, adapter, and execution profiles from "
            "one declarative catalog."),
    )
    parser.add_argument("--catalog", type=Path, default=default_catalog_path())
    parser.add_argument("--output-root", type=Path, default=default_output_root())
    parser.add_argument(
        "--init", action="store_true",
        help="create an empty user-owned catalog and do not generate profiles",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="verify generated files without changing them",
    )
    args = parser.parse_args(argv)
    try:
        if args.init:
            if args.check:
                raise ValueError("--init and --check cannot be combined")
            result = initialize_provider_catalog(args.catalog)
        else:
            result = build_provider_catalog(
                args.catalog, args.output_root, check=args.check)
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "CATALOG_SCHEMA_VERSION", "add_provider_model", "build_provider_catalog", "catalog_template_path",
    "default_catalog_path", "default_output_root", "initialize_provider_catalog",
    "main",
)
