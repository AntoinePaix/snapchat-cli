import asyncio
import json
from pathlib import Path

from snapchat_cli.client import SnapchatError
from snapchat_cli.client import sniff_ext
from snapchat_cli.downloader import download_collection
from snapchat_cli.models import Collection
from snapchat_cli.models import Snap


class FakeClient:
    """Stands in for SnapchatClient.download: writes a JPEG, fails on 'bad' URLs."""

    def __init__(self):
        self.calls: list[str] = []

    async def download(self, url: str, dest_stem: Path, default_ext: str = ".bin") -> Path:
        self.calls.append(url)
        if "bad" in url:
            raise SnapchatError(f"HTTP 500 for {url}")
        dest = dest_stem.with_name(dest_stem.name + ".jpg")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\xff\xd8\xff")
        return dest


def _collection() -> Collection:
    return Collection(
        kind="user",
        key="someone",
        folder="user/someone",
        url="https://www.snapchat.com/@someone",
        metadata={"profile": {"username": "someone"}},
        groups={
            "story": [
                Snap(
                    id="a",
                    media_url="https://cdn/a",
                    media_type="image",
                    timestamp=1_700_000_000,
                    overlay_url="https://cdn/a-overlay",
                ),
                Snap(id="b", media_url="https://cdn/bad", media_type="image"),
            ],
        },
    )


def test_download_and_resume(tmp_path):
    client = FakeClient()
    out, stats = asyncio.run(download_collection(client, _collection(), tmp_path))
    # media a + overlay a; b fails
    assert (stats.downloaded, stats.skipped, stats.failed) == (2, 0, 1)
    assert sorted(p.name for p in (out / "story").iterdir()) == [
        "20231114_221320_a.jpg",
        "20231114_221320_a_overlay.jpg",
    ]
    manifest = json.loads((out / "manifest.json").read_text())
    files = {s["id"]: s["file"] for s in manifest["snaps"]["story"]}
    assert files == {"a": "story/20231114_221320_a.jpg", "b": None}

    # Second run: finished files are skipped, the failed one is retried.
    client.calls.clear()
    _, stats = asyncio.run(download_collection(client, _collection(), tmp_path))
    assert (stats.downloaded, stats.skipped, stats.failed) == (0, 2, 1)
    assert client.calls == ["https://cdn/bad"]


def test_metadata_only(tmp_path):
    client = FakeClient()
    out, stats = asyncio.run(
        download_collection(client, _collection(), tmp_path, media=False, overlays=False, raw=True),
    )
    assert client.calls == [] and stats.downloaded == 0
    assert (out / "manifest.json").exists() and (out / "raw.json").exists()


def test_sniff_ext():
    assert sniff_ext(b"\x00\x00\x00\x18ftypmp42") == ".mp4"
    assert sniff_ext(b"\xff\xd8\xff\xe0") == ".jpg"
    assert sniff_ext(b"\x89PNG\r\n") == ".png"
    assert sniff_ext(b"RIFF\x00\x00\x00\x00WEBP") == ".webp"
    assert sniff_ext(b"hello") is None
