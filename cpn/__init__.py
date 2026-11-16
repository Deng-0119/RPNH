"""Registry-governed Petri-net harness."""

def _distribution_version() -> str:
    # Source checkouts use the same authoritative version as the build. An
    # installed wheel uses its generated distribution metadata, never another
    # handwritten copy that can drift from pyproject.toml.
    from importlib.metadata import PackageNotFoundError, version
    from pathlib import Path
    import tomllib

    project = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if project.is_file():
        metadata = tomllib.loads(project.read_text(encoding="utf-8")).get("project", {})
        if metadata.get("name") == "rpnh-harness":
            return str(metadata["version"])
    try:
        return version("rpnh-harness")
    except PackageNotFoundError:
        return "unknown"


__version__ = _distribution_version()
