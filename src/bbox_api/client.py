"""Async client for the Bouygues Telecom Bbox router API.

The router exposes a local REST API under ``/api/v1`` (see
https://api.bbox.fr/doc/apirouter/index.html). Authentication works with the
admin password: ``POST /api/v1/login`` sets a session cookie (``BBOX_ID``),
and every other call must carry a short-lived ``btoken`` query parameter
issued by ``GET /api/v1/device/token``.

Typical usage::

    async with BboxClient("admin-password") as client:
        device = await client.get_device()
        hosts = await client.get_hosts()

To share an existing aiohttp session (e.g. one managed by your application),
pass it with ``session=`` — the client then never closes it::

    client = BboxClient(password, host="192.168.1.1", session=my_session)
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Self

import aiohttp

from .const import (
    API_PATH,
    DEFAULT_HOST,
    DEFAULT_TIMEOUT,
    NO_TOKEN_ENDPOINTS,
    TEMPORARY_ERROR_REASONS,
    TOKEN_FALLBACK_TTL,
)
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
from .models import WANIP, DeviceInfo, Host, WANIPStats, Wireless

_LOGGER = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY = 1.0


def _unwrap(data: Any) -> Any:
    """Unwrap the API envelope: payloads are wrapped in a list of objects
    (e.g. ``hosts`` returns one object for the LAN and one for extenders),
    the first object carries the main payload."""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return data


class BboxClient:
    """Async client for a Bbox router."""

    def __init__(
        self,
        password: str,
        host: str = DEFAULT_HOST,
        *,
        use_tls: bool = False,
        port: int | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        session: aiohttp.ClientSession | None = None,
        verify_ssl: bool = True,
    ) -> None:
        """Initialize the client.

        Args:
            password: Router admin password (the one of the web interface).
            host: Router hostname or IP, e.g. ``bbox.lan`` or ``192.168.1.1``.
            use_tls: Use HTTPS instead of HTTP (required for remote access and
                for ``mabbox.bytel.fr``).
            port: Optional TCP port override.
            timeout: Request timeout in seconds.
            session: Optional shared aiohttp session. If omitted, one is
                created and closed by the client.
            verify_ssl: Verify TLS certificates when ``use_tls`` is enabled.

        Raises:
            ValueError: If the password is empty.
        """
        if not password:
            raise ValueError("password must not be empty")
        if not host:
            raise ValueError("host must not be empty")
        if port is not None and not 1 <= port <= 65535:
            raise ValueError(f"invalid port: {port}")
        if timeout <= 0:
            raise ValueError(f"timeout must be > 0, got {timeout}")
        self._password = password
        scheme = "https" if use_tls else "http"
        netloc = f"{host}:{port}" if port else host
        self._origin = f"{scheme}://{netloc}"
        self._base_url = f"{self._origin}/{API_PATH}"
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        self._session = session
        self._owns_session = session is None
        self._authenticated = False
        self._btoken: str | None = None
        self._btoken_expires: datetime | None = None
        # Serialise login and token renewal: concurrent calls must not
        # trigger several logins (the router rate-limits them with HTTP 429).
        self._auth_lock = asyncio.Lock()
        self._token_lock = asyncio.Lock()

    def __del__(self) -> None:
        """Warn if an owned session was never closed."""
        try:
            if self._session is not None and self._owns_session and not self._session.closed:
                _LOGGER.warning(
                    "BboxClient for %s was garbage-collected with an "
                    "unclosed session; use 'async with' or close()",
                    self._origin,
                )
        except Exception:
            pass

    @classmethod
    def remote(cls, password: str, wan_ip: str, **kwargs: Any) -> Self:
        """Create a client for remote (WAN) access through the router's public IP.

        Requires the "remote access" option enabled on the router and only
        works from outside the LAN — from the LAN, use the default host
        (``bbox.lan`` / ``mabbox.bytel.fr`` / its LAN IP) instead; the WAN IP
        is typically refused (no NAT hairpinning).

        The router's certificate is issued for ``mabbox.bytel.fr``, not for
        its IP, so TLS verification is disabled by default here.

        Discover the WAN IP from the LAN first:
        ``wan_ip = (await client.get_wan_ip()).ipaddress``.
        """
        kwargs.pop("host", None)
        kwargs.pop("use_tls", None)
        kwargs.setdefault("verify_ssl", False)
        return cls(password, host=wan_ip, use_tls=True, **kwargs)

    # ------------------------------------------------------------------
    # Session / authentication
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        """Authenticate and return the client (closes the session on failure)."""
        try:
            await self.login()
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *_exc_info: Any) -> None:
        """Close the underlying HTTP session if owned by this client."""
        await self.close()

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            # unsafe=True is required so the session cookie is kept when the
            # router is reached by IP address instead of a hostname.
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    family=socket.AF_INET, use_dns_cache=True, verify_ssl=self._verify_ssl
                ),
                cookie_jar=aiohttp.CookieJar(unsafe=True),
            )
            self._owns_session = True
        return self._session

    async def login(self) -> None:
        """Authenticate against the router with the admin password.

        Safe to call concurrently and repeatedly: only one login request is
        ever in flight.

        Raises:
            BboxInvalidCredentialsError: Wrong password.
            BboxRateLimitError: Too many attempts, the router blocks login.
            BboxConnectionError: Router unreachable.
            BboxTimeoutError: Router did not answer in time.
        """
        if self._authenticated:
            return
        async with self._auth_lock:
            if self._authenticated:
                return
            await self.request(
                "POST",
                "login",
                data={"password": self._password, "remember": "1"},
                skip_token=True,
            )
            self._authenticated = True
            _LOGGER.debug("Authenticated to %s", self._origin)

    async def logout(self) -> None:
        """Invalidate the session on the router."""
        try:
            await self.request("POST", "logout", skip_token=True)
        finally:
            self._authenticated = False
            async with self._token_lock:
                self._btoken = None
                self._btoken_expires = None

    async def close(self) -> None:
        """Close the HTTP session if this client owns it, and detach from it.

        A shared (injected) session is never closed; the client simply stops
        using it and will create its own session on the next request.
        """
        if self._session is not None and self._owns_session:
            await self._session.close()
        self._session = None
        self._owns_session = False
        self._authenticated = False

    def _token_valid(self) -> bool:
        return (
            self._btoken is not None
            and self._btoken_expires is not None
            and datetime.now(UTC) < self._btoken_expires
        )

    async def _get_token(self) -> str:
        """Return a valid ``btoken``, fetching or renewing it if needed."""
        if self._token_valid():
            return self._btoken  # type: ignore[return-value]

        await self.login()
        async with self._token_lock:
            if self._token_valid():  # another coroutine renewed it meanwhile
                return self._btoken  # type: ignore[return-value]
            data = _unwrap(await self.request("GET", "device/token", skip_token=True))
            device = data.get("device", {}) if isinstance(data, dict) else {}
            token = device.get("token")
            if not token:
                raise BboxApiError("Router did not return a btoken")
            self._btoken = str(token)
            expires = device.get("expires")
            try:
                self._btoken_expires = (
                    datetime.fromisoformat(str(expires).replace("Z", "+00:00")) if expires else None
                )
            except ValueError:
                self._btoken_expires = None
            if self._btoken_expires is None:
                # Missing/unparseable expiry: assume a short conservative TTL
                # instead of refetching the token on every single call.
                self._btoken_expires = datetime.now(UTC) + timedelta(seconds=TOKEN_FALLBACK_TTL)
            return self._btoken

    # ------------------------------------------------------------------
    # Low-level request
    # ------------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: Any = None,
        skip_token: bool = False,
        _retried_auth: bool = False,
        _attempt: int = 1,
    ) -> Any:
        """Perform a request against the router API and return parsed JSON.

        This is the escape hatch for endpoints not covered by the typed
        methods of this client.

        Raises:
            BboxApiError: (or a subclass) on any API/transport failure.
        """
        session = self._ensure_session()
        path = path.strip("/")
        url = f"{self._base_url}/{path}"

        if not skip_token and path not in NO_TOKEN_ENDPOINTS:
            await self.login()
            params = {**(params or {}), "btoken": await self._get_token()}

        headers = {"Referer": f"{self._base_url}/", "Origin": self._origin}

        try:
            async with asyncio.timeout(self._timeout):
                async with session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    data=data,
                    headers=headers,
                    allow_redirects=False,
                    ssl=self._verify_ssl,
                ) as response:
                    body = await response.read()

                    if 300 <= response.status < 400:
                        # e.g. http://bbox.lan redirects to https://mabbox.bytel.fr
                        location = response.headers.get("Location", "?")
                        raise BboxApiError(
                            f"Router answered {response.status} redirect to {location} "
                            f"on {path}; check host/use_tls (mabbox.bytel.fr is LAN-only)",
                            status_code=response.status,
                        )

                    if response.status == 401 and path != "login" and not _retried_auth:
                        # Session cookie expired: re-authenticate and retry once.
                        _LOGGER.debug("401 on %s, re-authenticating", path)
                        self._authenticated = False
                        async with self._token_lock:
                            self._btoken = None
                            self._btoken_expires = None
                        await self.login()
                        return await self.request(
                            method,
                            path,
                            params=params,
                            json_body=json_body,
                            data=data,
                            skip_token=skip_token,
                            _retried_auth=True,
                            _attempt=_attempt,
                        )

                    if response.status >= 400:
                        error = self._build_error(response.status, body, path)
                        if _attempt < _MAX_RETRIES and any(
                            str(err.get("reason", "")).lower() in TEMPORARY_ERROR_REASONS
                            for err in error.errors
                        ):
                            _LOGGER.debug(
                                "Temporary API error on %s, retry %s/%s",
                                path,
                                _attempt,
                                _MAX_RETRIES,
                            )
                            await asyncio.sleep(_RETRY_DELAY)
                            return await self.request(
                                method,
                                path,
                                params=params,
                                json_body=json_body,
                                data=data,
                                skip_token=skip_token,
                                _retried_auth=_retried_auth,
                                _attempt=_attempt + 1,
                            )
                        raise error

                    return self._parse_body(body)
        except TimeoutError as err:
            return await self._retry_or_raise(
                method,
                path,
                BboxTimeoutError(f"Timeout after {self._timeout}s on {method} {path}"),
                err,
                params,
                json_body,
                data,
                skip_token,
                _retried_auth,
                _attempt,
            )
        except aiohttp.ClientError as err:
            return await self._retry_or_raise(
                method,
                path,
                BboxConnectionError(f"Connection error on {method} {path}: {err}"),
                err,
                params,
                json_body,
                data,
                skip_token,
                _retried_auth,
                _attempt,
            )

    async def _retry_or_raise(
        self,
        method: str,
        path: str,
        error: BboxApiError,
        cause: BaseException,
        params: dict[str, Any] | None,
        json_body: Any,
        data: Any,
        skip_token: bool,
        retried_auth: bool,
        attempt: int,
    ) -> Any:
        """Retry idempotent GETs on transport errors, raise otherwise."""
        if method.upper() == "GET" and attempt < _MAX_RETRIES:
            _LOGGER.debug("Transport error on GET %s, retry %s/%s", path, attempt, _MAX_RETRIES)
            await asyncio.sleep(_RETRY_DELAY)
            return await self.request(
                method,
                path,
                params=params,
                json_body=json_body,
                data=data,
                skip_token=skip_token,
                _retried_auth=retried_auth,
                _attempt=attempt + 1,
            )
        raise error from cause

    @staticmethod
    def _parse_body(body: bytes) -> Any:
        text = body.decode("utf-8", errors="replace").lstrip("\ufeff")
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _build_error(self, status: int, body: bytes, path: str) -> BboxApiError:
        """Translate an HTTP error into the matching exception."""
        detail: dict[str, Any] = {}
        try:
            parsed = json.loads(body.decode("utf-8", errors="replace"))
            if isinstance(parsed, dict) and isinstance(parsed.get("exception"), dict):
                detail = parsed["exception"]
        except json.JSONDecodeError:
            pass

        errors = detail.get("errors")
        if not isinstance(errors, list):
            errors = []
        reasons = [
            str(err.get("reason")) for err in errors if isinstance(err, dict) and err.get("reason")
        ]
        suffix = f": {', '.join(reasons)}" if reasons else ""
        message = f"Bbox API error on {path} (HTTP {status}){suffix}"

        if status == 401:
            if path == "login":
                return BboxInvalidCredentialsError()
            return BboxSessionExpiredError(message)
        if status == 403:
            return BboxAuthenticationError(message, status_code=status, errors=errors)
        if status == 429:
            return BboxRateLimitError(message)
        if status == 404:
            return BboxNotFoundError(message)
        return BboxApiError(message, status_code=status, errors=errors)

    async def raw_request(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        """Call any API endpoint and return the JSON payload.

        The API wraps most payloads in a list of objects; the first object
        (the main payload) is returned. Endpoints that genuinely return a
        list of entries should be called through :meth:`request` instead.
        """
        return _unwrap(await self.request(method, path, params=params, json_body=json_body))

    # ------------------------------------------------------------------
    # Device
    # ------------------------------------------------------------------

    async def get_device(self) -> DeviceInfo:
        """Return router information (GET /device)."""
        data = _unwrap(await self.request("GET", "device"))
        return DeviceInfo.from_dict(data.get("device", data))

    async def reboot(self) -> None:
        """Reboot the router (POST /device/reboot)."""
        await self.request("POST", "device/reboot")

    async def get_device_cpu(self) -> dict[str, Any]:
        """Return CPU usage details (GET /device/cpu)."""
        return _unwrap(await self.request("GET", "device/cpu"))

    async def get_device_memory(self) -> dict[str, Any]:
        """Return memory usage details (GET /device/mem)."""
        return _unwrap(await self.request("GET", "device/mem"))

    # ------------------------------------------------------------------
    # Hosts (device tracker)
    # ------------------------------------------------------------------

    async def get_hosts(self, *, include_guests: bool = False) -> list[Host]:
        """Return the devices known to the router (GET /hosts).

        The payload is a list of blocks: the first block is the main LAN,
        the following ones are e.g. the guest network (their entries are
        flagged ``guest=1``). Guest hosts are excluded unless
        ``include_guests`` is True. Verified on F@st5688b fw 25.1.22: the
        guest block stays present even when the guest Wi-Fi is disabled
        (its hosts then show ``active=False``).
        """
        data = await self.request("GET", "hosts")
        elements = data if isinstance(data, list) else [data]
        hosts: list[Host] = []
        for element in elements:
            if not isinstance(element, dict):
                continue
            block = element.get("hosts")
            entries = block.get("list", []) if isinstance(block, dict) else []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                host = Host.from_dict(entry)
                if host.guest and not include_guests:
                    continue
                hosts.append(host)
        return hosts

    async def get_guests(self) -> list[Host]:
        """Return the hosts connected through the guest network (10.0.0.x)."""
        return [host for host in await self.get_hosts(include_guests=True) if host.guest]

    async def get_hosts_me(self) -> Host:
        """Return the router itself as a host (GET /hosts/me, key ``host``)."""
        data = _unwrap(await self.request("GET", "hosts/me"))
        me: Any = data.get("host") if isinstance(data, dict) else None
        if not isinstance(me, dict) and isinstance(data, dict):
            hosts = data.get("hosts")
            me = hosts.get("me") if isinstance(hosts, dict) else None
        if not isinstance(me, dict):
            me = {}
        return Host.from_dict(me)

    # ------------------------------------------------------------------
    # WAN
    # ------------------------------------------------------------------

    async def get_wan_ip(self) -> WANIP:
        """Return the WAN IP configuration (GET /wan/ip, block ``wan.ip``)."""
        data = _unwrap(await self.request("GET", "wan/ip"))
        wan = data.get("wan", {}) if isinstance(data, dict) else {}
        if not isinstance(wan, dict):
            wan = {}
        ip = dict(wan.get("ip") or {})
        link = wan.get("link")
        internet = wan.get("internet")
        if isinstance(link, dict):
            ip.setdefault("link_type", link.get("type"))
        if isinstance(internet, dict):
            ip.setdefault("internet_state", internet.get("state"))
        return WANIP.from_dict(ip)

    async def get_wan_ip_stats(self) -> WANIPStats:
        """Return WAN bandwidth statistics (GET /wan/ip/stats)."""
        data = _unwrap(await self.request("GET", "wan/ip/stats"))
        wan = data.get("wan") if isinstance(data, dict) else None
        ip = wan.get("ip") if isinstance(wan, dict) else None
        stats = ip.get("stats") if isinstance(ip, dict) else None
        if not isinstance(stats, dict):
            stats = ip if isinstance(ip, dict) else {}
        return WANIPStats.from_dict(stats)

    async def get_wan_ftth_stats(self) -> dict[str, Any]:
        """Return FTTH line statistics (GET /wan/ftth/stats)."""
        return _unwrap(await self.request("GET", "wan/ftth/stats"))

    async def get_wan_xdsl_stats(self) -> dict[str, Any]:
        """Return xDSL line statistics (GET /wan/xdsl/stats)."""
        return _unwrap(await self.request("GET", "wan/xdsl/stats"))

    # ------------------------------------------------------------------
    # Wireless
    # ------------------------------------------------------------------

    async def get_wireless(self) -> Wireless:
        """Return the Wi-Fi configuration (GET /wireless, block ``wireless``).

        Warning: the raw payload of this endpoint contains the Wi-Fi
        passphrase in clear text (``security.passphrase``); never expose
        ``raw`` to end users.
        """
        data = _unwrap(await self.request("GET", "wireless"))
        block = data.get("wireless", data) if isinstance(data, dict) else {}
        return Wireless.from_dict(block if isinstance(block, dict) else {})

    async def get_wireless_stats(self, band: Literal["24", "5", "6"]) -> dict[str, Any]:
        """Return Wi-Fi statistics for one band (GET /wireless/<band>/stats)."""
        return _unwrap(await self.request("GET", f"wireless/{band}/stats"))

    async def set_wireless(self, enabled: bool) -> None:
        """Enable or disable the main Wi-Fi radios (PUT /wireless, radio.enable).

        Verified on F@st5688b fw 25.1.22: this toggles all main bands
        (2.4/5/6 GHz) at once and leaves the guest network untouched.
        """
        await self.request("PUT", "wireless", params={"radio.enable": int(enabled)})

    async def set_guest_wifi(self, enabled: bool) -> None:
        """Enable or disable the guest Wi-Fi (PUT /wireless/guestenable, enable).

        Verified on F@st5688b fw 25.1.22: the ``enable`` parameter is the
        one that works on recent firmwares (``radio.enable`` has no effect).
        """
        await self.request("PUT", "wireless/guestenable", params={"enable": int(enabled)})

    async def get_wps(self) -> dict[str, Any]:
        """Return the WPS state (GET /wireless/wps)."""
        return _unwrap(await self.request("GET", "wireless/wps"))

    async def start_wps(self) -> None:
        """Start a WPS session (POST /wireless/wps)."""
        await self.request("POST", "wireless/wps")

    async def stop_wps(self) -> None:
        """Stop the WPS session (DELETE /wireless/wps)."""
        await self.request("DELETE", "wireless/wps")

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    async def get_summary(self) -> dict[str, Any]:
        """Return the router summary (GET /summary)."""
        return _unwrap(await self.request("GET", "summary"))

    async def get_lan_ip(self) -> dict[str, Any]:
        """Return the LAN IP configuration (GET /lan/ip)."""
        return _unwrap(await self.request("GET", "lan/ip"))

    async def get_voip(self) -> dict[str, Any]:
        """Return the VoIP state (GET /voip)."""
        return _unwrap(await self.request("GET", "voip"))

    async def get_call_log(self, line: str | None = None) -> list[dict[str, Any]]:
        """Return the VoIP call log (GET /voip/fullcalllog/<line>).

        If ``line`` is omitted, the first line declared by GET /voip is used
        (verified on F@st5688b: lines carry an ``id``, the call log lives at
        ``voip/fullcalllog/<id>`` and returns ``{"calllog": [...]}``).
        """
        if line is None:
            voip = _unwrap(await self.request("GET", "voip"))
            lines = voip.get("voip", []) if isinstance(voip, dict) else voip
            if isinstance(lines, list) and lines and isinstance(lines[0], dict):
                line = str(lines[0].get("id", "1"))
            else:
                line = "1"
        data = _unwrap(await self.request("GET", f"voip/fullcalllog/{line}"))
        if isinstance(data, dict) and isinstance(data.get("calllog"), list):
            return data["calllog"]
        if isinstance(data, dict):
            entries = data.get("voip", {})
            entries = entries.get("fullcalllog", []) if isinstance(entries, dict) else entries
            return entries if isinstance(entries, list) else [entries]
        return data if isinstance(data, list) else [data]
