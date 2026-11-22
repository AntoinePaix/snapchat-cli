"""Turn user input (URLs or bare names/ids) into normalised download targets."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote
from urllib.parse import urlparse

Kind = Literal["user", "search", "place", "nearby"]

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,30}$")


@dataclass(frozen=True)
class Target:
    kind: Kind
    key: str  # username, search query, or place id
    slug: str | None = None  # place URL slug, cosmetic only


def parse_url(value: str) -> Target:
    """Parse any supported snapchat.com URL.

    Supported forms::

        https://www.snapchat.com/@user           (also /add/user, /@user/highlight/..., ...)
        https://www.snapchat.com/explore/<query>
        https://www.snapchat.com/place/<slug>/<uuid>
    """
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc.endswith("snapchat.com"):
        raise ValueError(f"Not a snapchat.com URL: {value}")
    parts = [unquote(p) for p in parsed.path.split("/") if p]
    if not parts:
        raise ValueError(f"Unsupported snapchat.com URL: {value}")
    head = parts[0]
    if head.startswith("@") and len(head) > 1:
        return Target("user", head[1:])
    if head in ("add", "at") and len(parts) > 1:
        return Target("user", parts[1].lstrip("@"))
    if head == "explore" and len(parts) > 1:
        return Target("search", parts[1])
    if head == "place":
        uuid = next((p for p in parts[1:] if _UUID_RE.fullmatch(p)), None)
        if uuid:
            slug = parts[1] if len(parts) > 2 else None
            return Target("place", uuid.lower(), slug)
    raise ValueError(f"Unsupported snapchat.com URL: {value}")


def is_url(value: str) -> bool:
    return "snapchat.com" in value or value.startswith(("http://", "https://"))


def parse_target(value: str, kind: Kind | None = None) -> Target:
    """Parse a URL or, given an expected ``kind``, a bare username/query/place id."""
    value = value.strip()
    if is_url(value):
        target = parse_url(value)
        if kind and target.kind != kind:
            raise ValueError(f"Expected a {kind} URL, got a {target.kind} URL: {value}")
        return target
    if kind == "user":
        name = value.lstrip("@")
        if not _USERNAME_RE.match(name):
            raise ValueError(f"Invalid username: {value}")
        return Target("user", name)
    if kind == "search":
        if not value:
            raise ValueError("Empty search query")
        return Target("search", value)
    if kind == "place":
        match = _UUID_RE.search(value)
        if not match:
            raise ValueError(f"Expected a place URL or id (UUID), got: {value}")
        return Target("place", match.group(0).lower())
    raise ValueError(f"Cannot tell what kind of target this is (pass a full URL): {value}")


def slugify(text: str, max_len: int = 60) -> str:
    """Filesystem-safe name that keeps unicode letters (titles are often emoji/non-latin)."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[^\w.-]+", "_", text, flags=re.UNICODE).strip("._")
    return text[:max_len] or "untitled"


_COORDS_RE = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)\s*[,;\s]\s*([-+]?\d+(?:\.\d+)?)\s*$")


def parse_coordinates(value: str) -> tuple[float, float]:
    """``"48.8566,2.3522"`` (comma, semicolon or space separated) -> ``(lat, lng)``."""
    match = _COORDS_RE.match(value)
    if not match:
        raise ValueError(f"Expected 'LAT,LNG' (e.g. 48.8566,2.3522), got: {value!r}")
    lat, lng = float(match.group(1)), float(match.group(2))
    if not -90 <= lat <= 90:
        raise ValueError(f"Latitude out of range [-90, 90]: {lat}")
    if not -180 <= lng <= 180:
        raise ValueError(f"Longitude out of range [-180, 180]: {lng}")
    return lat, lng
