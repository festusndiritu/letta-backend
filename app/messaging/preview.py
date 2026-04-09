"""
Link preview.

Server-side Open Graph / meta tag fetcher.
Android sends a URL, server fetches it and returns title/description/image.
Keeps the fetch off the device (no leaking device IP to third-party sites)
and lets us cache results.

Simple in-memory cache — good enough for V1, swap for Redis later.
"""

import re
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.auth.dependencies import get_current_user
from app.models import User
from app.core.rate_limit import limiter

router = APIRouter()

# Simple in-memory cache: url → PreviewOut
_cache: dict[str, "PreviewOut"] = {}
_MAX_CACHE = 500


class PreviewOut(BaseModel):
    url: str
    title: str | None
    description: str | None
    image_url: str | None
    site_name: str | None


def _extract_meta(html: str, url: str) -> PreviewOut:
    def og(prop: str) -> str | None:
        m = re.search(
            rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        if m:
            return m.group(1)
        # Try reversed attribute order
        m = re.search(
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:{prop}["\']',
            html,
            re.IGNORECASE,
        )
        return m.group(1) if m else None

    def meta_name(name: str) -> str | None:
        m = re.search(
            rf'<meta[^>]+name=["\'](?:twitter:)?{name}["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        return m.group(1) if m else None

    def title_tag() -> str | None:
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        return m.group(1).strip() if m else None

    return PreviewOut(
        url=url,
        title=og("title") or meta_name("title") or title_tag(),
        description=og("description") or meta_name("description"),
        image_url=og("image") or meta_name("image"),
        site_name=og("site_name"),
    )


@router.get("/meta/preview", response_model=PreviewOut)
@limiter.limit("30/minute")
async def link_preview(
    request: Request,
    url: str = Query(...),
    current_user: User = Depends(get_current_user),
):
    _ = request
    # Basic URL validation
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid URL.")

    # Cache hit
    if url in _cache:
        return _cache[url]

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=8.0) as client:
            response = await client.get(
                url,
                headers={"User-Agent": "LettaBot/1.0 (link preview)"},
            )
        if response.status_code != 200:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Could not fetch URL.")

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="URL does not return HTML.")

        preview = _extract_meta(response.text, url)

    except httpx.RequestError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Could not reach URL.")

    # Cache with size cap
    if len(_cache) >= _MAX_CACHE:
        _cache.pop(next(iter(_cache)))
    _cache[url] = preview

    return preview