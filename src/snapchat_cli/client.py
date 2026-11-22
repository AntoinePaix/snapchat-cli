"""Async HTTP client for snapchat.com built on curl_cffi (browser TLS impersonation)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Iterable
from pathlib import Path

import aiofiles
import aiofiles.os
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from snapchat_cli.proxies import ProxyPool
from snapchat_cli.proxies import Rotation
from snapchat_cli.proxies import mask_proxy

log = logging.getLogger(__name__)

BASE_URL = "https://www.snapchat.com"

_RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}
# Statuses that usually mean "this proxy's IP is blocked/limited" (or bad proxy credentials).
_PROXY_BLOCK_STATUSES = {403, 407, 429}

CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "text/vtt": ".vtt",
}


def sniff_ext(head: bytes) -> str | None:
    """File extension from magic bytes (the CDN sometimes answers application/octet-stream)."""
    if head[4:8] == b"ftyp":
        return ".mov" if head[8:10] == b"qt" else ".mp4"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return ".webm"
    if head.startswith(b"WEBVTT"):
        return ".vtt"
    return None


class SnapchatError(Exception):
    """Base error for this package."""


class NotFoundError(SnapchatError):
    """The requested user, place or page does not exist (HTTP 404)."""


class SnapchatClient:
    """Thin async wrapper around a ``curl_cffi`` session with retries.

    Use as an async context manager::

        async with SnapchatClient() as client:
            html = await client.get_page("https://www.snapchat.com/@someone")

    Proxies: pass ``proxy`` (one) and/or ``proxies`` (several, or a ready
    :class:`ProxyPool`). A proxy is chosen per request according to
    ``proxy_rotation``; one that errors or gets blocked (HTTP 403/407/429) is
    swapped for another on the retry. With ``proxy_media=False`` only pages go
    through the proxies and media downloads from the CDN connect directly.
    Without any proxy, curl's usual ``HTTPS_PROXY``/``ALL_PROXY`` variables apply.
    """

    def __init__(
        self,
        *,
        impersonate: str = "chrome",
        locale: str = "en-US",
        proxy: str | None = None,
        proxies: ProxyPool | Iterable[str] = (),
        proxy_rotation: Rotation | str = Rotation.round_robin,
        proxy_media: bool = True,
        timeout: float = 30.0,
        retries: int = 3,
    ) -> None:
        self.impersonate = impersonate
        self.locale = locale
        if isinstance(proxies, ProxyPool):
            if proxy:
                raise ValueError("Pass either 'proxy' or a ProxyPool, not both")
            self.proxies = proxies
        else:
            self.proxies = ProxyPool(
                [*([proxy] if proxy else []), *proxies],
                rotation=proxy_rotation,
            )
        self.proxy_media = proxy_media
        self.timeout = timeout
        self.retries = retries
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> SnapchatClient:
        self._session = AsyncSession(
            impersonate=self.impersonate,
            headers={"Accept-Language": f"{self.locale},{self.locale.split('-')[0]};q=0.9"},
            timeout=self.timeout,
        )
        if self.proxies:
            log.info(
                "Using %d proxy(ies), %s rotation%s",
                len(self.proxies),
                self.proxies.rotation.value,
                "" if self.proxy_media else " (media downloads direct)",
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError(
                "SnapchatClient must be used as 'async with SnapchatClient() as client'",
            )
        return self._session

    async def _backoff(self, attempt: int, reason: str, url: str, proxy: str | None) -> None:
        if proxy and self.proxies.has_alternative(proxy):
            # The next attempt goes through another proxy: no need to wait long.
            delay = 0.2 + random.random() * 0.3
        else:
            delay = min(30.0, 2**attempt) + random.random()
        log.warning(
            "%s for %s via %s, retrying in %.1fs (%d/%d)",
            reason,
            url,
            mask_proxy(proxy),
            delay,
            attempt + 1,
            self.retries,
        )
        await asyncio.sleep(delay)

    async def get_page(self, url: str) -> str:
        """GET an HTML page, retrying transient failures. Raises ``NotFoundError`` on 404."""
        proxy: str | None = None
        for attempt in range(self.retries + 1):
            proxy = self.proxies.next(exclude=proxy)
            try:
                resp = await self.session.get(url, proxy=proxy)
            except RequestException as exc:
                self.proxies.report_failure(proxy)
                if attempt >= self.retries:
                    raise SnapchatError(
                        f"Request to {url} via {mask_proxy(proxy)} failed: {exc}",
                    ) from exc
                await self._backoff(attempt, type(exc).__name__, url, proxy)
                continue
            status = resp.status_code
            if status == 404:
                self.proxies.report_success(proxy)
                raise NotFoundError(f"Not found: {url}")
            blocked = proxy is not None and status in _PROXY_BLOCK_STATUSES
            if blocked:
                self.proxies.report_failure(proxy)
            if (status in _RETRY_STATUSES or blocked) and attempt < self.retries:
                await self._backoff(attempt, f"HTTP {status}", url, proxy)
                continue
            if status >= 400:
                raise SnapchatError(f"HTTP {status} for {url} via {mask_proxy(proxy)}")
            self.proxies.report_success(proxy)
            return resp.text
        raise AssertionError("unreachable")

    async def download(self, url: str, dest_stem: Path, default_ext: str = ".bin") -> Path:
        """Stream ``url`` to ``dest_stem`` + an extension derived from its Content-Type.

        Data goes to a ``.part`` file first and is renamed when complete, so an
        interrupted run never leaves a truncated file behind.
        """
        proxy: str | None = None
        for attempt in range(self.retries + 1):
            proxy = self.proxies.next(exclude=proxy) if self.proxy_media else None
            part: Path | None = None
            try:
                async with self.session.stream("GET", url, proxy=proxy) as resp:
                    status = resp.status_code
                    blocked = proxy is not None and status in _PROXY_BLOCK_STATUSES
                    if blocked:
                        self.proxies.report_failure(proxy)
                    if (status in _RETRY_STATUSES or blocked) and attempt < self.retries:
                        await self._backoff(attempt, f"HTTP {status}", url, proxy)
                        continue
                    if status >= 400:
                        raise SnapchatError(f"HTTP {status} for {url} via {mask_proxy(proxy)}")
                    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                    ext = CONTENT_TYPE_EXT.get(ctype)
                    part = dest_stem.with_name(dest_stem.name + ".part")
                    await aiofiles.os.makedirs(part.parent, exist_ok=True)
                    head = b""
                    async with aiofiles.open(part, "wb") as fh:
                        async for chunk in resp.aiter_content():
                            if len(head) < 16:
                                head += chunk[:16]
                            await fh.write(chunk)
                dest = dest_stem.with_name(dest_stem.name + (ext or sniff_ext(head) or default_ext))
                await aiofiles.os.replace(part, dest)
                self.proxies.report_success(proxy)
                return dest
            except RequestException as exc:
                self.proxies.report_failure(proxy)
                if part is not None:
                    with contextlib.suppress(FileNotFoundError):
                        await aiofiles.os.remove(part)
                if attempt >= self.retries:
                    raise SnapchatError(
                        f"Download of {url} via {mask_proxy(proxy)} failed: {exc}",
                    ) from exc
                await self._backoff(attempt, type(exc).__name__, url, proxy)
        raise SnapchatError(f"Download of {url} failed after {self.retries} retries")
