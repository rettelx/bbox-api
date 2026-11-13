"""Async Python client for the Bouygues Telecom Bbox router API."""

from .client import BboxClient
from .exceptions import (
    BboxApiError,
    BboxAuthenticationError,
    BboxConnectionError,
    BboxInvalidCredentialsError,
    BboxNotFoundError,
    BboxRateLimitError,
    BboxSessionExpiredError,
    BboxTimeoutError,
)
from .models import (
    WANIP,
    BboxModel,
    ConnectionFlags,
    DeviceInfo,
    Display,
    EthernetInfo,
    FirmwareVersion,
    Host,
    IPv6Address,
    WANIPStats,
    WANStats,
    Wireless,
    WirelessBand,
    WirelessInfo,
    WirelessRadio,
)

__version__ = "0.1.2"

__all__ = [
    "WANIP",
    "BboxApiError",
    "BboxAuthenticationError",
    "BboxClient",
    "BboxConnectionError",
    "BboxInvalidCredentialsError",
    "BboxModel",
    "BboxNotFoundError",
    "BboxRateLimitError",
    "BboxSessionExpiredError",
    "BboxTimeoutError",
    "ConnectionFlags",
    "DeviceInfo",
    "Display",
    "EthernetInfo",
    "FirmwareVersion",
    "Host",
    "IPv6Address",
    "WANIPStats",
    "WANStats",
    "Wireless",
    "WirelessBand",
    "WirelessInfo",
    "WirelessRadio",
    "__version__",
]
