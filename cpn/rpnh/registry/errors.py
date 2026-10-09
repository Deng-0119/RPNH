"""Typed failures at the native D1-C resource boundary."""


class ResourceServiceError(RuntimeError):
    """Base class for fail-closed resource-service faults."""


class UnknownResourceVersion(ResourceServiceError):
    pass


class MissingResourceAddress(ResourceServiceError):
    pass


class AmbiguousResourceAddress(ResourceServiceError):
    pass


class UnauthorizedResourceDelivery(ResourceServiceError):
    pass


class StaleInvocationContext(ResourceServiceError):
    pass


class StaleAuthorityHead(ResourceServiceError):
    pass


class StaleWriterFence(ResourceServiceError):
    pass


class ResourceIdempotencyConflict(ResourceServiceError):
    pass


class ResourceSchemaViolation(ResourceServiceError):
    pass


class ResourcePayloadSchemaViolation(ResourceSchemaViolation):
    """Provider-authored resource bytes violate their declared schema."""


class ResourceIntegrityFault(ResourceServiceError):
    pass


class UnresolvedStrongReference(ResourceServiceError):
    pass


class ResourceAddressConflict(ResourceServiceError):
    pass


class WorkspaceMaterializationFault(ResourceServiceError):
    pass


class PathPublicationFault(ResourceServiceError):
    pass


class StaleQueryContext(ResourceServiceError):
    pass


class DeliveryOutcomeUnknown(ResourceServiceError):
    pass


class DeliveryAcknowledgementConflict(ResourceServiceError):
    pass


class ReleaseAuthorizationDenied(ResourceServiceError):
    pass


class ReleaseWitnessConsumed(ResourceServiceError):
    pass


class UnmonitorableDeliveryBoundary(ResourceServiceError):
    pass


class UndeclaredPublicationOrigin(ResourceServiceError):
    pass


class WrongNetOutputBinding(ResourceServiceError):
    pass


class LeafReturnConflict(ResourceServiceError):
    pass


class RunAlreadyExists(ResourceServiceError):
    pass


class NotNativeRun(ResourceServiceError):
    pass


class IncompleteNativeRun(ResourceServiceError):
    pass


class UnknownRunLayout(ResourceServiceError):
    pass


class TerminalReadIncomplete(ResourceServiceError):
    """A bounded terminal read did not finish; no semantic result was proven."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        super().__init__(reason + (": " + detail if detail else ""))


class TerminalReadUnsupported(ResourceServiceError):
    """Valid historical composition outside the terminal reader's support."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        super().__init__(reason + (": " + detail if detail else ""))


class TerminalReadStale(ResourceServiceError):
    """An explicitly changed source, Registry cut, or writer requires a reread."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        super().__init__(reason + (": " + detail if detail else ""))
