"""Proxy parsing and rotation.

A :class:`ProxyPool` hands out one proxy per request (round-robin, random or
sticky) and benches proxies that keep failing for a cooldown period, so one
dead proxy does not sink a whole run.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from urllib.parse import quote
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

SCHEMES = ("http", "https", "socks4", "socks4a", "socks5", "socks5h")


class Rotation(str, Enum):
    round_robin = "round-robin"  # next proxy on every request
    random = "random"  # random proxy on every request
    sticky = "sticky"  # keep the first healthy proxy until it fails


def normalize_proxy(value: str) -> str:
    """Return a proxy URL, accepting the common shorthand formats.

    * ``scheme://[user:pass@]host:port`` (http, https, socks4, socks4a, socks5, socks5h)
    * ``host:port``                -> ``http://host:port``
    * ``host:port:user:pass``      -> ``http://user:pass@host:port``
    """
    value = value.strip()
    if not value:
        raise ValueError("Empty proxy")
    if "://" not in value:
        parts = value.split(":")
        if len(parts) == 2:
            host, port = parts
            value = f"http://{host}:{port}"
        elif len(parts) == 4:
            host, port, user, password = parts
            value = f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
        else:
            # Don't echo the value: a malformed shorthand may still contain a password.
            raise ValueError(
                f"Invalid proxy ({len(parts)} ':'-separated parts): "
                "expected host:port, host:port:user:pass or scheme://[user:pass@]host:port",
            )
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in SCHEMES:
        raise ValueError(
            f"Invalid proxy scheme {parsed.scheme!r} (use one of: {', '.join(SCHEMES)})",
        )
    try:
        port = parsed.port
    except ValueError:
        port = None
    if not parsed.hostname or port is None:
        raise ValueError(f"Invalid proxy {mask_proxy(value)!r}: missing host or port")
    return value


def mask_proxy(proxy: str | None) -> str:
    """Proxy URL safe to print: the password is replaced with ``***``."""
    if not proxy:
        return "direct"
    try:
        parsed = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
    except ValueError:
        return "<invalid proxy>"
    if parsed.password is None:
        return proxy
    netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@", 1)
    return parsed._replace(netloc=netloc).geturl()


def load_proxy_file(path: Path) -> list[str]:
    """Proxies from a text file: one per line, blank lines and ``#`` comments ignored."""
    proxies = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            proxies.append(line)
    return proxies


class ProxyPool:
    """Rotating set of proxies with failure tracking.

    After ``max_failures`` consecutive failures a proxy is benched for
    ``cooldown`` seconds. If every proxy is benched, they are used anyway
    (better a flaky proxy than none when the user asked for proxies).
    An empty pool always returns ``None`` (direct connection).
    """

    def __init__(
        self,
        proxies: Iterable[str] = (),
        *,
        rotation: Rotation | str = Rotation.round_robin,
        max_failures: int = 3,
        cooldown: float = 60.0,
    ) -> None:
        self.proxies = list(dict.fromkeys(normalize_proxy(p) for p in proxies))
        self.rotation = Rotation(rotation)
        self.max_failures = max_failures
        self.cooldown = cooldown
        self._failures: dict[str, int] = {}
        self._benched_until: dict[str, float] = {}
        self._index = 0

    def __len__(self) -> int:
        return len(self.proxies)

    def __bool__(self) -> bool:
        return bool(self.proxies)

    def _healthy(self) -> list[str]:
        now = time.monotonic()
        return [p for p in self.proxies if self._benched_until.get(p, 0.0) <= now]

    def next(self, exclude: str | None = None) -> str | None:
        """Pick the proxy for the next request (``exclude``: avoid the one that just failed)."""
        if not self.proxies:
            return None
        candidates = self._healthy() or list(self.proxies)
        if exclude in candidates and len(candidates) > 1:
            candidates.remove(exclude)
        if self.rotation is Rotation.random:
            return random.choice(candidates)
        if self.rotation is Rotation.sticky:
            return candidates[0]
        for _ in range(len(self.proxies)):
            proxy = self.proxies[self._index % len(self.proxies)]
            self._index += 1
            if proxy in candidates:
                return proxy
        return candidates[0]

    def has_alternative(self, proxy: str | None) -> bool:
        """Whether another healthy proxy than ``proxy`` is available."""
        return any(p != proxy for p in self._healthy())

    def report_success(self, proxy: str | None) -> None:
        if proxy:
            self._failures.pop(proxy, None)

    def report_failure(self, proxy: str | None) -> None:
        if not proxy:
            return
        count = self._failures.get(proxy, 0) + 1
        if count >= self.max_failures:
            self._failures.pop(proxy, None)
            self._benched_until[proxy] = time.monotonic() + self.cooldown
            log.warning(
                "Proxy %s failed %d times in a row, benched for %.0fs",
                mask_proxy(proxy),
                count,
                self.cooldown,
            )
        else:
            self._failures[proxy] = count
