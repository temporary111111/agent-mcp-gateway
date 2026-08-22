"""Web fetch and search tools for direct mode."""

from __future__ import annotations

import re
from typing import Any

import httpx

from ..errors import InvalidRequestError

# Common browser user-agent
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_FETCH_TIMEOUT = 15.0
_MAX_FETCH_BYTES = 500_000


def _strip_html(html: str) -> str:
    """Crude HTML-to-text conversion for readability."""
    # Remove script and style elements
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def web_fetch(url: str, *, max_chars: int = 50_000) -> dict[str, Any]:
    """Fetch a URL and return its content as text."""
    if not url or not url.strip():
        raise InvalidRequestError("URL must not be empty.")
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise InvalidRequestError("URL must start with http:// or https://")

    try:
        with httpx.Client(
            timeout=_FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            max_redirects=5,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.TimeoutException:
        raise InvalidRequestError(f"Timeout fetching {url}")
    except httpx.HTTPStatusError as exc:
        raise InvalidRequestError(
            f"HTTP {exc.response.status_code} fetching {url}"
        )
    except Exception as exc:
        raise InvalidRequestError(f"Failed to fetch {url}: {exc}")

    content_type = response.headers.get("content-type", "")
    raw = response.content[:_MAX_FETCH_BYTES]

    # Try to extract text from HTML
    if "html" in content_type:
        try:
            text = raw.decode("utf-8", errors="replace")
            text = _strip_html(text)
        except Exception:
            text = raw.decode("utf-8", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")

    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"

    return {
        "url": url,
        "status_code": response.status_code,
        "content_type": content_type,
        "text": text,
        "truncated": len(response.content) > _MAX_FETCH_BYTES,
    }


def web_search(query: str, *, max_results: int = 10) -> dict[str, Any]:
    """Search the web using DuckDuckGo HTML search (no API key needed)."""
    if not query or not query.strip():
        raise InvalidRequestError("Search query must not be empty.")
    query = query.strip()

    try:
        with httpx.Client(
            timeout=_FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
            )
            response.raise_for_status()
    except Exception as exc:
        raise InvalidRequestError(f"Search failed: {exc}")

    html = response.text
    results = []

    # Parse DuckDuckGo HTML results
    for match in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        url = match.group(1)
        title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        snippet = re.sub(r"<[^>]+>", "", match.group(3)).strip()
        # DuckDuckGo wraps URLs in a redirect
        if "uddg=" in url:
            import urllib.parse
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            url = parsed.get("uddg", [url])[0]
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break

    return {
        "query": query,
        "results": results,
        "total": len(results),
    }
