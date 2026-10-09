"""Trusted deterministic acceptance HOST. No providers, accounts or user files."""
from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import sys
import tempfile

PROFILE_ID = "rpnh-neutral-fixture/v1"
PLUGIN_ID = "neutral_fixture"
VERSION = "1.0"


def _execution(distribution):
    return {"python_executable": os.path.realpath(sys.executable), "python_prefix": os.path.realpath(sys.prefix),
            "pid": os.getpid(), "distribution": distribution, "distribution_version": metadata.version(distribution)}


def numbers(context, arguments):
    import numpy as np
    rule = context.config["rule"]
    if rule["story"] != "numbers" or rule["variant"] not in {"P0", "P1"}:
        raise ValueError("unsupported packaged numerical rule")
    factor = rule["factor"]
    values = np.asarray(arguments["values"], dtype=np.float64) * factor
    return {"business": {"mean": float(np.mean(values)), "variance": float(np.var(values)),
            "unit": arguments["unit"], "variance_unit": arguments["unit"] + "^2"}, "execution": _execution("numpy")}


def files(context, arguments):
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
    rule = context.config["rule"]
    if rule["story"] != "files" or Version(rule["rule_version"]) not in SpecifierSet("==1.0"):
        raise ValueError("unsupported packaged file-rule grammar")
    rows = []
    with tempfile.TemporaryDirectory(prefix="rpnh-neutral-files-") as folder:
        for name, content in sorted(arguments["files"].items()):
            if not name or Path(name).name != name or name in {".", ".."}:
                raise ValueError("synthetic basename required")
            path = Path(folder) / name
            path.write_bytes(content.encode("utf-8"))
            actual = path.read_bytes()
            if rule["exclude_empty"] and not actual:
                continue
            rows.append({"name": name, "bytes": len(actual), "sha256": hashlib.sha256(actual).hexdigest(), "empty": not bool(actual)})
    return {"business": {"files": len(rows), "nonempty": sum(not r["empty"] for r in rows),
            "empty": sum(r["empty"] for r in rows), "total_bytes": sum(r["bytes"] for r in rows), "digests": rows},
            "execution": _execution("packaging")}


def _object(properties):
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}


def definition():
    from cpn.plugins.api import PluginDefinition, PluginOperation
    execution = _object({"python_executable": {"type": "string"}, "python_prefix": {"type": "string"},
        "pid": {"type": "integer"}, "distribution": {"type": "string"}, "distribution_version": {"type": "string"}})
    numeric_input = _object({"values": {"type": "array", "items": {"type": "number"}, "minItems": 1}, "unit": {"type": "string"}})
    numeric_output = _object({"business": _object({"mean": {"type": "number"}, "variance": {"type": "number"},
        "unit": {"type": "string"}, "variance_unit": {"type": "string"}}), "execution": execution})
    file_input = _object({"files": {"type": "object", "additionalProperties": {"type": "string"}}})
    file_output = _object({"business": _object({"files": {"type": "integer"}, "nonempty": {"type": "integer"},
        "empty": {"type": "integer"}, "total_bytes": {"type": "integer"}, "digests": {"type": "array", "items": _object({
            "name": {"type": "string"}, "bytes": {"type": "integer"}, "sha256": {"type": "string"}, "empty": {"type": "boolean"}})}}), "execution": execution})
    rule = _object({"story": {"enum": ["numbers", "files"]}, "variant": {"enum": ["P0", "P1"]},
        "rule_version": {"const": "1.0"}, "factor": {"enum": [1, 2]}, "exclude_empty": {"type": "boolean"}})
    return PluginDefinition(PLUGIN_ID, VERSION, (
        PluginOperation("numbers", "Apply the exact packaged numerical rule", numeric_input, numeric_output, numbers),
        PluginOperation("files", "Apply the exact packaged synthetic file rule", file_input, file_output, files),
    ), config_schema=_object({"rule": rule}))


def catalog(config):
    from cpn.plugins.catalog import BoundPlugin, PluginCatalog
    return PluginCatalog((BoundPlugin(definition(), config),))


def _configuration(local):
    from cpn.rpnh.collaboration.environment_requirements import read_package_environment
    rows = [row for row in local["plugins"] if row["plugin_id"] == PLUGIN_ID]
    if len(rows) != 1:
        raise ValueError("exact local neutral fixture archive reference required")
    verified = read_package_environment(rows[0]["configuration_ref"])
    if verified.target.to_dict() != local["target"]:
        raise ValueError("neutral fixture archive differs from exact bound package")
    root = next(p for p in verified.previews if p.manifest_digest == local["target"]["root_manifest_digest"])
    entry = root.manifest["entries"][0]
    module = json.loads(dict(root.artifacts)[entry["declaration_path"]])
    # Fixed trusted interpreter of inert native-plugin config. No import locator,
    # script, operation implementation or arbitrary executable comes from ZIP.
    return module["components"][0]["config"]["native_plugin"]["capability"]["config"]


def probes(local):
    from cpn.rpnh.collaboration.environment_host import _distribution_fingerprint, profile_fingerprints
    from cpn.rpnh.collaboration.environment_check import ProbePolicy
    def plugin_metadata(*, requirement, binding, interpreter):
        _configuration(local)
        from cpn.rpnh.collaboration.share_packages import canonical_bytes, sha256
        digest = sha256(canonical_bytes({"code": _distribution_fingerprint(metadata.distribution("rpnh-neutral-fixture"), "rpnh_neutral_fixture"),
            "entry_point": "rpnh_neutral_fixture:definition"}))
        return {"status": "satisfied", "reason_code": "NEUTRAL_PLUGIN_METADATA_OBSERVED", "evidence_level": "installed_metadata",
            "identity": {"plugin_id": PLUGIN_ID, "version": VERSION, "api_contract": "rpnh/plugin/v1",
                         "distribution": "rpnh-neutral-fixture", "implementation_digest": digest,
                         "configuration_digest": hashlib.sha256(Path(binding["configuration_ref"]).read_bytes()).hexdigest()}}
    return ProbePolicy(plugins={PLUGIN_ID: plugin_metadata}, host_profiles=profile_fingerprints((PROFILE_ID,)))


def profile(local):
    from cpn.rpnh.collaboration.environment_host import HostProfile
    from cpn.plugins.runtime import plugin_registration
    from cpn.rpnh.public_module_materials import PublicHostSelection
    if type(local) is PublicHostSelection:
        configuration = local.configuration['configuration']
        public_catalog = catalog(configuration)
        def public_registration():
            result = plugin_registration(public_catalog)
            result.register_schema('rpnh/neutral_public_configuration/v1', {
                **definition().config_schema, '$id': 'rpnh/neutral_public_configuration/v1',
                '$schema': 'http://json-schema.org/draft-07/schema#'})
            return result
        return HostProfile(PROFILE_ID, public_registration,
            catalog=public_catalog, public_material_contract=local.contract_bytes)
    def policy():
        return probes(local)
    def registration():
        return plugin_registration(catalog(_configuration(local)))
    return HostProfile(PROFILE_ID, registration, policy)
