"""Explicit HOST selection of installed plugins; pure, versioned assembly."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .api import (API_VERSION, PluginDefinition, PluginError, PluginOperation,
                  canonical, frozen, json_copy, symbol, validate, version)

CONFIG_VERSION = "rpnh/plugins/v1"
ENTRY_POINT_GROUP = "rpnh.plugins"


@dataclass(frozen=True, slots=True)
class BoundPlugin:
    definition: PluginDefinition
    config: Mapping[str, Any]
    environment: tuple[str, ...] = ()

    def __post_init__(self):
        import re
        if not isinstance(self.definition, PluginDefinition):
            raise PluginError("bound plugin requires a validated definition")
        if (not isinstance(self.environment, tuple)
                or any(not isinstance(x, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", x)
                       for x in self.environment)
                or len(set(self.environment)) != len(self.environment)):
            raise PluginError("environment requires distinct variable names")
        object.__setattr__(self, "config", frozen(validate(self.definition.config_schema, self.config)))
        object.__setattr__(self, "environment", tuple(sorted(self.environment)))

    def descriptor(self):
        return {"definition": self.definition.descriptor(), "config": json_copy(self.config),
                "environment": list(self.environment)}

    @property
    def digest(self):
        return hashlib.sha256(canonical(self.descriptor())).hexdigest()


class PluginCatalog:
    """Complete selected catalog. No imports from task-authored locators."""
    def __init__(self, plugins=()):
        self._plugins = {}
        for plugin in plugins:
            if not isinstance(plugin, BoundPlugin):
                raise PluginError("catalog requires configured plugin definitions")
            name = plugin.definition.name
            if name in self._plugins:
                raise PluginError("duplicate plugin identity")
            self._plugins[name] = plugin
        order, visiting, visited = [], set(), set()
        def visit(name):
            if name in visiting:
                raise PluginError("plugin dependency cycle")
            if name in visited:
                return
            visiting.add(name)
            for required, revision in sorted(self._plugins[name].definition.requires.items()):
                if required not in self._plugins or self._plugins[required].definition.version != revision:
                    raise PluginError("missing or incompatible plugin dependency: " + required)
                visit(required)
            visiting.remove(name); visited.add(name); order.append(name)
        for name in sorted(self._plugins):
            visit(name)
        self.plugins = tuple(self._plugins[name] for name in order)

    def resolve(self, selector: str) -> tuple[BoundPlugin, PluginOperation]:
        if not isinstance(selector, str) or selector.count("/") != 1:
            raise PluginError("operation selector must be plugin_name/operation_name")
        name, operation = selector.split("/")
        symbol(name); symbol(operation)
        plugin = self._plugins.get(name)
        if plugin is not None:
            for op in plugin.definition.operations:
                if op.name == operation:
                    return plugin, op
        raise PluginError("operation is not in the owner-selected plugin catalog")

    def operation_key(self, selector):
        plugin, operation = self.resolve(selector)
        return (f"rpnh/plugin/{plugin.definition.name}/{plugin.definition.version}/"
                f"{operation.name}/{plugin.digest}")

    def describe(self):
        return [{"selector": p.definition.name + "/" + op.name,
                 "version": p.definition.version, "binding_digest": p.digest,
                 **op.descriptor()}
                for p in self.plugins for op in sorted(p.definition.operations, key=lambda x: x.name)]

    @property
    def digest(self):
        return hashlib.sha256(canonical([p.descriptor() for p in self.plugins])).hexdigest()


def load_catalog(document=None, *, factories=None) -> PluginCatalog:
    """Resolve only explicitly selected installed entry points.

    ``factories`` is a trusted Python HOST/testing injection, not JSON data.
    Imports/factory execution are trusted code and must be declaration-only.
    """
    if document is None:
        return PluginCatalog()
    rows = _selection_rows(document)
    installed = None if factories is not None else metadata.entry_points(group=ENTRY_POINT_GROUP)
    configured = []
    for row in rows:
        if factories is not None:
            factory = factories.get(row["entry_point"])
            if factory is None:
                raise PluginError("unknown explicitly installed plugin entry point")
        else:
            matches = [ep for ep in installed if ep.name == row["entry_point"]]
            if len(matches) != 1:
                raise PluginError("plugin entry point must resolve uniquely: " + row["entry_point"])
            factory = matches[0].load()
        definition = factory()
        if (not isinstance(definition, PluginDefinition)
                or definition.name != row["name"] or definition.version != row["version"]
                or definition.api_version != API_VERSION):
            raise PluginError("installed plugin differs from the selected identity/version")
        config = validate(definition.config_schema, row["config"])
        configured.append(BoundPlugin(definition, frozen(config), tuple(sorted(row["environment"]))))
    return PluginCatalog(configured)


def read_config(path: Path | None = None):
    """No implicit project code discovery; default is empty unless owner selects a file."""
    selected = path or os.environ.get("RPNH_PLUGIN_CONFIG")
    if selected is None:
        return None
    with Path(selected).open(encoding="utf-8") as stream:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise PluginError("duplicate configuration key")
                result[key] = value
            return result
        return json.load(stream, object_pairs_hook=unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(PluginError("nonfinite config")))


def _selection_rows(document):
    if document is None:
        return []
    value = json_copy(document)
    if (not isinstance(value, dict) or set(value) != {"schema_version", "plugins"}
            or value["schema_version"] != CONFIG_VERSION or not isinstance(value["plugins"], list)):
        raise PluginError("invalid plugin configuration document")
    rows = value["plugins"]
    # Check the entire input before importing any selected factory.
    seen = set()
    for row in rows:
        if (not isinstance(row, dict)
                or set(row) != {"name", "entry_point", "version", "config", "environment"}
                or not isinstance(row["entry_point"], str) or not row["entry_point"]
                or not isinstance(row["config"], dict)
                or not isinstance(row["environment"], list)):
            raise PluginError("plugin selection requires name/entry_point/version/config/environment")
        symbol(row["name"]); version(row["version"])
        if row["name"] in seen:
            raise PluginError("duplicate selected plugin")
        seen.add(row["name"])
        import re
        if (len(set(row["environment"])) != len(row["environment"])
                or any(not isinstance(x, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", x)
                       for x in row["environment"])):
            raise PluginError("environment contains invalid or duplicate variable names")
    return rows


INERT_METADATA_FILE = "rpnh_environment_plugins.json"


def read_plugin_metadata(text):
    """Validate the existing inert wheel/installed metadata contract; no imports.

    None means missing metadata, not an invitation to execute a factory.
    """
    if text is None:
        return None
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PluginError("duplicate inert metadata key: " + key)
            result[key] = value
        return result
    try:
        value = json.loads(text, object_pairs_hook=unique)
    except (ValueError, TypeError) as exc:
        raise PluginError("invalid " + INERT_METADATA_FILE) from exc
    if (not isinstance(value, dict) or set(value) != {"schema_version", "plugins"}
            or value["schema_version"] != "rpnh/installed_plugin_metadata/v1"
            or not isinstance(value["plugins"], list) or len(value["plugins"]) > 128):
        raise PluginError("invalid inert plugin metadata document")
    seen = set()
    for row in value["plugins"]:
        if (not isinstance(row, dict)
                or set(row) != {"plugin_id", "version", "api_contract", "entry_point"}
                or any(not isinstance(v, str) or not v or len(v) > 240 for v in row.values())):
            raise PluginError("invalid inert plugin metadata row")
        version(row["version"])
        key = row["plugin_id"], row["entry_point"]
        if key in seen:
            raise PluginError("duplicate inert plugin declaration")
        seen.add(key)
    return value


def inspect_plugins(document=None):
    """Observe installed entry points and explicit selections without loading code.

    This plugin catalog observation is separate from Registry source authority.
    Metadata is an inert declaration, never a verified PluginDefinition or HOST
    binding. Installed file identities are not original wheel artifact hashes.
    Config values/environment names are deliberately not included in this view.
    """
    selected = _selection_rows(document)
    installed = list(metadata.entry_points(group=ENTRY_POINT_GROUP))
    rows = []
    for ep in sorted(installed, key=lambda item: (item.name, item.value)):
        raw = ep.dist.read_text(INERT_METADATA_FILE)
        error = None
        try:
            inert = read_plugin_metadata(raw)
        except PluginError as exc:
            inert, error = None, str(exc)
        declarations = [] if inert is None else [r for r in inert["plugins"] if r["entry_point"] == ep.name]
        choices = [{"name": r["name"], "version": r["version"]}
                   for r in selected if r["entry_point"] == ep.name]
        rows.append({
            "entry_point": ep.name, "entry_point_value": ep.value,
            "installed_distribution": {"name": ep.dist.metadata["Name"], "version": ep.dist.version,
                "location": str(Path(ep.dist.locate_file("")).resolve()), "wheel_sha256": None},
            "inert_metadata": {"status": "invalid" if error else "declared" if declarations else "unknown",
                "source": INERT_METADATA_FILE, "declarations": declarations, "error": error},
            "selection": {"status": "selected" if choices else "not_selected", "identities": choices,
                "entry_point_resolution": "unique" if sum(p.name == ep.name for p in installed) == 1 else "ambiguous"},
            "factory_loaded": "not_checked", "host_bound": "not_checked",
        })
    for choice in selected:
        if not any(ep.name == choice["entry_point"] for ep in installed):
            rows.append({"entry_point": choice["entry_point"], "entry_point_value": None,
                "installed_distribution": None,
                "inert_metadata": {"status": "unknown", "source": INERT_METADATA_FILE,
                    "declarations": [], "error": None},
                "selection": {"status": "selected", "identities": [{"name": choice["name"], "version": choice["version"]}],
                    "entry_point_resolution": "missing"},
                "factory_loaded": "not_checked", "host_bound": "not_checked"})
    return {"schema_version": "rpnh/plugin_inspection/v1", "plugins": rows,
        "note": "No factory imported. Inert declarations and selected versions are not factory verification or HOST binding; wheel SHA is unknown."}


def inspect_plugin_wheel(path):
    """Read an explicit wheel's inert declarations without installing/importing.

    This does not verify PluginDefinition, dependencies, or execution readiness.
    No wheel member is extracted. Missing declarations stay unknown.
    """
    import configparser
    from email.parser import Parser
    import zipfile
    with zipfile.ZipFile(path) as archive:
        metadata_files = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            raise PluginError("wheel requires one distribution METADATA")
        prefix = metadata_files[0].rsplit("/", 1)[0] + "/"
        distribution = Parser().parsestr(archive.read(metadata_files[0]).decode("utf-8"))
        name = prefix + INERT_METADATA_FILE
        inert = read_plugin_metadata(archive.read(name).decode("utf-8") if name in archive.namelist() else None)
        entrypoints = configparser.ConfigParser(interpolation=None)
        entrypoints.optionxform = str
        ep_name = prefix + "entry_points.txt"
        if ep_name in archive.namelist():
            entrypoints.read_string(archive.read(ep_name).decode("utf-8"))
        entries = dict(entrypoints[ENTRY_POINT_GROUP]) if entrypoints.has_section(ENTRY_POINT_GROUP) else {}
        if inert and any(row["entry_point"] not in entries for row in inert["plugins"]):
            raise PluginError("wheel inert declaration has no matching rpnh.plugins entry point")
        return {"schema_version": "rpnh/plugin_wheel_inspection/v1",
            "wheel": str(Path(path).resolve()),
            "distribution": {"name": distribution["Name"], "version": distribution["Version"]},
            "entry_points": entries, "inert_metadata": {"status": "unknown" if inert is None else "declared", "document": inert},
            "installed": "not_checked", "selected": "not_checked",
            "factory_loaded": "not_checked", "host_bound": "not_checked"}
