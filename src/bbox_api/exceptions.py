"""Exceptions raised by the Bbox API client.

Typical mapping in an application: ``BboxInvalidCredentialsError`` /
``BboxSessionExpiredError`` -> "authentication failed", ``BboxConnectionError``
/ ``BboxTimeoutError`` -> "retry later / not ready".
"""

from __future__ import annotations

from typing import Any


class BboxApiError(Exception):
    """Base exception for all Bbox API errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        """Initialize the error with an optional HTTP status and API error list."""
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.errors = errors or []


class BboxConnectionError(BboxApiError):
    """The router could not be reached (DNS failure, refused connection...)."""


class BboxTimeoutError(BboxApiError):
    """A request to the router timed out."""

    def __init__(self, message: str = "Request to the Bbox timed out") -> None:
        """Initialize the timeout error."""
        super().__init__(message)


class BboxAuthenticationError(BboxApiError):
    """Authentication with the router failed or is missing."""


class BboxInvalidCredentialsError(BboxAuthenticationError):
    """The admin password is wrong (HTTP 401 on /login)."""

    def __init__(self, message: str = "Invalid Bbox admin password") -> None:
        """Initialize the invalid credentials error."""
        super().__init__(message, status_code=401)


class BboxSessionExpiredError(BboxAuthenticationError):
    """The session cookie expired and re-authentication did not succeed."""

    def __init__(self, message: str = "Bbox session expired") -> None:
        """Initialize the session expired error."""
        super().__init__(message, status_code=401)


class BboxRateLimitError(BboxApiError):
    """Too many login attempts: the router temporarily blocks authentication."""

    def __init__(self, message: str = "Too many login attempts, rate limited") -> None:
        """Initialize the rate limit error."""
        super().__init__(message, status_code=429)


class BboxNotFoundError(BboxApiError):
    """The requested endpoint does not exist on this router/firmware."""

    def __init__(self, message: str = "Endpoint not found on the Bbox") -> None:
        """Initialize the not found error."""
        super().__init__(message, status_code=404)
