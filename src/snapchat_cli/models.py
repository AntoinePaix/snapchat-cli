"""Normalised output models shared by extractors, the downloader and the CLI."""

from __future__ import annotations

import hashlib
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import Field

MediaType = Literal["image", "video", "unknown"]


class Snap(BaseModel):
    """A single piece of downloadable media (story snap, highlight snap, spotlight...)."""

    id: str = ""
    media_url: str
    media_type: MediaType = "unknown"
    preview_url: str | None = None
    overlay_url: str | None = None
    timestamp: int | None = None  # Unix epoch, seconds
    author: str | None = None
    title: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    # Filled in by the downloader: path of the media file, relative to the collection folder.
    file: str | None = None

    @property
    def stem(self) -> str:
        """Stable file name (without extension), sortable by date."""
        ident = self.id or hashlib.sha1(self.media_url.encode()).hexdigest()[:16]
        if self.timestamp:
            date = datetime.fromtimestamp(self.timestamp, tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
            return f"{date}_{ident}"
        return ident


class Collection(BaseModel):
    """Everything extracted from one web page.

    ``groups`` maps a relative folder name (e.g. ``"story"`` or
    ``"highlights/Moon_Joy_ddf02468"``) to the snaps that belong in it.
    """

    kind: Literal["user", "search", "place", "nearby"]
    key: str  # username, search query or place id
    folder: str  # output folder, relative to the download root
    url: str
    metadata: dict[str, Any]
    groups: dict[str, list[Snap]] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def snap_count(self) -> int:
        return sum(len(snaps) for snaps in self.groups.values())

    def manifest(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "url": self.url,
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "metadata": self.metadata,
            "snaps": {
                name: [s.model_dump(mode="json") for s in snaps]
                for name, snaps in self.groups.items()
            },
        }
