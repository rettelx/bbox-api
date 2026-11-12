"""Tests for the async BboxClient."""

from __future__ import annotations

import asyncio

import aiohttp
import pytest
from aioresponses import aioresponses

from bbox_api import (
    BboxApiError,
    BboxClient,
    BboxConnectionError,
    BboxInvalidCredentialsError,
    BboxRateLimitError,
)

from .conftest import (
    DEVICE_PAYLOAD,
    HOSTS_PAYLOAD,
    WAN_IP_STATS_PAYLOAD,
    api_url,
    calls_for,
    register_auth,
)


async def test_context_manager_logs_in_and_get_device(mock_api: aioresponses) -> None:
    mock_api.get(api_url("device"), payload=DEVICE_PAYLOAD)

    async with BboxClient("secret") as client:
        device = await client.get_device()

    assert device.modelname == "F@st5696b"
    assert device.serialnumber == "LK23456789012"
    assert device.uptime == 123456
    assert device.numberofboots == 42
    assert device.firmware_version == "25.5.28"
    assert device.using is not None and device.using.ftth is True
    assert device.raw["unknown_future_field"] == {"foo": "bar"}

    login_calls = calls_for(mock_api, "POST", "login")
    assert len(login_calls) == 1
    assert login_calls[0]["data"] == {"password": "secret", "remember": "1"}


async def test_btoken_is_sent_on_api_calls(mock_api: aioresponses) -> None:
    mock_api.get(api_url("device"), payload=DEVICE_PAYLOAD)

    client = BboxClient("secret")
    await client.login()
    await client.get_device()
    await client.close()

    device_calls = calls_for(mock_api, "GET", "device")
    assert len(device_calls) == 1
    assert device_calls[0]["params"]["btoken"] == "tok-123"


async def test_token_is_fetched_once_and_cached(mock_api: aioresponses) -> None:
    mock_api.get(api_url("device"), payload=DEVICE_PAYLOAD, repeat=True)
    mock_api.get(api_url("summary"), payload={"state": "ok"}, repeat=True)

    client = BboxClient("secret")
    await client.login()
    await client.get_device()
    await client.get_summary()
    await client.close()

    assert len(calls_for(mock_api, "GET", "device/token")) == 1


async def test_get_hosts(mock_api: aioresponses) -> None:
    mock_api.get(api_url("hosts"), payload=HOSTS_PAYLOAD)

    async with BboxClient("secret") as client:
        hosts = await client.get_hosts()

    assert len(hosts) == 2  # guest block excluded by default
    iphone = hosts[0]
    assert iphone.active is True
    assert iphone.is_connected is True
    assert iphone.hostname == "iPhone-Xavier"
    assert iphone.macaddress == "AA:BB:CC:DD:EE:FF"
    assert iphone.ipaddress == "192.168.1.10"
    assert iphone.wireless is not None and iphone.wireless.rssi == -55
    tv = hosts[1]
    assert tv.active is False
    assert tv.hostname == "Café-TV"  # mojibake fixed
    assert tv.ethernet is not None and tv.ethernet.speed == 1000


async def test_get_hosts_include_guests_and_get_guests(mock_api: aioresponses) -> None:
    mock_api.get(api_url("hosts"), payload=HOSTS_PAYLOAD, repeat=True)

    async with BboxClient("secret") as client:
        all_hosts = await client.get_hosts(include_guests=True)
        guests = await client.get_guests()

    assert len(all_hosts) == 3
    assert len(guests) == 1
    assert guests[0].guest is True
    assert guests[0].hostname == "Guest-Phone"
    assert guests[0].ipaddress == "10.0.0.11"
    assert guests[0].link == "Wifi 5Guest"


async def test_get_hosts_multi_element_envelope(mock_api: aioresponses) -> None:
    # Real F@st5688b behaviour: /hosts returns [lan_object, guest_object]
    mock_api.get(
        api_url("hosts"),
        payload=[
            {
                "hosts": {
                    "list": [
                        {
                            "id": 1,
                            "active": 1,
                            "guest": 0,
                            "hostname": "lan-host",
                            "macaddress": "aa",
                        }
                    ]
                },
                "downloadThreshold": 0,
                "wirelesshosts": [],
                "extenderhosts": [],
            },
            {
                "hosts": {
                    "list": [
                        {
                            "id": 2,
                            "active": 1,
                            "guest": 1,
                            "hostname": "guest-host",
                            "macaddress": "bb",
                        }
                    ]
                }
            },
        ],
        repeat=True,
    )

    async with BboxClient("secret") as client:
        lan_hosts = await client.get_hosts()
        guests = await client.get_guests()

    assert [h.hostname for h in lan_hosts] == ["lan-host"]
    assert [h.hostname for h in guests] == ["guest-host"]


async def test_get_wan_ip_stats(mock_api: aioresponses) -> None:
    mock_api.get(api_url("wan/ip/stats"), payload=WAN_IP_STATS_PAYLOAD)

    async with BboxClient("secret") as client:
        stats = await client.get_wan_ip_stats()

    assert stats.rx.bandwidth == 940500
    assert stats.rx.bandwidth_mbps == pytest.approx(940.5)
    assert stats.rx.max_bandwidth_mbps == pytest.approx(8000.0)
    assert stats.tx.max_bandwidth_mbps == pytest.approx(1000.0)


async def test_set_wireless_sends_global_put_with_token(mock_api: aioresponses) -> None:
    # Verified on F@st5688b fw 25.1.22: PUT /wireless radio.enable toggles all main bands
    mock_api.put(api_url("wireless"), status=200, payload={})

    client = BboxClient("secret")
    await client.login()
    await client.set_wireless(False)
    await client.close()

    calls = calls_for(mock_api, "PUT", "wireless")
    assert len(calls) == 1
    assert calls[0]["params"]["radio.enable"] == 0
    assert calls[0]["params"]["btoken"] == "tok-123"


async def test_set_guest_wifi_uses_enable_param(mock_api: aioresponses) -> None:
    # Verified on F@st5688b fw 25.1.22: PUT /wireless/guestenable enable=0/1
    mock_api.put(api_url("wireless/guestenable"), status=200, payload={})

    client = BboxClient("secret")
    await client.login()
    await client.set_guest_wifi(False)
    await client.close()

    calls = calls_for(mock_api, "PUT", "wireless/guestenable")
    assert len(calls) == 1
    assert calls[0]["params"]["enable"] == 0
    assert calls[0]["params"]["btoken"] == "tok-123"


async def test_get_wan_ip_real_envelope(mock_api: aioresponses) -> None:
    mock_api.get(
        api_url("wan/ip"),
        payload=[
            {
                "wan": {
                    "internet": {"state": 2},
                    "link": {"state": "Up", "type": "sfp"},
                    "ip": {
                        "address": "176.191.153.87",
                        "state": "Up",
                        "mtu": 1500,
                        "mac": "94:98:8f:29:0e:b0",
                    },
                }
            }
        ],
    )

    async with BboxClient("secret") as client:
        wan = await client.get_wan_ip()

    assert wan.ipaddress == "176.191.153.87"
    assert wan.state == "Up"
    assert wan.mtu == 1500
    assert wan.link_type == "sfp"
    assert wan.internet_state == 2


async def test_get_wireless_real_envelope(mock_api: aioresponses) -> None:
    mock_api.get(
        api_url("wireless"),
        payload=[
            {
                "wireless": {
                    "status": "Configured",
                    "radio": {"5": {"enable": 1, "current_channel": 64}},
                    "ssid": {"5": {"id": "MySSID", "enable": 1, "bssid": "72:98:8F:29:0E:BD"}},
                }
            }
        ],
    )

    async with BboxClient("secret") as client:
        wireless = await client.get_wireless()

    assert wireless.status == "Configured"
    assert wireless.is_enabled("5") is True
    assert wireless.band("5").id == "MySSID"


async def test_get_hosts_me_real_envelope(mock_api: aioresponses) -> None:
    mock_api.get(
        api_url("hosts/me"),
        payload={"host": {"id": 9, "hostname": "my-laptop", "macaddress": "a0:36:bc:2b:52:e5"}},
    )

    async with BboxClient("secret") as client:
        me = await client.get_hosts_me()

    assert me.hostname == "my-laptop"
    assert me.macaddress == "a0:36:bc:2b:52:e5"


async def test_get_call_log_auto_detects_line(mock_api: aioresponses) -> None:
    # Real F@st5688b behaviour: GET /voip returns lines with an id,
    # the call log lives at /voip/fullcalllog/<id> as {"calllog": [...]}
    mock_api.get(
        api_url("voip"),
        payload=[{"voip": [{"id": 1, "status": "Disabled", "uri": "+33...@fai.bouygtel.net"}]}],
    )
    mock_api.get(
        api_url("voip/fullcalllog/1"),
        payload=[{"calllog": [{"id": 42, "number": "+33...", "type": "incoming"}]}],
    )

    async with BboxClient("secret") as client:
        log = await client.get_call_log()

    assert log == [{"id": 42, "number": "+33...", "type": "incoming"}]
    assert len(calls_for(mock_api, "GET", "voip/fullcalllog/1")) == 1


async def test_reboot(mock_api: aioresponses) -> None:
    mock_api.post(api_url("device/reboot"), status=200, payload={})

    async with BboxClient("secret") as client:
        await client.reboot()

    assert len(calls_for(mock_api, "POST", "device/reboot")) == 1


async def test_invalid_credentials() -> None:
    with aioresponses() as mocked:
        register_auth(mocked, login_status=401)
        client = BboxClient("wrong")
        with pytest.raises(BboxInvalidCredentialsError):
            await client.login()


async def test_rate_limited_login() -> None:
    with aioresponses() as mocked:
        register_auth(mocked, login_status=429)
        client = BboxClient("secret")
        with pytest.raises(BboxRateLimitError):
            await client.login()


async def test_expired_session_triggers_single_relogin() -> None:
    with aioresponses() as mocked:
        register_auth(mocked)
        mocked.get(
            api_url("device"),
            status=401,
            payload={"exception": {"errors": [{"reason": "not authenticated"}]}},
        )
        mocked.get(api_url("device"), payload=DEVICE_PAYLOAD)

        client = BboxClient("secret")
        await client.login()
        device = await client.get_device()
        await client.close()

    assert device.modelname == "F@st5696b"
    assert len(calls_for(mocked, "POST", "login")) == 2


async def test_connection_error_on_get_is_retried_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("bbox_api.client._RETRY_DELAY", 0)
    with aioresponses() as mocked:
        register_auth(mocked)
        mocked.get(
            api_url("device"), exception=aiohttp.ClientConnectionError("refused"), repeat=True
        )
        client = BboxClient("secret")
        await client.login()
        with pytest.raises(BboxConnectionError):
            await client.get_device()
        await client.close()
    # 1 initial attempt + 2 retries (MAX_RETRIES=3)
    assert len(calls_for(mocked, "GET", "device")) == 3


async def test_connection_error_on_get_recovers_on_retry(
    mock_api: aioresponses, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bbox_api.client._RETRY_DELAY", 0)
    mock_api.get(api_url("device"), exception=aiohttp.ClientConnectionError("flaky"))
    mock_api.get(api_url("device"), payload=DEVICE_PAYLOAD)

    async with BboxClient("secret") as client:
        device = await client.get_device()

    assert device.modelname == "F@st5696b"
    assert len(calls_for(mock_api, "GET", "device")) == 2


async def test_connection_error_on_post_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("bbox_api.client._RETRY_DELAY", 0)
    with aioresponses() as mocked:
        register_auth(mocked)
        mocked.post(
            api_url("device/reboot"), exception=aiohttp.ClientConnectionError("flaky"), repeat=True
        )
        client = BboxClient("secret")
        await client.login()
        with pytest.raises(BboxConnectionError):
            await client.reboot()
        await client.close()
    assert len(calls_for(mocked, "POST", "device/reboot")) == 1


async def test_redirect_raises_clear_error(mock_api: aioresponses) -> None:
    # http://bbox.lan answers 302 -> https://mabbox.bytel.fr on real boxes
    mock_api.get(
        api_url("device"),
        status=302,
        headers={"Location": "https://mabbox.bytel.fr/api/v1/device"},
    )

    async with BboxClient("secret") as client:
        with pytest.raises(BboxApiError, match="redirect") as exc_info:
            await client.get_device()

    assert exc_info.value.status_code == 302


async def test_concurrent_login_happens_once() -> None:
    with aioresponses() as mocked:
        register_auth(mocked, login_repeat=False)  # a second login would 404
        client = BboxClient("secret")
        await asyncio.gather(client.login(), client.login(), client.login())
        assert len(calls_for(mocked, "POST", "login")) == 1
        await client.close()


async def test_expired_token_is_renewed() -> None:
    with aioresponses() as mocked:
        mocked.post(api_url("login"), status=200, payload={}, repeat=True)
        # token already expired -> every call must fetch a new one
        mocked.get(
            api_url("device/token"),
            payload=[{"device": {"token": "tok-old", "expires": "2020-01-01T00:00:00+00:00"}}],
            repeat=True,
        )
        mocked.get(api_url("device"), payload=DEVICE_PAYLOAD, repeat=True)
        client = BboxClient("secret")
        await client.login()
        await client.get_device()
        await client.get_device()
        await client.close()
    assert len(calls_for(mocked, "GET", "device/token")) == 2


async def test_shared_session_is_not_closed() -> None:
    with aioresponses() as mocked:
        register_auth(mocked)
        mocked.get(api_url("device"), payload=DEVICE_PAYLOAD)
        session = aiohttp.ClientSession()
        client = BboxClient("secret", session=session)
        await client.login()
        await client.get_device()
        await client.close()
        assert not session.closed
        assert client._session is None
        await session.close()


async def test_context_manager_closes_session_on_login_failure() -> None:
    with aioresponses() as mocked:
        register_auth(mocked, login_status=401)
        client = BboxClient("wrong")
        with pytest.raises(BboxInvalidCredentialsError):
            async with client:
                pass
        assert client._session is None


async def test_unparseable_token_expiry_uses_fallback_ttl() -> None:
    with aioresponses() as mocked:
        mocked.post(api_url("login"), status=200, payload={}, repeat=True)
        mocked.get(
            api_url("device/token"),
            payload=[{"device": {"token": "tok-x", "expires": "not-a-date"}}],
            repeat=True,
        )
        mocked.get(api_url("device"), payload=DEVICE_PAYLOAD, repeat=True)
        client = BboxClient("secret")
        await client.login()
        await client.get_device()
        await client.get_device()
        await client.close()
    # fallback TTL (60s) applies: the token is NOT refetched on every call
    assert len(calls_for(mocked, "GET", "device/token")) == 1


async def test_bom_prefixed_json_body_is_parsed(mock_api: aioresponses) -> None:
    import json as _json

    body = b"\xef\xbb\xbf" + _json.dumps(DEVICE_PAYLOAD).encode()
    mock_api.get(api_url("device"), body=body, content_type="application/json")

    async with BboxClient("secret") as client:
        device = await client.get_device()

    assert device.modelname == "F@st5696b"


async def test_path_is_normalized(mock_api: aioresponses) -> None:
    mock_api.get(api_url("device"), payload=DEVICE_PAYLOAD)

    async with BboxClient("secret") as client:
        device = await client.raw_request("/device/")  # leading/trailing slashes

    assert device["device"]["modelname"] == "F@st5696b"


async def test_invalid_constructor_arguments() -> None:
    with pytest.raises(ValueError, match="host"):
        BboxClient("pw", host="")
    with pytest.raises(ValueError, match="port"):
        BboxClient("pw", port=70000)
    with pytest.raises(ValueError, match="timeout"):
        BboxClient("pw", timeout=0)


async def test_temporary_api_error_is_retried(
    mock_api: aioresponses, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bbox_api.client._RETRY_DELAY", 0)
    mock_api.get(
        api_url("summary"),
        status=500,
        payload={"exception": {"errors": [{"reason": "Cannot extract request parameters"}]}},
    )
    mock_api.get(api_url("summary"), payload={"state": "ok"})

    async with BboxClient("secret") as client:
        summary = await client.get_summary()

    assert summary == {"state": "ok"}
    assert len(calls_for(mock_api, "GET", "summary")) == 2


async def test_empty_password_rejected() -> None:
    with pytest.raises(ValueError, match="password"):
        BboxClient("")


async def test_remote_factory_uses_wan_ip_without_ssl_verify() -> None:
    # Remote access goes through the WAN IP, not mabbox.bytel.fr (LAN-only,
    # the box hijacks DNS for it). The box cert is not valid for an IP.
    client = BboxClient.remote("secret", "203.0.113.10")
    assert client._base_url == "https://203.0.113.10/api/v1"
    assert client._verify_ssl is False
    await client.close()
