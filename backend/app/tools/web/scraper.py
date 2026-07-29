import ipaddress
import os
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from langchain_core.tools import tool

from ..base import ToolSpec, register

MAX_CHARS = int(os.getenv("FETCH_PAGE_MAX_CHARS", "6000"))
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = float(os.getenv("FETCH_PAGE_TIMEOUT", "15"))
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"

_TEXT_CONTENT_TYPES = ("text/html", "application/xhtml", "text/plain")


class _TextExtractor(HTMLParser):
    """Stdlib HTML-to-text: drops script/style/head, keeps title and body text."""

    _SKIP_TAGS = {"script", "style", "noscript", "template", "head", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.title = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if not self._skip_depth and data.strip():
            self._parts.append(" ".join(data.split()))

    def text(self) -> str:
        return "\n".join(self._parts)


def _is_private_host(host: str | None) -> bool:
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


@tool
async def fetch_page(url: str) -> str:
    """Fetch a public web page by URL and return its readable text content (title plus body text, truncated). Use this to read the content of a specific web page the user mentions or that a search result points to."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "Error: only http/https URLs are supported."
    # SSRF guard: never fetch internal/private addresses chosen by the model.
    if _is_private_host(parsed.hostname):
        return "Error: refusing to fetch a private or unresolvable address."

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if not any(t in content_type for t in _TEXT_CONTENT_TYPES):
                    return f"Error: unsupported content type '{content_type}'."
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= MAX_BYTES:
                        break
                body = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
    except httpx.HTTPStatusError as exc:
        return f"Error: server returned HTTP {exc.response.status_code} for {url}."
    except httpx.HTTPError as exc:
        return f"Error: could not fetch {url}: {exc}."

    if "text/plain" in content_type:
        text = body
        title = ""
    else:
        extractor = _TextExtractor()
        extractor.feed(body)
        text = extractor.text()
        title = extractor.title.strip()

    result = (f"Title: {title}\n\n" if title else "") + text
    if len(result) > MAX_CHARS:
        result = result[:MAX_CHARS] + "\n[...truncated]"
    return result or "Error: page contained no readable text."


register(
    ToolSpec(
        tool=fetch_page,
        prompt_hint=(
            "To read the content of a specific web page, call fetch_page with "
            "its URL."
        ),
    )
)
