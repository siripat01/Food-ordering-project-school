class DomainError(Exception):
    """Base error safe to map to an HTTP response."""


class InvalidInputError(DomainError):
    pass


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class ForbiddenError(DomainError):
    pass
