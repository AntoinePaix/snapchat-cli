"""Offline parser tests against saved snapchat.com pages (tests/fixtures)."""

import json

import pytest

from snapchat_cli.client import SnapchatError
from snapchat_cli.extractors import parse_place
from snapchat_cli.extractors import parse_search
from snapchat_cli.extractors import parse_user
from snapchat_cli.extractors import search_usernames
from snapchat_cli.parsing import clean_rsc
from snapchat_cli.parsing import unwrap

PLACE_ID = "b0fe0fce-9994-11e8-9ce9-bf074d92c723"


def test_parse_user(user_html):
    c = parse_user(user_html, "nasa")
    assert c.kind == "user" and c.folder == "user/nasa"
    assert c.metadata["profile"]["username"] == "nasa"
    assert c.metadata["profile"]["title"] == "NASA"
    assert "story" not in c.groups  # no live story when the fixture was captured
    highlights = [g for g in c.groups if g.startswith("highlights/")]
    assert len(highlights) == len(c.metadata["highlights"]) == 10
    assert len(c.groups["highlights/Moon_Joy_ddf02468"]) == 9
    assert {s.media_type for s in c.groups["highlights/Artemis_Wallpaper_for_Your_Phone_264684ee"]} == {"image"}
    assert len(c.groups["spotlight"]) == 7
    assert c.snap_count == 63
    assert len(c.metadata["lenses"]) == 3
    snap = c.groups["highlights/Moon_Joy_ddf02468"][0]
    assert snap.media_url.startswith("https://") and snap.timestamp and snap.author == "nasa"
    assert {s.media_type for g in c.groups.values() for s in g} <= {"image", "video"}


def test_parse_user_story_and_empty_spotlight_tiles():
    """Synthetic page: a live story, plus spotlight tiles without media (skipped)."""
    props = {
        "userProfile": {"$case": "publicProfileInfo", "publicProfileInfo": {"username": "someone"}},
        "story": {
            "storyId": {"value": "someone"},
            "snapList": [
                {
                    "snapId": {"value": "s1"},
                    "snapMediaType": 0,
                    "timestampInSec": {"value": "1795312165"},
                    "snapUrls": {
                        "mediaUrl": "https://cdn/s1",
                        "overlayUrl": {"value": "https://cdn/o1"},
                    },
                },
            ],
        },
        "spotlightHighlights": [{"snapList": []}, {"snapList": None}],
    }
    page = json.dumps({"props": {"pageProps": props}})
    html = f'<script id="__NEXT_DATA__" type="application/json">{page}</script>'
    c = parse_user(html, "someone")
    assert list(c.groups) == ["story"]
    snap = c.groups["story"][0]
    assert (snap.id, snap.media_type, snap.overlay_url) == ("s1", "image", "https://cdn/o1")
    assert snap.stem == "20261122_014925_s1"
    assert c.metadata["story"]["snap_count"] == 1 and c.metadata["spotlight_count"] == 0


def test_parse_user_missing_profile():
    with pytest.raises(SnapchatError):
        parse_user("<html></html>", "nobody")


def test_parse_search(explore_html):
    c = parse_search(explore_html, "disney")
    counts = c.metadata["counts"]
    assert counts["spotlight"] == 15 and counts["profiles"] == 17 and counts["places"] == 10
    spot = c.groups["spotlight"]
    assert len(spot) == 15
    first = spot[0]
    assert first.media_type == "video" and first.author == "cryybabyytee"
    assert first.timestamp and first.extra["views"] > 0
    assert "disney" in search_usernames(c)


def test_parse_search_no_payload():
    with pytest.raises(SnapchatError):
        parse_search("<html></html>", "x")


def test_parse_place(place_html):
    c = parse_place(place_html, PLACE_ID)
    assert c.metadata["place"]["placeInfo"]["address"]["locality"] == "Anaheim"
    assert len(c.groups["snaps"]) == 35
    subs = [g for g in c.groups if g.startswith("sub_places/")]
    assert len(subs) == 9  # 10 sub-places, one has no snaps
    assert sum(len(c.groups[g]) for g in subs) == 155
    snap = c.groups["snaps"][0]
    assert snap.author and snap.timestamp and snap.timestamp < 10**10  # seconds, not ms


def test_parse_place_without_sub_places(place_html):
    c = parse_place(place_html, PLACE_ID, include_sub_places=False)
    assert list(c.groups) == ["snaps"]


def test_rsc_helpers():
    assert clean_rsc(
        {"a": "$undefined", "b": "$$x", "c": "$29:props:x", "d": ["$undefined", 1]},
    ) == {
        "a": None,
        "b": "$x",
        "c": None,
        "d": [None, 1],
    }
    assert unwrap({"value": "x"}) == "x"
    assert unwrap({"value": "x", "other": 1}) == {"value": "x", "other": 1}
