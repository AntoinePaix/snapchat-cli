"""Nearby (GPS) search: coordinates, grid, merging, and the CDN cache workaround."""

import asyncio
import gzip
import json
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import pytest

from snapchat_cli.client import NotFoundError
from snapchat_cli.client import SnapchatError
from snapchat_cli.extractors import distance_km
from snapchat_cli.extractors import fetch_nearby
from snapchat_cli.extractors import fetch_nearby_point
from snapchat_cli.extractors import grid_points
from snapchat_cli.extractors import nearby_url
from snapchat_cli.extractors import parse_nearby
from snapchat_cli.schemas import NearbyResponse
from snapchat_cli.targets import parse_coordinates

PARIS = (48.8566, 2.3522)
FIXTURE = Path(__file__).parent / "fixtures" / "nearby.json.gz"


@pytest.fixture(scope="module")
def paris_responses() -> list[tuple[str, NearbyResponse]]:
    """Real API answers for bars and museums around Paris centre."""
    data = json.loads(gzip.decompress(FIXTURE.read_bytes()))
    return [(d["category"], NearbyResponse.model_validate(d["response"])) for d in data]


# --------------------------------------------------------------------------- coordinates & geometry


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("48.8566,2.3522", (48.8566, 2.3522)),
        (" 48.8566 , 2.3522 ", (48.8566, 2.3522)),
        ("-33.8688,151.2093", (-33.8688, 151.2093)),
        ("40.7128 -74.006", (40.7128, -74.006)),
        ("0;0", (0.0, 0.0)),
    ],
)
def test_parse_coordinates(value, expected):
    assert parse_coordinates(value) == expected


@pytest.mark.parametrize("value", ["", "48.85", "abc,def", "91,0", "0,181", "1,2,3"])
def test_parse_coordinates_invalid(value):
    with pytest.raises(ValueError):
        parse_coordinates(value)


def test_distance_km():
    assert distance_km(*PARIS, *PARIS) == 0
    assert distance_km(48.8566, 2.3522, 51.5072, -0.1276) == pytest.approx(343.5, abs=1)


def test_grid_points():
    assert grid_points(*PARIS) == [PARIS]
    points = grid_points(*PARIS, size=3, step_km=1.0)
    assert len(points) == 9 and PARIS in points
    # Neighbours are ~1 km apart, both north-south and east-west.
    assert distance_km(*points[4], *points[1]) == pytest.approx(1.0, rel=0.01)
    assert distance_km(*points[4], *points[3]) == pytest.approx(1.0, rel=0.01)
    even = grid_points(*PARIS, size=2, step_km=1.0)
    assert len(even) == 4 and PARIS not in even  # centred between the 4 points


def test_grid_wraps_longitude():
    assert all(-180 <= lng <= 180 for _, lng in grid_points(0, 179.999, size=3, step_km=5))


def test_nearby_url():
    query = parse_qs(urlsplit(nearby_url(48.8566, 2.3522, "bars", "en-US-x-ab12")).query)
    assert query == {
        "category": ["bars"],
        "lat": ["48.8566"],
        "lng": ["2.3522"],
        "locale": ["en-US-x-ab12"],
    }


# --------------------------------------------------------------------------- merging


def test_parse_nearby(paris_responses):
    c = parse_nearby(*PARIS, paris_responses)
    md = c.metadata
    assert c.kind == "nearby" and c.folder == "nearby/48.8566_2.3522"
    assert md["categories"] == ["bars", "museums"]
    assert md["place_count"] == 40 and len(md["places"]) == 40
    distances = [p["distance_km"] for p in md["places"]]
    assert distances == sorted(distances) and distances[-1] < 3
    first = md["places"][0]
    assert first["place_name"] == "Galerie des enfants du Centre Pompidou"
    assert first["categories"] == ["museums"]
    assert c.snap_count == 113 and len(c.groups) == 29
    assert sum(p["snap_count"] for p in md["places"]) == 113
    snap = next(iter(c.groups.values()))[0]
    assert snap.media_url.startswith("https://") and snap.timestamp
    # raw.json keeps the API's original (camelCase) story keys.
    story = next(iter(c.raw["responses"][0]["stories_by_place_id"].values()))[0]
    assert "snapId" in story and "videoMetadata" in story


def test_parse_nearby_deduplicates_across_categories(paris_responses):
    bars = paris_responses[0][1]
    c = parse_nearby(*PARIS, [("bars", bars), ("pubs", bars)])
    assert c.metadata["place_count"] == 20
    assert c.snap_count == 56  # same snaps are not counted twice
    assert all(p["categories"] == ["bars", "pubs"] for p in c.metadata["places"])


# --------------------------------------------------------------------------- fetching (fake client)


class FakeClient:
    """Answers nearby calls from canned responses; records the URLs it was asked for."""

    locale = "en-US"

    def __init__(self, responses):
        self.responses = list(responses)
        self.urls: list[str] = []

    async def get_page(self, url: str) -> str:
        self.urls.append(url)
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer)


def _answer(lat, lng, category, places=()):
    return {"query": {"lat": lat, "lng": lng, "category": category}, "places": list(places)}


def test_fetch_retries_stale_cached_response():
    stale = _answer(33.8121, -117.919, "amusement-parks")
    good = _answer(*PARIS, "bars", [{"place_id": "p1", "location": {"lat": 48.857, "lng": 2.352}}])
    client = FakeClient([stale, good])
    response = asyncio.run(fetch_nearby_point(client, *PARIS, "bars"))
    assert response.places[0].place_id == "p1"
    locales = [parse_qs(urlsplit(u).query)["locale"][0] for u in client.urls]
    assert len(locales) == 2 and locales[0] != locales[1]
    assert all(loc.startswith("en-US-x-") for loc in locales)  # unique tag busts the CDN cache


def test_fetch_gives_up_on_persistent_stale_cache():
    stale = _answer(33.8121, -117.919, "amusement-parks")
    client = FakeClient([stale] * 3)
    with pytest.raises(SnapchatError, match="cached results"):
        asyncio.run(fetch_nearby_point(client, *PARIS, "bars"))


def test_fetch_unknown_category():
    client = FakeClient([NotFoundError("404")])
    with pytest.raises(SnapchatError, match="Unknown nearby category 'nope'"):
        asyncio.run(fetch_nearby_point(client, *PARIS, "nope"))


def test_fetch_nearby_grid_and_categories():
    points = grid_points(*PARIS, size=2, step_km=1.0)
    answers = [
        _answer(lat, lng, cat, [{"place_id": f"{cat}-{i}", "location": {"lat": lat, "lng": lng}}])
        for i, (lat, lng) in enumerate(points)
        for cat in ("bars", "museums")
    ]
    client = FakeClient(answers)
    c = asyncio.run(fetch_nearby(client, *PARIS, ["bars", "museums"], grid=2, step_km=1.0))
    assert len(client.urls) == 8
    assert c.metadata["place_count"] == 8
    assert c.metadata["grid"] == {"size": 2, "step_km": 1.0, "points": 4}
