"""Read-only Microsoft Graph metadata poller; never requests message bodies.

Source identity is a heuristic, NOT authentication. No email, subject, body,
attachments, OTP, Steam Guard or login links reach the rest of the application.
"""
from datetime import datetime, timezone
from urllib.parse import urlparse
import re

GRAPH_ROOT = "https://graph.microsoft.com"
GRAPH_PATH = "/v1.0/me/mailFolders/inbox/messages"
MAX_PAGES = 20
MAX_MESSAGES = 1000

PLATFORMS = {
    "steam": ("steampowered.com", "steamcommunity.com"),
    "epic": ("epicgames.com",),
    "ea": ("ea.com",),
    "ubisoft": ("ubisoft.com",),
    "battlenet": ("battle.net", "blizzard.com"),
    "gog": ("gog.com",),
}


class GraphError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__("Ошибка Microsoft Graph, HTTP " + str(status))


def classify(address):
    """A hint from the *untrusted* From header, not sender verification."""
    if type(address) is not str or not 3 <= len(address) <= 320 or address.count("@") != 1:
        return None
    local, _, domain = address.rpartition("@")
    if not local or not domain.isascii():
        return None
    domain = domain.lower().rstrip(".")
    for name, domains in PLATFORMS.items():
        if any(domain == d or domain.endswith("." + d) for d in domains):
            return name
    return None


def received_epoch(raw):
    if type(raw) is not str or len(raw) > 64:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return int(dt.timestamp())
    except (ValueError, OverflowError):
        return None


def safe_graph_url(url):
    """Only the fixed HTTPS Graph inbox messages endpoint incl. nextLink."""
    if type(url) is not str or len(url) > 4096:
        return False
    try:
        p = urlparse(url)
        return (p.scheme == "https" and p.hostname == "graph.microsoft.com"
                and p.port in (None, 443) and not p.username and not p.password
                and p.path == GRAPH_PATH and not p.fragment)
    except ValueError:
        return False


def _default_get(url, token):
    try:
        import requests
    except ImportError:
        raise GraphError("missing-requests") from None
    try:
        response = requests.get(
            url, headers={"Authorization": "Bearer " + token,
                          "Accept": "application/json"},
            timeout=(8, 20), allow_redirects=False)
    except requests.RequestException:
        raise GraphError("network") from None
    if response.status_code != 200:
        raise GraphError(response.status_code)
    if len(response.content) > 2_000_000:
        raise GraphError("large-response")
    try:
        return response.json()
    except (ValueError, TypeError):
        raise GraphError("bad-json") from None


def fetch_since(token, from_epoch, *, http_get=None):
    """Fully collect a bounded interval. On truncation FAIL without checkpoint.

    Caller only saves checkpoint after every page is retrieved and validated.
    A too-busy mailbox requires a narrower interval or a later delta API.
    """
    if type(token) is not str or not token or type(from_epoch) is not int or from_epoch < 0:
        raise GraphError("invalid-args")
    start = datetime.fromtimestamp(from_epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Graph expects ISO dates and indexable filter/order combination.
    url = (GRAPH_ROOT + GRAPH_PATH +
           "?%24select=id%2CreceivedDateTime%2Cfrom"
           "&%24top=50&%24orderby=receivedDateTime%20desc"
           "&%24filter=receivedDateTime%20ge%20" + start)
    get = http_get or _default_get
    seen_urls = set()
    results = []
    while url:
        if not safe_graph_url(url) or url in seen_urls or len(seen_urls) >= MAX_PAGES:
            raise GraphError("unsafe-or-truncated-pagination")
        seen_urls.add(url)
        data = get(url, token)
        if not isinstance(data, dict) or not isinstance(data.get("value"), list):
            raise GraphError("malformed-response")
        for item in data["value"]:
            if not isinstance(item, dict):
                raise GraphError("malformed-item")
            message_id = item.get("id")
            epoch = received_epoch(item.get("receivedDateTime"))
            if type(message_id) is not str or not 1 <= len(message_id) <= 1024 or epoch is None:
                raise GraphError("invalid-item")
            if epoch < from_epoch:
                continue
            if len(results) >= MAX_MESSAGES:
                raise GraphError("too-many-messages")
            raw_from = item.get("from") or {}
            if not isinstance(raw_from, dict):
                raise GraphError("invalid-sender")
            sender = raw_from.get("emailAddress") or {}
            if not isinstance(sender, dict):
                raise GraphError("invalid-sender")
            platform = classify(sender.get("address"))
            if platform:
                results.append((message_id, epoch, platform))
        url = data.get("@odata.nextLink")
        if url is not None and type(url) is not str:
            raise GraphError("invalid-nextlink")
    return results
