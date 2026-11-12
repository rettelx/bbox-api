"""Typed data models for the Bbox router API responses.

All models are deliberately tolerant: unknown keys are preserved in ``raw``,
missing or malformed keys fall back to the field default, so a firmware update
changing the API payload is unlikely to break parsing. API keys are mapped to
snake_case attributes through the ``alias`` field metadata.
"""

from __future__ import annotations

import dataclasses
import types
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from typing import Any, Self, Union, cast, get_args, get_origin, get_type_hints


def fix_mojibake(value: str) -> str:
    """Repair strings mis-decoded by the router (UTF-8 sent as latin-1)."""
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return value


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _to_int(value: Any) -> int:
    return int(float(value))


def _to_float(value: Any) -> float:
    return float(value)


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)  # raises ValueError on garbage
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _coerce(hint: Any, value: Any) -> Any:
    """Coerce a raw API value to the declared field type.

    Raises ValueError/TypeError when the value cannot be coerced, so the
    caller can fall back to the field default instead of crashing.
    """
    if value is None or hint is Any:
        return value

    origin = get_origin(hint)

    if origin in (list, tuple):
        args = get_args(hint)
        inner = args[0] if args else Any
        if not isinstance(value, (list, tuple)):
            raise TypeError(f"expected a list, got {type(value).__name__}")
        return [_coerce(inner, item) for item in value]

    if origin is dict:
        args = get_args(hint)
        inner = args[1] if len(args) == 2 else Any
        if not isinstance(value, dict):
            raise TypeError(f"expected a dict, got {type(value).__name__}")
        return {key: _coerce(inner, item) for key, item in value.items()}

    if origin is Union or isinstance(hint, types.UnionType):
        for arg in get_args(hint):
            if arg is type(None):
                continue
            try:
                return _coerce(arg, value)
            except (ValueError, TypeError, KeyError):
                continue
        raise ValueError(f"cannot coerce {value!r} to {hint}")

    if dataclasses.is_dataclass(hint):
        if not isinstance(value, dict):
            name = getattr(hint, "__name__", hint)
            raise TypeError(f"expected a dict for {name}, got {type(value).__name__}")
        return cast("type[BboxModel]", hint).from_dict(value)

    if hint is bool:
        return _to_bool(value)
    if hint is int:
        return _to_int(value)
    if hint is float:
        return _to_float(value)
    if hint is datetime:
        return _to_datetime(value)
    if hint is str:
        text = value if isinstance(value, str) else str(value)
        fixed = fix_mojibake(text)
        return fixed if fixed else None  # the API uses "" for "no value"
    return value


@cache
def _type_hints(cls: Any) -> dict[str, Any]:
    return get_type_hints(cls)


def _field_default(f: dataclasses.Field[Any]) -> Any:
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:
        return f.default_factory()
    return None


@dataclass
class BboxModel:
    """Base model: tolerant ``from_dict`` plus the untouched raw payload."""

    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Build a model from a raw API payload.

        Unknown keys are kept in ``raw``; missing keys keep their default;
        keys that fail to coerce also fall back to the default.
        """
        if not isinstance(data, dict):
            raise TypeError(f"{cls.__name__}.from_dict expects a dict, got {type(data).__name__}")
        hints = _type_hints(cast(Any, cls))
        kwargs: dict[str, Any] = {"raw": dict(data)}
        for f in dataclasses.fields(cls):
            if f.name == "raw":
                continue
            alias = f.metadata.get("alias")
            if alias is not None and alias in data:
                value = data[alias]
            elif f.name in data:
                value = data[f.name]
            else:
                continue
            try:
                kwargs[f.name] = _coerce(hints.get(f.name, Any), value)
            except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
                kwargs[f.name] = _field_default(f)
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# /device
# ---------------------------------------------------------------------------


@dataclass
class FirmwareVersion(BboxModel):
    """Firmware version block of the router."""

    version: str | None = None
    date: datetime | None = None


@dataclass
class ConnectionFlags(BboxModel):
    """WAN connection types supported by the router ('using' block)."""

    ipv4: bool = False
    ipv6: bool = False
    ftth: bool = False
    adsl: bool = False
    vdsl: bool = False


@dataclass
class Display(BboxModel):
    """Front panel display state."""

    luminosity: int = 0
    luminosity_extender: int = 0
    state: str | None = None
    orientation: int | None = None


@dataclass
class DeviceInfo(BboxModel):
    """Router information, from GET /device."""

    modelname: str | None = None
    modelclass: str | None = None
    serialnumber: str | None = None
    status: int = 0
    numberofboots: int = 0
    uptime: int = 0
    now: datetime | None = None
    firstusedate: datetime | None = None
    main: FirmwareVersion | None = None
    running: FirmwareVersion | None = None
    reco: FirmwareVersion | None = None
    spl: FirmwareVersion | None = None
    tpl: FirmwareVersion | None = None
    ldr1: FirmwareVersion | None = None
    ldr2: FirmwareVersion | None = None
    display: Display | None = None
    using: ConnectionFlags | None = None

    @property
    def firmware_version(self) -> str | None:
        """Return the running firmware version, if known."""
        if self.running and self.running.version:
            return self.running.version
        return self.main.version if self.main else None


# ---------------------------------------------------------------------------
# /hosts
# ---------------------------------------------------------------------------


@dataclass
class IPv6Address(BboxModel):
    """An IPv6 address of a host."""

    ipaddress: str | None = None
    status: str | None = None
    lastseen: datetime | None = None
    lastscan: datetime | None = None


@dataclass
class EthernetInfo(BboxModel):
    """Ethernet link details of a host."""

    physicalport: int = 0
    logicalport: int = 0
    speed: int = 0  # Mbps
    mode: str | None = None


@dataclass
class WirelessInfo(BboxModel):
    """Wi-Fi link details of a host."""

    band: float | None = None
    rssi_dbm: int | None = field(default=None, metadata={"alias": "rssi0"})
    rate: int = 0  # Mbps
    mcs: int = 0
    tx_usage: int = field(default=0, metadata={"alias": "txUsage"})
    rx_usage: int = field(default=0, metadata={"alias": "rxUsage"})
    estimatedRate: int = 0

    @property
    def rssi(self) -> int | None:
        """Return the RSSI in dBm."""
        return self.rssi_dbm


@dataclass
class Host(BboxModel):
    """A device connected to the Bbox, from GET /hosts (device tracker)."""

    id: int = 0
    active: bool = False
    hostname: str | None = None
    ipaddress: str | None = None
    macaddress: str | None = None
    type: str | None = None  # DHCP / Static
    link: str | None = None  # Ethernet, Wifi 2.4, Wifi 5...
    lease: int = 0
    firstseen: datetime | None = None
    lastseen: int = 0
    devicetype: str | None = None
    duid: str | None = None
    guest: bool = False
    me: bool = False
    serial_number: str | None = field(default=None, metadata={"alias": "serialNumber"})
    ip6address: list[IPv6Address] = field(default_factory=list)
    ethernet: EthernetInfo | None = None
    wireless: WirelessInfo | None = None

    @property
    def is_connected(self) -> bool:
        """Return True if the host is currently active on the LAN."""
        return self.active


# ---------------------------------------------------------------------------
# /wan/ip and /wan/ip/stats
# ---------------------------------------------------------------------------


@dataclass
class WANStats(BboxModel):
    """Traffic counters of one direction (rx or tx) of the WAN interface."""

    packets: int = 0
    bytes: int = 0
    packetserrors: int = 0
    packetsdiscards: int = 0
    occupation: int = 0  # percent
    bandwidth: int = 0  # kbit/s currently used
    max_bandwidth: int = field(default=0, metadata={"alias": "maxBandwidth"})  # kbit/s
    contractual_bandwidth: int = field(default=0, metadata={"alias": "contractualBandwidth"})

    @property
    def bandwidth_mbps(self) -> float:
        """Current bandwidth in Mbit/s."""
        return self.bandwidth / 1000

    @property
    def max_bandwidth_mbps(self) -> float:
        """Maximum bandwidth in Mbit/s."""
        return self.max_bandwidth / 1000

    @property
    def contractual_bandwidth_mbps(self) -> float:
        """Contractual bandwidth in Mbit/s."""
        return self.contractual_bandwidth / 1000


@dataclass
class WANIPStats(BboxModel):
    """WAN IP statistics, from GET /wan/ip/stats (bandwidth sensors)."""

    rx: WANStats = field(default_factory=WANStats)
    tx: WANStats = field(default_factory=WANStats)


@dataclass
class WANIP(BboxModel):
    """WAN IP configuration, from GET /wan/ip (the ``wan.ip`` block)."""

    ipaddress: str | None = field(default=None, metadata={"alias": "address"})
    subnet: str | None = None
    gateway: str | None = None
    state: str | None = None
    dnsservers: str | None = None  # comma-separated string on this API
    dnsserversv6: str | None = None
    mac: str | None = None
    mtu: int = 0
    portrange: str | None = None
    cgnatenable: bool = False
    maptenable: bool = False
    ip6state: str | None = None
    ip6address: list[IPv6Address] = field(default_factory=list)
    ip6prefix: list[dict[str, Any]] = field(default_factory=list)
    # Filled by the client from the sibling blocks wan.link / wan.internet:
    link_type: str | None = None
    internet_state: int | None = None


# ---------------------------------------------------------------------------
# /wireless
# ---------------------------------------------------------------------------


@dataclass
class WirelessBand(BboxModel):
    """One Wi-Fi band SSID configuration (keyed '24', '5', '6', 'guest').

    Warning: ``raw`` contains the ``security`` block, which includes the
    Wi-Fi passphrase in clear text. Never expose ``raw`` in logs or API
    responses.
    """

    id: str | None = None  # SSID name
    enable: bool = False
    hidden: bool = False
    bssid: str | None = None


@dataclass
class WirelessRadio(BboxModel):
    """One Wi-Fi band radio state (keyed '24', '5', '6', 'guest')."""

    enable: bool = False
    state: int = 0
    standard: str | None = None
    channel: int = 0
    current_channel: int | None = None
    current_bandwidth: int | None = None


@dataclass
class Wireless(BboxModel):
    """Wi-Fi configuration, from GET /wireless (the ``wireless`` block).

    ``bands`` / ``radios`` are keyed by band name as returned by the API
    ('24', '5', '6', 'guest').
    """

    status: str | None = None
    bands: dict[str, WirelessBand] = field(default_factory=dict, metadata={"alias": "ssid"})
    radios: dict[str, WirelessRadio] = field(default_factory=dict, metadata={"alias": "radio"})

    def band(self, name: str) -> WirelessBand | None:
        """Return the SSID configuration of one band ('24', '5', '6'...)."""
        return self.bands.get(name)

    def is_enabled(self, band: str) -> bool | None:
        """Return whether the radio of one band is enabled, if known."""
        radio = self.radios.get(band)
        return radio.enable if radio else None
