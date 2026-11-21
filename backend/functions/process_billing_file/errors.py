class BillingValidationError(ValueError):
    """Raised when an uploaded billing file is invalid."""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or [message]
