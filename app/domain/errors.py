class DomainError(Exception):
    """A business rule violation that can be returned to an API consumer."""


class ConflictError(DomainError):
    pass


class NotFoundError(DomainError):
    pass


class ValidationError(DomainError):
    pass
