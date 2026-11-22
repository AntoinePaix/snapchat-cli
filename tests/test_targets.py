import pytest

from snapchat_cli.targets import Target
from snapchat_cli.targets import parse_target
from snapchat_cli.targets import slugify

PLACE_ID = "b0fe0fce-9994-11e8-9ce9-bf074d92c723"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://www.snapchat.com/@nasa", Target("user", "nasa")),
        ("https://www.snapchat.com/@nasa/highlight/abc", Target("user", "nasa")),
        ("snapchat.com/add/nasa", Target("user", "nasa")),
        ("https://www.snapchat.com/explore/disney", Target("search", "disney")),
        ("https://www.snapchat.com/explore/disney%20land", Target("search", "disney land")),
        (
            f"https://www.snapchat.com/place/disney-california-adventure-park/{PLACE_ID}",
            Target("place", PLACE_ID, "disney-california-adventure-park"),
        ),
    ],
)
def test_parse_urls(value, expected):
    assert parse_target(value) == expected


def test_bare_values():
    assert parse_target("@someone", "user") == Target("user", "someone")
    assert parse_target("disney land", "search") == Target("search", "disney land")
    assert parse_target(PLACE_ID.upper(), "place") == Target("place", PLACE_ID)


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("not a user!", "user"),
        ("not-a-place", "place"),
        ("https://example.com/@x", None),
        ("https://www.snapchat.com/explore/disney", "user"),  # kind mismatch
        ("nasa", None),  # ambiguous without a kind
    ],
)
def test_invalid(value, kind):
    with pytest.raises(ValueError):
        parse_target(value, kind)


def test_slugify():
    assert slugify("#baking") == "baking"
    assert (
        slugify("The Little Mermaid - Ariel's Undersea Adventure")
        == "The_Little_Mermaid_-_Ariel_s_Undersea_Adventure"
    )
    assert slugify("ديزني") == "ديزني"
    assert slugify("///") == "untitled"
