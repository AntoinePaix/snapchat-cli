"""Pydantic models for the raw JSON that snapchat.com embeds in its pages.

Conventions:

* Python names are snake_case; the camelCase JSON keys are aliases (``to_camel``).
* ``extra="allow"`` keeps every field we don't model, so ``model_dump(by_alias=True)``
  round-trips the original data into ``manifest.json``.
* Snapchat wraps many protobuf optionals as ``{"value": x}``; :data:`Wrapped` types
  unwrap them. ``null`` lists and dicts become empty ones.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated
from typing import Any
from typing import TypeVar

from pydantic import BaseModel
from pydantic import BeforeValidator
from pydantic import ConfigDict
from pydantic import Field
from pydantic import TypeAdapter
from pydantic import ValidationError
from pydantic import field_validator
from pydantic.alias_generators import to_camel

from snapchat_cli.client import SnapchatError

T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"value"}:
        value = value["value"]
    return None if value == "" else value


WrappedStr = Annotated[str | None, BeforeValidator(_unwrap)]
WrappedInt = Annotated[int | None, BeforeValidator(_unwrap)]
List = Annotated[list[T], BeforeValidator(lambda v: v or [])]
Dict = Annotated[dict[str, T], BeforeValidator(lambda v: v or {})]


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

    def dump(self) -> dict[str, Any]:
        """Original-looking JSON (camelCase keys, unknown fields kept)."""
        return self.model_dump(mode="json", by_alias=True)


def validate(model: type[M], data: Any, what: str) -> M:
    """Validate ``data``, turning pydantic errors into a readable :class:`SnapchatError`."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise SnapchatError(
            f"Unexpected {what} data (snapchat.com layout changed?): {exc}",
        ) from exc


# --------------------------------------------------------------------------- stories (profiles)


class SnapUrls(Model):
    media_url: str | None = None
    media_preview_url: WrappedStr = None
    overlay_url: WrappedStr = None
    attachment_url: WrappedStr = None


class StorySnap(Model):
    snap_index: int | None = None
    snap_id: WrappedStr = None
    snap_media_type: int | None = None
    snap_urls: SnapUrls | None = None
    timestamp_in_sec: WrappedInt = None
    snap_title: WrappedStr = None
    snap_subtitles: Any = None
    lat: float | None = None
    lng: float | None = None
    is_sponsored: bool | None = None


class Story(Model):
    story_id: WrappedStr = None
    highlight_id: WrappedStr = None
    story_title: WrappedStr = None
    story_subtitle: WrappedStr = None
    thumbnail_url: WrappedStr = None
    snap_list: List[StorySnap] = []

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.story_id,
            "highlight_id": self.highlight_id,
            "title": self.story_title,
            "subtitle": self.story_subtitle,
            "thumbnail_url": self.thumbnail_url,
            "snap_count": len(self.snap_list),
        }


_RESIZE_SUFFIX_RE = re.compile(r"\._RS\d+,\d+_FM[a-z]+$")


def original_image_url(url: str | None) -> str | None:
    """URL of the original, uncropped image behind a resized CDN rendition.

    ``cf-st.sc-cdn.net/aps/bolt/<id>`` URLs end with a resize directive such as
    ``._RS0,90_FMjpeg`` (a 90x90 crop). Replacing it with ``.jpeg`` makes the CDN
    serve the original image as JPEG (it ignores the extension, but needs one).
    """
    if not url:
        return url
    return _RESIZE_SUFFIX_RE.sub(".jpeg", url)


class ProfileInfo(Model):
    username: str
    title: str | None = None
    bio: str | None = None
    profile_picture_url: str | None = None
    square_hero_image_url: str | None = None
    related_accounts_info: List[dict[str, Any]] = []

    @field_validator("profile_picture_url", "square_hero_image_url")
    @classmethod
    def _original_image(cls, value: str | None) -> str | None:
        return original_image_url(value)


class UserPageProps(Model):
    user_profile: ProfileInfo | None = None
    story: Story | None = None
    curated_highlights: List[Story] = []
    spotlight_highlights: List[Story] = []
    lenses: List[dict[str, Any]] = []

    @field_validator("user_profile", mode="before")
    @classmethod
    def _pick_case(cls, value: Any) -> Any:
        # {"$case": "publicProfileInfo", "publicProfileInfo": {...}}: keep the populated oneof branch.
        if isinstance(value, dict) and "$case" in value:
            return value.get(value["$case"]) or None
        return value


# --------------------------------------------------------------------------- places


class PlaceInfo(Model):
    name: str | None = None


class PlaceProfile(Model):
    place_id: str | None = None
    place_info: PlaceInfo = PlaceInfo()


class VideoMetadata(Model):
    video_url: str | None = None
    thumbnail_url: str | None = None
    media_type: str | None = None


class PlaceSnap(Model):
    snap_id: str | None = None
    story_thumbnail_url: str | None = None
    poster_username: str | None = None
    poster_display_name: str | None = None
    poster_user_id: str | None = None
    posted_timestamp: int | None = None  # milliseconds
    video_metadata: VideoMetadata | None = None
    orbis_story_type: str | None = None


class SubPlace(Model):
    place_id: str
    place_name: str | None = None


class PlaceComponent(Model):
    component_title: str | None = None
    places_info: List[SubPlace] = []


class PlacePageProps(Model):
    place_profile: PlaceProfile | None = None
    place_components: List[PlaceComponent] = []
    story_thumbnail_data: List[PlaceSnap] = Field(default=[], alias="storythumbnailData")
    component_thumbnail_data: Dict[List[PlaceSnap]] = {}
    breadcrumbs: List[dict[str, Any]] = []
    place_pivots: List[Any] = []


# --------------------------------------------------------------------------- search (explore)


class SearchSection(Model):
    kind: str = "unknown"
    title: str | None = None
    items: List[dict[str, Any]] = []


SearchSections = TypeAdapter(list[SearchSection])


class SpotlightItem(Model):
    id: str | None = None
    snap_id: str | None = None
    title: str | None = None
    creator_name: str | None = None
    description: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    share_count: int | None = None
    thumbnail_url: str | None = None
    video_preview_url: str | None = None
    href: str | None = None


class VideoObject(Model):
    """schema.org VideoObject from the page's JSON-LD."""

    url: str | None = None
    content_url: str | None = None
    upload_date: datetime | None = None
    duration: str | None = None
    keywords: List[str] = []
    width: int | None = None
    height: int | None = None


# --------------------------------------------------------------------------- nearby (local API)


class SnakeModel(BaseModel):
    """For the /api/local endpoints, whose JSON keys are already snake_case."""

    model_config = ConfigDict(extra="allow")

    def dump(self) -> dict[str, Any]:
        # by_alias: nested camelCase models (PlaceSnap) keep their original keys.
        return self.model_dump(mode="json", by_alias=True)


class LatLng(SnakeModel):
    lat: float
    lng: float


class NearbyPlace(SnakeModel):
    place_id: str
    place_name: str | None = None
    category_display: str | None = None
    place_url: str | None = None
    location: LatLng | None = None
    address: str | None = None


class NearbyQuery(SnakeModel):
    lat: float
    lng: float
    category: str


class NearbyCategory(SnakeModel):
    slug: str | None = None
    display_name: str | None = None


class NearbyResponse(SnakeModel):
    """``GET /api/local/near-me?category=&lat=&lng=&locale=`` (20 places max)."""

    query: NearbyQuery
    category: NearbyCategory | None = None
    places: List[NearbyPlace] = []
    # Stories use the same camelCase shape as the place pages.
    stories_by_place_id: Dict[List[PlaceSnap]] = {}
