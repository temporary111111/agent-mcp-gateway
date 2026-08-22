"""Browser automation tools for web page interaction.

Provides basic browser capabilities: open URLs, read page content,
and interact with web elements. Uses httpx for HTTP and basic HTML parsing.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from ..errors import InvalidRequestError

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_TIMEOUT = 15.0
_MAX_CONTENT = 200_000


def _extract_links(html: str, base_url: str) -> list[dict[str, str]]:
    """Extract links from HTML."""
    links = []
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\']', html, re.IGNORECASE):
        href = match.group(1)
        full_url = urljoin(base_url, href)
        # Get link text
        text_match = re.search(
            r'<a[^>]+href=["\'][^"\']+["\'][^>]*>(.*?)</a>',
            html[max(0, match.start() - 200):match.end() + 200],
            re.DOTALL | re.IGNORECASE,
        )
        text = text_match.group(1) if text_match else href
        text = re.sub(r"<[^>]+>", "", text).strip()[:100]
        links.append({"url": full_url, "text": text or href})
    return links[:50]


def _extract_forms(html: str) -> list[dict[str, Any]]:
    """Extract form information from HTML."""
    forms = []
    for match in re.finditer(r"<form[^>]*>(.*?)</form>", html, re.DOTALL | re.IGNORECASE):
        form_html = match.group(0)
        action_match = re.search(r'action=["\']([^"\']*)["\']', form_html, re.IGNORECASE)
        method_match = re.search(r'method=["\']([^"\']*)["\']', form_html, re.IGNORECASE)
        inputs = []
        for inp in re.finditer(r'<input[^>]+>', form_html, re.IGNORECASE):
            inp_html = inp.group(0)
            name_match = re.search(r'name=["\']([^"\']*)["\']', inp_html, re.IGNORECASE)
            type_match = re.search(r'type=["\']([^"\']*)["\']', inp_html, re.IGNORECASE)
            if name_match:
                inputs.append({
                    "name": name_match.group(1),
                    "type": (type_match.group(1) if type_match else "text"),
                })
        forms.append({
            "action": action_match.group(1) if action_match else "",
            "method": (method_match.group(1) if method_match else "GET").upper(),
            "inputs": inputs,
        })
    return forms


def browser_open(url: str) -> dict[str, Any]:
    """Open a URL and return the page content with links and forms."""
    if not url or not url.strip():
        raise InvalidRequestError("URL must not be empty.")
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise InvalidRequestError("URL must start with http:// or https://")

    try:
        with httpx.Client(
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            max_redirects=5,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.TimeoutException:
        raise InvalidRequestError(f"Timeout loading {url}")
    except httpx.HTTPStatusError as exc:
        raise InvalidRequestError(f"HTTP {exc.response.status_code} loading {url}")
    except Exception as exc:
        raise InvalidRequestError(f"Failed to load {url}: {exc}")

    content_type = response.headers.get("content-type", "")
    raw = response.content[:_MAX_CONTENT]
    html = raw.decode("utf-8", errors="replace")

    # Extract title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else ""

    # Extract text content
    text = html
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    links = _extract_links(html, url) if "html" in content_type else []
    forms = _extract_forms(html) if "html" in content_type else []

    return {
        "url": str(response.url),
        "title": title,
        "status_code": response.status_code,
        "content_type": content_type,
        "text": text[:30_000],
        "links": links,
        "forms": forms,
        "truncated": len(response.content) > _MAX_CONTENT,
    }
