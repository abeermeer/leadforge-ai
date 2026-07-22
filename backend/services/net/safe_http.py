"""SSRF-safe HTTP fetching (audit 1.1).

Every server-side fetch of a user-influenced URL (agency website, lead website)
must go through `safe_get()`. It:

  * allows only http/https schemes,
  * resolves the hostname and rejects any request whose resolved IPs fall in a
    private / loopback / link-local / reserved range (this is what blocks the
    cloud metadata endpoint 169.254.169.254, localhost, and internal services),
  * does NOT auto-follow redirects — it follows them manually and re-validates
    every hop's target (a public URL 302-ing to http://169.254.169.254/ is
    rejected at the hop, not followed),
  * caps the response body size so a hostile endpoint cannot stream us out of
    memory.

Residual risk: DNS rebinding (host resolves safe at validate time, evil at
connect time) is not fully closed here — closing it requires pinning the
connection to the validated IP. The ranges below cover the standard SSRF
targets; pin-to-IP is a future hardening if this ever fetches truly untrusted
input at scale.
"""
import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger("trax9.net.safe_http")

MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_REDIRECTS = 5
ALLOWED_SCHEMES = ("http", "https")


class UnsafeURLError(Exception):
    """Raised when a URL is disallowed (bad scheme or resolves to a blocked IP)."""


def _ip_blocked(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # 169.254.0.0/16 + fe80::/10 — the metadata range
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def validate_url(url: str) -> None:
    """Raise UnsafeURLError unless `url` is http/https and resolves only to public IPs."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Blocked scheme: {parsed.scheme!r} (only http/https allowed)")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")

    # A literal IP in the URL is checked directly (no DNS).
    try:
        literal = ipaddress.ip_address(host)
        if _ip_blocked(literal):
            raise UnsafeURLError(f"Blocked address: {host}")
        return
    except ValueError:
        pass  # not a literal IP — resolve the name

    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, parsed.port or 80, 0, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve host {host!r}: {exc}")

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _ip_blocked(ip):
            raise UnsafeURLError(f"Host {host!r} resolves to blocked address {ip}")


async def safe_get(
    url: str,
    *,
    client: httpx.AsyncClient,
    headers: dict | None = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> httpx.Response:
    """GET `url` with SSRF protection. `client` MUST be created with
    follow_redirects=False. Returns a fully-read httpx.Response.

    Raises UnsafeURLError for a blocked target (initial or any redirect hop),
    httpx errors for transport failures.
    """
    await validate_url(url)
    current = url
    hops = 0
    while True:
        request = client.build_request("GET", current, headers=headers)
        response = await client.send(request, stream=True, follow_redirects=False)
        if response.is_redirect and hops < max_redirects:
            location = response.headers.get("location")
            await response.aclose()
            if not location:
                break
            current = urljoin(current, location)
            await validate_url(current)  # re-validate every hop
            hops += 1
            continue

        # Final response — read the body under the cap.
        chunks: list[bytes] = []
        total = 0
        try:
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise UnsafeURLError(f"Response exceeded {max_bytes} byte cap")
                chunks.append(chunk)
        finally:
            await response.aclose()
        # Make .text/.content/.json() work after the manual stream read.
        response._content = b"".join(chunks)
        return response
