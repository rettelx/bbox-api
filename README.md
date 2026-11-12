# bbox-api

Async client for the **Bouygues Telecom Bbox router API** (`/api/v1`), for any
asyncio app. Python >= 3.11, MIT, single dependency (`aiohttp`), fully typed
(`py.typed`, mypy-clean) with fault-tolerant dataclass models (unknown or
malformed payload keys never crash parsing; unknown keys stay in `.raw`).

- **Local**: `BboxClient(pw)` → `http://bbox.lan/api/v1` (also `192.168.1.1` or
  `mabbox.bytel.fr` with `use_tls=True` — that name resolves only on the LAN,
  the box hijacks DNS for it). **Remote**: `BboxClient.remote(pw, wan_ip)` →
  `https://<wan-ip>/api/v1` ("remote access" enabled on the box; the WAN IP is
  refused from the LAN itself).
- **Auth is automatic**: admin password → session cookie + cached `btoken`,
  re-auth on 401, retries on transient errors.

## Install

```bash
pip install bbox-api
```

## Usage

```python
import asyncio
from bbox_api import BboxClient


async def main():
    async with BboxClient("admin-password") as client:  # local: bbox.lan
        device = await client.get_device()  # model, firmware, uptime
        stats = await client.get_wan_ip_stats()  # rx/tx bandwidth_mbps
        hosts = await client.get_hosts()  # connected devices
        await client.set_wireless(False)  # all main Wi-Fi bands off


asyncio.run(main())
```

Any endpoint not wrapped by a typed method: `await client.raw_request("alerts")`.
To share your own `aiohttp.ClientSession`, pass `session=` — the client then
never closes it.

## Errors

All inherit `BboxApiError`: `BboxInvalidCredentialsError`, `BboxSessionExpiredError`, `BboxRateLimitError`, `BboxNotFoundError`, `BboxConnectionError`, `BboxTimeoutError`.

## Verified endpoints (live-tested on a F@st5688b, fw 25.1.22)

GET endpoints returning HTTP 200, grouped by area:

- **device**: `device`, `device/cpu`, `device/mem`, `device/summary`, `device/log`, `device/led`
- **hosts**: `hosts`, `hosts/me`, `alerts`
- **wan**: `wan/ip`, `wan/ip/stats`, `wan/ftth/stats`, `wan/diags`, `cpl`
- **wireless**: `wireless`, `wireless/24`, `wireless/5`, `wireless/6`, `wireless/guest`,
  `wireless/24/stats`, `wireless/5/stats`, `wireless/6/stats`, `wireless/wps`,
  `wireless/repeater`, `wireless/scheduler`
- **lan / misc**: `summary`, `lan/ip`, `lan/stats`, `iptv`, `services`, `speedtest`,
  `parentalcontrol`, `voip`, `voip/fullcalllog/<line-id>` (line id from `GET /voip`,
  e.g. `voip/fullcalllog/1`)

GET endpoints returning 404 on this model/firmware (they raise `BboxNotFoundError`):

- `device/optimization`, `wan/ftth`, `wan/xdsl`, `wan/xdsl/stats`, `wan/cable`
  (this is a FTTH box), `lan/ip6`, `ddns`, `remote`, `voip/voicemail`,
  `voip/callforward`, `voip/fullcalllog` without a line id

Writes verified (PUT/POST/DELETE with `btoken`):

- `PUT /wireless` + `radio.enable=0/1` — toggles all main bands (2.4/5/6 GHz) at once
- `PUT /wireless/guestenable` + `enable=0/1` — guest Wi-Fi
- `POST` / `DELETE /wireless/wps` — start/stop a WPS session

Behavior notes from live testing:

- `/hosts` returns a **list of blocks**: the first is the main LAN, the following
  ones are e.g. the guest network (entries flagged `guest=1`, 10.0.0.x subnet).
  The guest block stays present even when guest Wi-Fi is off (its hosts just show
  `active=False`). `get_hosts()` parses all blocks and excludes guests unless
  `include_guests=True`; `get_guests()` returns only guest hosts.

Security warning: `GET /wireless` returns the **Wi-Fi passphrase in clear text**
(`raw["bands"]["24"]["security"]["passphrase"]`). Never expose a model's `raw`
dict in logs or API responses.

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m pytest        # 44 tests, mocked API (aiohttp<3.14: aioresponses limit)
.venv/Scripts/ruff check .
.venv/Scripts/python -m mypy
```

CI/CD (`.github/workflows/`): `ci.yml` runs pytest on Python 3.11–3.14 plus
ruff and mypy on every push/PR; `release.yml` builds the wheel/sdist with
hatchling and publishes to PyPI on `v*` tags via trusted publishing (OIDC —
configure the `bbox-api` project on PyPI with this repo as trusted publisher,
no API token needed).

## License

MIT
