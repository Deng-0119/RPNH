"""Equivalent human/local-agent instructions. Rendering grants no authority."""
from dataclasses import dataclass
from .share_packages import canonical_bytes


@dataclass(frozen=True)
class SetupDocument:
    content: str
    format: str
    plan_digest: str


def render_environment_setup(plan, *, format="text"):
    if format not in {"text", "json"}:
        raise ValueError("setup format must be text or json")
    value = {"schema_version": "rpnh/environment_setup/v1", "plan_digest": plan.digest,
        "plan": plan.to_dict(), "authorization": "No authorization is conveyed by this document or a completed checkbox.",
        "acceptance": ["Verify the exact archive, manifest, package lock, entry and requirements digests.",
            "Obtain trusted approval of this exact plan digest, paths and actions before applying changes.",
            "Apply only the listed exact artifacts. Keep partial completed actions if interrupted; do not delete user files.",
            "Use the same check_environment checker on the resulting binding and exact resolution lock.",
            "Assemble the actual trusted HOST inside the selected interpreter and preserve its declaration digest.",
            "Retain missing/unsupported system and service checks; an agent's assertion cannot turn them into passed.",
            "Preparation is separate from a separately authorized package run and its Registry business terminal."]}
    if format == "json":
        return SetupDocument(canonical_bytes(value).decode("ascii"), format, plan.digest)
    p = plan.to_dict()
    lines = ["RPNH receiver environment setup", "Plan digest: " + plan.digest,
             "Exact target: " + canonical_bytes(p["target"]).decode("ascii"), value["authorization"], ""]
    for index, action in enumerate(p["actions"], 1):
        lines.append(f"{index}. {action['kind']} [{action['trusted_adapter_contract']}]")
        lines.append("   Target: " + canonical_bytes(action["target"]).decode("ascii"))
        lines.append("   Exact options: " + canonical_bytes(action["options"]).decode("ascii"))
        lines.append("   On failure: " + action["retained_on_failure"])
    lines.extend(["", "Unresolved: " + canonical_bytes(p["unresolved"]).decode("ascii"), "", "Acceptance:"])
    lines.extend("- " + text for text in value["acceptance"])
    return SetupDocument("\n".join(lines) + "\n", format, plan.digest)
