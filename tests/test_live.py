"""Live smoke tests against snapchat.com, deselected by default: run them with `pytest -m live`."""

import asyncio

import pytest

from snapchat_cli import NotFoundError
from snapchat_cli import SnapchatClient
from snapchat_cli import fetch_nearby
from snapchat_cli import fetch_place
from snapchat_cli import fetch_search
from snapchat_cli import fetch_user

pytestmark = pytest.mark.live


def _run(coro):
    return asyncio.run(coro)


async def _fetch_all():
    async with SnapchatClient() as client:
        return await asyncio.gather(
            fetch_user(client, "nasa"),
            fetch_search(client, "disney"),
            fetch_place(client, "b0fe0fce-9994-11e8-9ce9-bf074d92c723"),
        )


def test_live_endpoints():
    user, search, place = _run(_fetch_all())
    assert user.metadata["profile"]["username"] == "nasa"
    assert search.metadata["counts"].get("spotlight", 0) > 0
    assert place.metadata["place"]["placeInfo"]["name"]
    assert user.snap_count + search.snap_count + place.snap_count > 0


def test_live_not_found():
    async def go():
        async with SnapchatClient(retries=0) as client:
            await fetch_user(client, "zzqq_no_such_user_8812734")

    with pytest.raises(NotFoundError):
        _run(go())


def test_live_nearby():
    async def go():
        async with SnapchatClient() as client:
            return await fetch_nearby(client, 48.8566, 2.3522, ["museums", "bars"])

    c = _run(go())
    assert c.metadata["categories"] == ["museums", "bars"]
    assert 20 < c.metadata["place_count"] <= 40
    assert all(p["distance_km"] < 5 for p in c.metadata["places"])  # really around Paris
