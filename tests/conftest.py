"""Shared fixtures: a mocked Bbox router API."""

from __future__ import annotations

import re
from typing import Any

import pytest
from aioresponses import aioresponses

BASE = "http://bbox.lan/api/v1"

DEVICE_PAYLOAD = [
    {
        "device": {
            "now": "2026-11-12T10:00:00+01:00",
            "status": 0,
            "numberofboots": 42,
            "modelname": "F@st5696b",
            "modelclass": "Bouygues Telecom",
            "serialnumber": "LK23456789012",
            "uptime": 123456,
            "firstusedate": "2024-01-15T08:30:00+01:00",
            "main": {"version": "25.5.28", "date": "2025-05-01T00:00:00+02:00"},
            "running": {"version": "25.5.28", "date": "2025-05-01T00:00:00+02:00"},
            "reco": {"version": "25.4.10", "date": None},
            "display": {"luminosity": 3, "luminosity_extender": 0, "state": "on"},
            "using": {"ipv4": True, "ipv6": True, "ftth": True, "adsl": False, "vdsl": False},
            "unknown_future_field": {"foo": "bar"},
        }
    }
]

HOSTS_PAYLOAD = [
    {
        "hosts": {
            "list": [
                {
                    "id": 1,
                    "active": True,
                    "hostname": "iPhone-Xavier",
                    "ipaddress": "192.168.1.10",
                    "macaddress": "AA:BB:CC:DD:EE:FF",
                    "type": "DHCP",
                    "link": "Wifi 5",
                    "lease": 3600,
                    "firstseen": "2026-11-01T12:00:00+01:00",
                    "lastseen": 5,
                    "devicetype": "smartphone",
                    "guest": False,
                    "wireless": {"band": 5, "rssi0": -55, "rate": 866, "mcs": 9},
                },
                {
                    "id": 2,
                    "active": False,
                    "hostname": "CafÃ©-TV",
                    "ipaddress": "192.168.1.20",
                    "macaddress": "11:22:33:44:55:66",
                    "type": "DHCP",
                    "link": "Ethernet",
                    "lease": 0,
                    "lastseen": 86400,
                    "ethernet": {
                        "physicalport": 1,
                        "logicalport": 1,
                        "speed": 1000,
                        "mode": "Full",
                    },
                },
            ]
        }
    },
    # Second envelope block on real boxes: the guest network hosts
    {
        "hosts": {
            "list": [
                {
                    "id": 1,
                    "active": True,
                    "guest": 1,
                    "hostname": "Guest-Phone",
                    "ipaddress": "10.0.0.11",
                    "macaddress": "DE:AD:BE:EF:00:01",
                    "link": "Wifi 5Guest",
                },
            ]
        }
    },
]

WAN_IP_STATS_PAYLOAD = [
    {
        "wan": {
            "ip": {
                "stats": {
                    "rx": {
                        "packets": 1000,
                        "bytes": 2000000,
                        "packetserrors": 0,
                        "packetsdiscards": 0,
                        "occupation": 12,
                        "bandwidth": 940500,
                        "maxBandwidth": 8000000,
                        "contractualBandwidth": 8000000,
                    },
                    "tx": {
                        "packets": 500,
                        "bytes": 100000,
                        "packetserrors": 0,
                        "packetsdiscards": 0,
                        "occupation": 1,
                        "bandwidth": 12000,
                        "maxBandwidth": 1000000,
                        "contractualBandwidth": 1000000,
                    },
                }
            }
        }
    }
]

TOKEN_PAYLOAD = [{"device": {"token": "tok-123", "expires": "2099-01-01T00:00:00+00:00"}}]


def api_url(path: str) -> re.Pattern[str]:
    """Match an API endpoint regardless of its query string (btoken...)."""
    return re.compile(re.escape(f"{BASE}/{path}") + r"(\?.*)?$")


def calls_for(mocked: aioresponses, method: str, path: str) -> list[dict[str, Any]]:
    """Return the recorded kwargs of every request made to method+path."""
    target = f"{BASE}/{path}"
    return [
        call.kwargs
        for (req_method, req_url), req_calls in mocked.requests.items()
        if req_method == method and str(req_url).split("?")[0] == target
        for call in req_calls
    ]


def register_auth(
    mocked: aioresponses, *, login_status: int = 200, login_repeat: bool = True
) -> None:
    """Register the login and token endpoints on a mocked API."""
    mocked.post(
        api_url("login"),
        status=login_status,
        headers={"Set-Cookie": "BBOX_ID=session123; Path=/"},
        payload={} if login_status == 200 else {"exception": {"errors": [{"reason": "denied"}]}},
        repeat=login_repeat,
    )
    mocked.get(api_url("device/token"), payload=TOKEN_PAYLOAD, repeat=True)


@pytest.fixture
def mock_api() -> aioresponses:
    """A mocked Bbox API with successful authentication."""
    with aioresponses() as mocked:
        register_auth(mocked)
        yield mocked
