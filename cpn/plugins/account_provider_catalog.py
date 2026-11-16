"""Explicit installed HOST account providers for ordinary task workers.

Only the chosen entry point is loaded. Its declared implementation identity is
compared in full before the factory can run. The provider supplies the existing
resolver ABI and current-use authority; this catalog stores no account grants,
credentials, active-account preference or runtime results.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .api import canonical, frozen, implementation_identity, json_copy
from .account_credentials import AccountCredentialError


PROVIDER_SCHEMA = "rpnh/installed_account_provider/v1"
PROVIDER_ENTRY_POINT_GROUP = "rpnh.account_providers"


def account_provider_schema_document():
    path = Path(__file__).resolve().parents[1] / "schemas" / "rpnh" / "installed_account_provider.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class AccountProviderSelection:
    entry_point: str
    implementation: Mapping[str, Any]

    def __post_init__(self):
        from jsonschema import Draft7Validator, ValidationError
        try:
            Draft7Validator(account_provider_schema_document()).validate(self.to_dict())
        except (TypeError, ValidationError) as exc:
            raise ValueError("invalid installed account provider selection") from exc
        for value, pattern in (
                (self.entry_point, r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"),
                (self.implementation["implementation_id"], r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}"),
                (self.implementation["revision"], r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"),
                (self.implementation["handler"], r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")):
            if re.fullmatch(pattern, value) is None:
                raise ValueError("invalid installed account provider selection")
        object.__setattr__(self, "implementation", frozen(self.implementation))

    @staticmethod
    def schema_document():
        return account_provider_schema_document()

    def to_dict(self):
        return {"schema_version": PROVIDER_SCHEMA, "entry_point": self.entry_point,
                "implementation": json_copy(self.implementation)}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {"schema_version", "entry_point", "implementation"}
                or value.get("schema_version") != PROVIDER_SCHEMA):
            raise ValueError("invalid installed account provider selection")
        return cls(value["entry_point"], value["implementation"])


def load_account_provider(selection):
    """Resolve a fixed installed factory; never import the JSON handler field.

    Loading installed code is an explicit HOST selection. This function does
    not call the factory, query an account service or inspect credential bytes.
    Original managed service construction and dispatch retain those boundaries.
    """
    if not isinstance(selection, AccountProviderSelection):
        selection = AccountProviderSelection.from_dict(selection)
    try:
        matches = tuple(entry for entry in metadata.entry_points(group=PROVIDER_ENTRY_POINT_GROUP)
                        if entry.name == selection.entry_point)
        if len(matches) != 1:
            raise AccountCredentialError("account_provider_unavailable")
        factory = matches[0].load()
        actual = implementation_identity(factory, identity_version="v2")
        if canonical(actual) != canonical(selection.implementation):
            raise AccountCredentialError("account_provider_identity_mismatch")
        return factory
    except AccountCredentialError:
        raise
    except Exception:
        raise AccountCredentialError("account_provider_unavailable") from None


__all__ = ("PROVIDER_SCHEMA", "PROVIDER_ENTRY_POINT_GROUP", "AccountProviderSelection",
           "account_provider_schema_document", "load_account_provider")
