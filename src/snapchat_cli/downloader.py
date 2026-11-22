"""Concurrent media download + metadata writing for a :class:`Collection`."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import aiofiles
import aiofiles.os

from snapchat_cli.client import SnapchatClient
from snapchat_cli.client import SnapchatError
from snapchat_cli.models import Collection
from snapchat_cli.models import Snap

log = logging.getLogger(__name__)

_DEFAULT_EXT = {"image": ".jpg", "video": ".mp4"}


@dataclass
class DownloadStats:
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: DownloadStats) -> None:
        self.downloaded += other.downloaded
        self.skipped += other.skipped
        self.failed += other.failed
        self.errors.extend(other.errors)


def _existing(stem: Path) -> Path | None:
    """A finished file named ``stem.<ext>`` (ignoring ``.part`` leftovers)."""
    if not stem.parent.is_dir():
        return None
    for candidate in stem.parent.glob(f"{stem.name}.*"):
        if candidate.suffix != ".part":
            return candidate
    return None


async def write_json(path: Path, data: object) -> None:
    """Atomically write ``data`` as pretty JSON (temp file + rename)."""
    await aiofiles.os.makedirs(path.parent, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    async with aiofiles.open(tmp, "w", encoding="utf-8") as fh:
        await fh.write(json.dumps(data, indent=2, ensure_ascii=False))
    await aiofiles.os.replace(tmp, path)


async def download_collection(
    client: SnapchatClient,
    collection: Collection,
    root: Path,
    *,
    concurrency: int = 8,
    media: bool = True,
    previews: bool = False,
    overlays: bool = True,
    raw: bool = False,
    on_progress: Callable[[], None] | None = None,
) -> tuple[Path, DownloadStats]:
    """Download every snap of ``collection`` under ``root/collection.folder``.

    Existing files are skipped, so re-running a command only fetches new snaps.
    Writes ``manifest.json`` (metadata + every snap with its local ``file``) and,
    when ``raw`` is set, the untouched page state as ``raw.json``.
    """
    out_dir = root / collection.folder
    await aiofiles.os.makedirs(out_dir, exist_ok=True)
    stats = DownloadStats()
    sem = asyncio.Semaphore(max(1, concurrency))

    async def fetch(url: str, stem: Path, default_ext: str) -> Path | None:
        if (found := _existing(stem)) is not None:
            stats.skipped += 1
            return found
        async with sem:
            try:
                path = await client.download(url, stem, default_ext)
            except SnapchatError as exc:
                stats.failed += 1
                stats.errors.append(str(exc))
                log.debug("download failed: %s", exc)
                return None
        stats.downloaded += 1
        return path

    async def handle(group: str, snap: Snap) -> None:
        stem = out_dir / group / snap.stem
        default_ext = _DEFAULT_EXT.get(snap.media_type, ".bin")
        if media:
            path = await fetch(snap.media_url, stem, default_ext)
            if path is not None:
                snap.file = path.relative_to(out_dir).as_posix()
        if overlays and snap.overlay_url:
            await fetch(snap.overlay_url, stem.with_name(stem.name + "_overlay"), ".png")
        if previews and snap.preview_url:
            await fetch(snap.preview_url, stem.with_name(stem.name + "_preview"), ".jpg")
        if on_progress:
            on_progress()

    await asyncio.gather(*(handle(g, s) for g, snaps in collection.groups.items() for s in snaps))

    await write_json(out_dir / "manifest.json", collection.manifest())
    if raw:
        await write_json(out_dir / "raw.json", collection.raw)
    return out_dir, stats
