"""Tests for the data models."""

from __future__ import annotations

from datetime import datetime

import pytest

from bbox_api.models import (
    WANIP,
    DeviceInfo,
    Host,
    WANIPStats,
    Wireless,
    fix_mojibake,
)


def test_fix_mojibake() -> None:
    assert fix_mojibake("CafÃ©") == "Café"
    assert fix_mojibake("already fine") == "already fine"


def test_device_info_from_dict() -> None:
    device = DeviceInfo.from_dict(
        {
            "modelname": "F@st5696b",
            "uptime": "123456",  # string coerced to int
            "numberofboots": 42,
            "main": {"version": "25.5.28", "date": "2025-05-01T00:00:00+02:00"},
            "running": {"version": "25.5.28"},
            "using": {"ftth": 1, "ipv4": True},
            "some_new_field": 123,
        }
    )
    assert device.modelname == "F@st5696b"
    assert device.uptime == 123456
    assert device.firmware_version == "25.5.28"
    assert device.using is not None
    assert device.using.ftth is True
    assert device.using.adsl is False
    assert device.main is not None and isinstance(device.main.date, datetime)
    assert device.raw["some_new_field"] == 123


def test_device_info_missing_fields_get_defaults() -> None:
    device = DeviceInfo.from_dict({})
    assert device.modelname is None
    assert device.uptime == 0
    assert device.firmware_version is None


def test_host_from_dict_coercions() -> None:
    host = Host.from_dict(
        {
            "id": "7",
            "active": 1,
            "hostname": "CafÃ©-TV",
            "macaddress": "11:22:33:44:55:66",
            "ip6address": [{"ipaddress": "fe80::1", "status": "Preferred"}],
            "wireless": {"band": "5", "rssi0": "-55"},
        }
    )
    assert host.id == 7
    assert host.active is True
    assert host.hostname == "Café-TV"
    assert host.ip6address[0].ipaddress == "fe80::1"
    assert host.wireless is not None
    assert host.wireless.band == 5.0
    assert host.wireless.rssi == -55


def test_wan_ip_stats_from_dict() -> None:
    stats = WANIPStats.from_dict(
        {
            "rx": {"bandwidth": 940500, "maxBandwidth": 8000000, "occupation": "12"},
            "tx": {"bandwidth": 12000, "maxBandwidth": 1000000},
        }
    )
    assert stats.rx.bandwidth_mbps == pytest.approx(940.5)
    assert stats.rx.occupation == 12
    assert stats.tx.max_bandwidth_mbps == pytest.approx(1000.0)


def test_wan_ip_real_shape() -> None:
    # Real payload of GET /wan/ip on a F@st5688b (fw 25.1.22)
    wan = WANIP.from_dict(
        {
            "address": "176.191.153.87",
            "state": "Up",
            "gateway": "",  # empty string means "no value" -> None
            "dnsservers": "",
            "dnsserversv6": "2001:860:b0ff:1::1,2001:860:b0ff:1::2",
            "subnet": "",
            "mac": "94:98:8f:29:0e:b0",
            "mtu": 1500,
            "ip6state": "Up",
            "ip6address": [{"ipaddress": "2001:861:3013:abaf::1", "status": "Preferred"}],
            "link_type": "sfp",
            "internet_state": 2,
        }
    )
    assert wan.ipaddress == "176.191.153.87"
    assert wan.gateway is None
    assert wan.dnsservers is None
    assert wan.dnsserversv6 == "2001:860:b0ff:1::1,2001:860:b0ff:1::2"
    assert wan.mac == "94:98:8f:29:0e:b0"
    assert wan.mtu == 1500
    assert wan.ip6address[0].ipaddress == "2001:861:3013:abaf::1"
    assert wan.link_type == "sfp"
    assert wan.internet_state == 2


def test_wireless_real_shape() -> None:
    # Real payload of GET /wireless on a F@st5688b (fw 25.1.22), security block removed
    wireless = Wireless.from_dict(
        {
            "status": "Configured",
            "radio": {
                "24": {"enable": 1, "state": 1, "standard": "11ax", "current_channel": 5},
                "5": {
                    "enable": 1,
                    "state": 1,
                    "standard": "11ax",
                    "current_channel": 64,
                    "current_bandwidth": 160,
                },
                "guest": {"enable": 1},
            },
            "ssid": {
                "24": {"id": "MySSID", "enable": 1, "hidden": 0, "bssid": "62:98:8F:29:0E:BB"},
                "5": {"id": "MySSID", "enable": 1, "hidden": 0, "bssid": "72:98:8F:29:0E:BD"},
            },
        }
    )
    assert wireless.status == "Configured"
    assert set(wireless.bands) == {"24", "5"}
    assert set(wireless.radios) == {"24", "5", "guest"}
    assert wireless.band("5") is not None
    assert wireless.band("5").id == "MySSID"
    assert wireless.band("5").bssid == "72:98:8F:29:0E:BD"
    assert wireless.is_enabled("5") is True
    assert wireless.is_enabled("6") is None
    assert wireless.radios["5"].current_bandwidth == 160


def test_from_dict_rejects_non_dict() -> None:
    with pytest.raises(TypeError):
        Host.from_dict(["not", "a", "dict"])  # type: ignore[arg-type]


def test_malformed_values_fall_back_to_defaults() -> None:
    # A firmware change must never crash parsing: bad values -> field default
    host = Host.from_dict(
        {
            "id": "not-a-number",
            "lease": "garbage",
            "active": "maybe",  # unrecognised bool string -> False
            "ip6address": "not-a-list",
            "firstseen": "not a date",
            "serialNumber": "SN123",
        }
    )
    assert host.id == 0
    assert host.lease == 0
    assert host.active is False
    assert host.ip6address == []
    assert host.firstseen is None
    assert host.serial_number == "SN123"


def test_wan_stats_snake_case_aliases() -> None:
    stats = WANIPStats.from_dict(
        {
            "rx": {"bandwidth": 940500, "maxBandwidth": 8000000, "contractualBandwidth": 8000000},
            "tx": {"bandwidth": 12000, "maxBandwidth": 1000000},
        }
    )
    assert stats.rx.max_bandwidth == 8000000
    assert stats.rx.contractual_bandwidth == 8000000
    assert stats.rx.max_bandwidth_mbps == pytest.approx(8000.0)
    assert stats.rx.contractual_bandwidth_mbps == pytest.approx(8000.0)


def test_empty_string_becomes_none() -> None:
    host = Host.from_dict({"hostname": "", "macaddress": "11:22:33:44:55:66"})
    assert host.hostname is None
    assert host.macaddress == "11:22:33:44:55:66"


def test_negative_string_int_coercion() -> None:
    # Real payloads contain e.g. "lease": "-353857"
    host = Host.from_dict({"lease": "-353857", "active": 0})
    assert host.lease == -353857
    assert host.active is False
