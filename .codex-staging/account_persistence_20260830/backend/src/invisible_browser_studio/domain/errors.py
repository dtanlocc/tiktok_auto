class DomainError(ValueError):
    """Base error for domain invariant violations."""


class InvalidStateTransition(DomainError):
    pass


class InvalidUrl(DomainError):
    pass

