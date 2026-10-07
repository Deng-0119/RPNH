"""Publish inert environment metadata alongside normal wheel metadata.

This build hook does not import the plugin or execute an operation. RPNH still
requires the receiver's actual installed HOST assembly before any package run.
"""
from pathlib import Path
from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel


class EnvironmentMetadataWheel(bdist_wheel):
    def egg2dist(self, egginfo_path, distinfo_path):
        super().egg2dist(egginfo_path, distinfo_path)
        source = Path(__file__).parent / "rpnh_environment_plugins.json"
        (Path(distinfo_path) / source.name).write_bytes(source.read_bytes())


setup(cmdclass={"bdist_wheel": EnvironmentMetadataWheel})
