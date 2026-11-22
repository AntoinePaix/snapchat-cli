import pytest

from snapchat_cli.client import SnapchatError
from snapchat_cli.schemas import PlacePageProps
from snapchat_cli.schemas import Story
from snapchat_cli.schemas import UserPageProps
from snapchat_cli.schemas import original_image_url
from snapchat_cli.schemas import validate

BOLT = "https://cf-st.sc-cdn.net/aps/bolt/aHR0cHM6Ly9jZi1zdC5zYy1jZG4ubmV0L2Qv"


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        ("._RS0,90_FMjpeg", ".jpeg"),
        ("._RS0,640_FMpng", ".jpeg"),  # the CDN serves the original as JPEG anyway
        ("._RS0,1080_FMjpeg", ".jpeg"),
        (".jpeg", ".jpeg"),  # already the original
        ("", ""),  # no resize directive: untouched
    ],
)
def test_original_image_url(suffix, expected):
    assert original_image_url(BOLT + suffix) == BOLT + expected
    assert original_image_url(None) is None


def test_profile_picture_is_original():
    props = UserPageProps.model_validate(
        {
            "userProfile": {
                "$case": "publicProfileInfo",
                "publicProfileInfo": {
                    "username": "someone",
                    "profilePictureUrl": BOLT + "._RS0,90_FMjpeg",
                    "squareHeroImageUrl": BOLT + "._RS0,1080_FMjpeg",
                },
            },
        },
    )
    profile = props.user_profile
    assert profile.profile_picture_url == profile.square_hero_image_url == BOLT + ".jpeg"
    dumped = profile.dump()
    assert dumped["profilePictureUrl"] == dumped["squareHeroImageUrl"] == BOLT + ".jpeg"


def test_story_unwraps_values_and_aliases():
    story = Story.model_validate(
        {
            "storyId": {"value": ""},
            "storyTitle": {"value": "#baking"},
            "snapList": [
                {
                    "snapId": {"value": "abc"},
                    "snapMediaType": 1,
                    "timestampInSec": {"value": "1795312165"},
                    "snapUrls": {
                        "mediaUrl": "https://cdn/x",
                        "mediaPreviewUrl": {"value": "https://cdn/p"},
                    },
                    "somethingNew": 42,
                },
            ],
        },
    )
    assert story.story_id is None and story.story_title == "#baking"
    snap = story.snap_list[0]
    assert snap.snap_id == "abc" and snap.timestamp_in_sec == 1795312165
    assert snap.snap_urls.media_preview_url == "https://cdn/p"
    assert snap.dump()["somethingNew"] == 42  # unknown fields are kept


def test_null_lists_become_empty():
    props = UserPageProps.model_validate({"curatedHighlights": None, "story": {"snapList": None}})
    assert props.curated_highlights == [] and props.story.snap_list == []
    place = PlacePageProps.model_validate(
        {"storythumbnailData": None, "componentThumbnailData": None},
    )
    assert place.story_thumbnail_data == [] and place.component_thumbnail_data == {}


def test_profile_case_branch():
    props = UserPageProps.model_validate(
        {"userProfile": {"$case": "userInfo", "userInfo": {"username": "someone", "title": "S"}}},
    )
    assert props.user_profile.username == "someone"


def test_validate_wraps_errors():
    with pytest.raises(SnapchatError, match="layout changed"):
        validate(
            UserPageProps,
            {"userProfile": {"$case": "x", "x": {"title": "missing username"}}},
            "profile page",
        )
