"""Locate the exact successful binding written by prepare-environment.

A receipt or binding is preparation evidence only, never a business result.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from cpn.rpnh.collaboration.environment_local_contracts import LocalEnvironmentBinding, PreparationReceipt


def prepared_binding(receipt_path, state_dir):
    receipt = PreparationReceipt.from_bytes(Path(receipt_path).read_bytes()).to_dict()
    if receipt["failure"] is not None or receipt["result_binding_digest"] is None or receipt["host_declarations_digest"] is None:
        raise ValueError("preparation did not complete actual HOST assembly")
    path = Path(state_dir).absolute() / ("binding-" + receipt["result_binding_digest"] + ".json")
    binding = LocalEnvironmentBinding.from_bytes(path.read_bytes())
    if (binding.digest != receipt["result_binding_digest"] or binding.to_dict()["target"] != receipt["target"]
            or binding.to_dict()["binding_revision"] != receipt["result_binding_revision"]):
        raise ValueError("binding differs from the successful preparation receipt")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    print(prepared_binding(args.receipt, args.state_dir))
