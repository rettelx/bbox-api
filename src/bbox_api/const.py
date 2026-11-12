"""Constants for the Bbox router API."""

API_PATH = "api/v1"

#: Default hostname of a Bbox on the local network.
DEFAULT_HOST = "bbox.lan"

#: Default IP address of a Bbox on the local network
#: (192.168.1.1 on recent models, 192.168.1.254 on older ones).
DEFAULT_IP = "192.168.1.1"

#: Alternate hostname the Bbox answers on the LAN: the router hijacks DNS for
#: it (mabbox.bytel.fr.lan -> its own LAN IP) and serves a valid certificate
#: for this name. It only works from the local network, NOT from the internet.
MABBOX_HOST = "mabbox.bytel.fr"

#: Remote (WAN) access goes through the router's public IP, not mabbox.bytel.fr:
#: https://<wan-ip>/api/v1, with the "remote access" option enabled on the box.
#: Discover the WAN IP from the LAN with ``get_wan_ip().ipaddress``.

DEFAULT_TIMEOUT = 10

#: Conservative validity assumed for a btoken when the router omits or
#: returns an unparseable "expires" (real tokens live ~20 min).
TOKEN_FALLBACK_TTL = 60

#: Endpoints that must be called without a ``btoken`` query parameter.
NO_TOKEN_ENDPOINTS = frozenset({"login", "logout", "device/token"})

#: Reasons returned by the API in its error envelope that are worth retrying.
TEMPORARY_ERROR_REASONS = frozenset({"cannot extract request parameters"})
