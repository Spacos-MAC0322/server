"""Integration failures independent of HTTP and external client libraries."""


class ExternalIntegrationError(Exception):
    """Base failure; service is an internal identifier, never a URL or credential."""

    def __init__(
        self,
        service: str,
        *,
        status_code: int | None = None,
        reason: str | None = None,
        page: int | None = None,
        attempts: int | None = None,
    ) -> None:
        self.service = service
        self.status_code = status_code
        self.reason = reason
        self.page = page
        self.attempts = attempts
        super().__init__(f"External integration failed: {service}")


class ExternalServiceUnavailable(ExternalIntegrationError):
    """The external service cannot be reached or is unavailable."""


class ExternalServiceTimeout(ExternalIntegrationError):
    """The external service did not respond within the configured deadline."""


class ExternalServiceInvalidResponse(ExternalIntegrationError):
    """The external response cannot be used as required by the adapter contract."""
