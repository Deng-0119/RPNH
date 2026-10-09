"""Public synthetic credential implementation, observed as an installed asset.

The immediate secret is supplied at runtime. No value, locator, environment or
private configuration is stored in this public implementation asset.
"""
from cpn.rpnh.public_material_contracts import canonical
from cpn.llm_adapters.public_credentials import bearer_secret_slot
from cpn.llm_adapters._external_provider_credentials import CredentialResolutionError


class SyntheticCredentialStore:
    def __init__(self, *, binding, secret, counts):
        self.binding_bytes = canonical(binding)
        self.secret = secret
        self.counts = counts

    def resolve(self, binding):
        self.counts['resolver'] = self.counts.get('resolver', 0) + 1
        if canonical(binding) != self.binding_bytes:
            raise CredentialResolutionError()
        return self.secret

    def render(self, secret):
        self.counts['renderer'] = self.counts.get('renderer', 0) + 1
        return bearer_secret_slot(secret)
