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
