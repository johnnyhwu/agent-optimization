"""How a credential reaches the agent server — and where it must not reach.

**Authentication is not part of the agent server contract.** The contract says
what a server must answer; whether it demands a credential first is its own
business, and most of the teams this platform is being handed to have no
authentication on their agent at all. So everything here is inert until
somebody fills a field in: with no API key the request headers are exactly what
they were before this module existed, which is the property
`tests/test_agent_client.py` pins down.

What the platform gains is the ability to *send* one. A team whose agent sits
behind a gateway could not connect at all before — the failure was a 401 with
no field on any screen to answer it.

**One key, one header.** `Authorization: Bearer <key>` is what an
OpenAI-standard endpoint takes, and that covers LiteLLM, vLLM and every gateway
speaking the same shape. A header name can be given for the internal gateway
that wants `X-Api-Key` instead. A free-form table of headers was the obvious
generalisation and is a worse product: nothing in it can be validated, nothing
can tell which row is the secret (so neither redaction nor endpoint binding
works), and it invites pasting a session cookie into a stored credential.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import httpx

# The header a credential goes in when nobody says otherwise, and the one name
# whose value is prefixed. Every other header carries the key verbatim: an
# `X-Api-Key: Bearer abc` would be rejected by the gateway that asked for it.
DEFAULT_AUTH_HEADER = "Authorization"
BEARER_PREFIX = "Bearer "


def auth_headers(api_key: str | None, header_name: str | None = None) -> dict[str, str]:
    """The authorization header for one request, or nothing at all.

    Blank key means blank headers — not an empty `Authorization`, which some
    gateways treat as a malformed credential and reject more confusingly than
    no credential at all.
    """
    key = (api_key or "").strip()
    if not key:
        return {}
    name = (header_name or "").strip() or DEFAULT_AUTH_HEADER
    if name.lower() == DEFAULT_AUTH_HEADER.lower():
        # Tolerated rather than doubled: someone who pastes a value that already
        # says "Bearer" has given us a complete header value, not a key.
        value = key if key.lower().startswith(BEARER_PREFIX.lower()) else BEARER_PREFIX + key
    else:
        value = key
    return {name: value}


def same_origin(a: str | None, b: str | None) -> bool:
    """Do these two URLs address the same server?

    This is the rule that decides whether the chat endpoint's credential is also
    sent to the skills endpoint. The credential was typed against the chat URL,
    and `services/user_secrets.py` states the binding it has to keep: a
    credential only ever goes to the address its owner was looking at. Two URLs
    on one host are one address for that purpose; a skills URL pointed somewhere
    else is somewhere else, and gets nothing.

    Compared on scheme, host and port rather than by string prefix, so
    `https://agent:443/x` and `https://agent/y` are correctly the same origin
    and `https://agent.example.com.evil.test/` is correctly not.
    """
    if not a or not b:
        return False
    pa, pb = urlsplit(a.strip()), urlsplit(b.strip())
    if not pa.scheme or not pa.hostname or not pb.scheme or not pb.hostname:
        return False

    def port(p) -> int | None:
        try:
            return p.port or {"http": 80, "https": 443}.get(p.scheme.lower())
        except ValueError:
            # An unparseable port is not a match. Failing closed here costs one
            # re-typed credential; failing open sends it to the wrong host.
            return None

    return (
        pa.scheme.lower() == pb.scheme.lower()
        and pa.hostname.lower() == pb.hostname.lower()
        and port(pa) is not None
        and port(pa) == port(pb)
    )


def redact(text: str, api_key: str | None) -> str:
    """The same text with the credential taken out of it.

    Applied to whatever the agent server said, because that text is quoted into
    run records, check results and the browser. A server that echoes the request
    headers in its error body — a real thing that gateways and debug handlers do
    — would otherwise write the key into a place none of the storage rules for
    credentials cover.
    """
    key = (api_key or "").strip()
    if not key or not text:
        return text
    # The full header value first, so `Bearer abc` becomes one marker rather
    # than the word "Bearer" followed by one.
    return text.replace(BEARER_PREFIX + key, "<redacted>").replace(key, "<redacted>")


def _is_https_upgrade(origin: str, url: str) -> bool:
    """Is this the one redirect that may keep the credential? `http://h` → `https://h`.

    Mirrors the exception httpx makes for `Authorization` (`_is_https_redirect`),
    so binding the credential to an origin does not break the server that is
    reached over http and immediately upgrades. Anything else — another host,
    another port, a downgrade — is a different address.
    """
    a, b = urlsplit(origin), urlsplit(url)
    if not a.hostname or a.hostname.lower() != (b.hostname or "").lower():
        return False
    try:
        return (
            a.scheme.lower() == "http"
            and (a.port or 80) == 80
            and b.scheme.lower() == "https"
            and (b.port or 443) == 443
        )
    except ValueError:
        return False


def _origin_guard(header_name: str, origin: str):
    """A request hook that drops the credential header once it leaves its origin.

    `follow_redirects=True` means the client may send the request somewhere
    other than the URL that was typed. httpx already strips `Authorization` on a
    cross-origin redirect — and only that name, because it is the only one it
    knows to be a credential. A server configured with `X-Api-Key` would have
    its key carried to wherever a `302` pointed, which is the endpoint binding
    in `services/user_secrets.py` broken by a response.

    A request hook rather than a custom transport: httpx runs these once per
    redirect hop with the hop's real URL (`_send_handling_redirects`), so this
    sees every address the credential would reach, while leaving the client's
    own transport — and with it the proxy configuration httpx reads from the
    environment — exactly as it would otherwise be. Applied to both header names
    rather than only the custom one: two rules that mostly agree are harder to
    reason about than one, and this one is the rule the platform actually
    promises.
    """

    async def guard(request: httpx.Request) -> None:
        target = str(request.url)
        if same_origin(origin, target) or _is_https_upgrade(origin, target):
            return
        # Conditional because httpx may have removed it already: it does its own
        # stripping for `Authorization`, and deleting a header that is not there
        # raises.
        if header_name in request.headers:
            del request.headers[header_name]

    return guard


def credentialed_client(
    url: str,
    *,
    timeout_s: float,
    api_key: str = "",
    auth_header: str = "",
) -> httpx.AsyncClient:
    """The client for talking to one agent endpoint, credential bound to its origin.

    With no key this is the plain client it has always been — same timeout, same
    redirect following, nothing in the way.
    """
    headers = auth_headers(api_key, auth_header)
    if not headers:
        return httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)
    name = next(iter(headers))
    return httpx.AsyncClient(
        timeout=timeout_s,
        follow_redirects=True,
        event_hooks={"request": [_origin_guard(name, url)]},
    )
