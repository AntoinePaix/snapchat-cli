"""Turn snapchat.com pages into :class:`~snapchat_cli.models.Collection` objects.

Each ``fetch_*`` coroutine downloads one page and delegates to a pure
``parse_*`` function, which can be tested offline against saved HTML.
Raw page JSON is validated with the pydantic models in :mod:`snapchat_cli.schemas`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
from typing import Any
from urllib.parse import quote
from urllib.parse import urlencode

from pydantic import ValidationError

from snapchat_cli.client import BASE_URL
from snapchat_cli.client import NotFoundError
from snapchat_cli.client import SnapchatClient
from snapchat_cli.client import SnapchatError
from snapchat_cli.models import Collection
from snapchat_cli.models import MediaType
from snapchat_cli.models import Snap
from snapchat_cli.parsing import find_json_value
from snapchat_cli.parsing import ld_json
from snapchat_cli.parsing import page_props
from snapchat_cli.parsing import rsc_payload
from snapchat_cli.schemas import NearbyQuery
from snapchat_cli.schemas import NearbyResponse
from snapchat_cli.schemas import PlacePageProps
from snapchat_cli.schemas import PlaceSnap
from snapchat_cli.schemas import SearchSections
from snapchat_cli.schemas import SpotlightItem
from snapchat_cli.schemas import Story
from snapchat_cli.schemas import StorySnap
from snapchat_cli.schemas import UserPageProps
from snapchat_cli.schemas import VideoObject
from snapchat_cli.schemas import validate
from snapchat_cli.targets import slugify

log = logging.getLogger(__name__)

_STORY_MEDIA_TYPES: dict[int | None, MediaType] = {0: "image", 1: "video"}


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v not in (None, [], {})}


def _story_snap(raw: StorySnap, author: str | None) -> Snap | None:
    """Snap from a ``story.snapList[]`` entry (profiles, highlights, spotlight highlights)."""
    urls = raw.snap_urls
    if urls is None or not urls.media_url:
        return None
    return Snap(
        id=raw.snap_id or "",
        media_url=urls.media_url,
        media_type=_STORY_MEDIA_TYPES.get(raw.snap_media_type, "unknown"),
        preview_url=urls.media_preview_url,
        overlay_url=urls.overlay_url,
        timestamp=raw.timestamp_in_sec,
        author=author,
        title=raw.snap_title,
        extra=_compact(
            {
                "index": raw.snap_index,
                "lat": raw.lat,
                "lng": raw.lng,
                "subtitles": raw.snap_subtitles,
                "attachment_url": urls.attachment_url,
                "is_sponsored": raw.is_sponsored,
            },
        ),
    )


def _story_snaps(story: Story | None, author: str | None) -> list[Snap]:
    if story is None:
        return []
    return [s for s in (_story_snap(raw, author) for raw in story.snap_list) if s is not None]


# --------------------------------------------------------------------------- user


def user_url(username: str) -> str:
    return f"{BASE_URL}/@{quote(username)}"


def parse_user(html: str, username: str) -> Collection:
    raw_props = page_props(html)
    props = validate(UserPageProps, raw_props, "profile page")
    info = props.user_profile
    if info is None:
        raise SnapchatError(
            f"No profile data found for @{username} (private or unavailable account?)",
        )
    username = info.username or username

    groups: dict[str, list[Snap]] = {}
    story = _story_snaps(props.story, username)
    if story:
        groups["story"] = story

    highlights = []
    for n, hl in enumerate(props.curated_highlights):
        summary = hl.summary()
        highlights.append(summary)
        snaps = _story_snaps(hl, username)
        if snaps:
            hid = hl.highlight_id or hl.story_id or str(n)
            groups[f"highlights/{slugify(hl.story_title or 'highlight')}_{hid[:8]}"] = snaps

    spotlight = [snap for sp in props.spotlight_highlights for snap in _story_snaps(sp, username)]
    if spotlight:
        groups["spotlight"] = spotlight

    metadata = {
        "profile": info.dump(),
        "story": props.story.summary() if props.story else None,
        "highlights": highlights,
        "spotlight_count": len(spotlight),
        "lenses": props.lenses,
        "related_accounts": info.related_accounts_info,
    }
    return Collection(
        kind="user",
        key=username,
        folder=f"user/{slugify(username)}",
        url=user_url(username),
        metadata=metadata,
        groups=groups,
        raw=raw_props,
    )


async def fetch_user(client: SnapchatClient, username: str) -> Collection:
    return parse_user(await client.get_page(user_url(username)), username)


# --------------------------------------------------------------------------- search


def search_url(query: str) -> str:
    return f"{BASE_URL}/explore/{quote(query, safe='')}"


def _ld_videos(html: str) -> dict[str, VideoObject]:
    """Map spotlight snap id -> schema.org VideoObject (has uploadDate, duration...)."""
    videos: dict[str, VideoObject] = {}
    for block in ld_json(html):
        for item in block.get("itemListElement") or []:
            if not isinstance(item, dict) or item.get("@type") != "VideoObject":
                continue
            try:
                video = VideoObject.model_validate(item)
            except ValidationError:
                continue  # JSON-LD only enriches results; never fail on it
            if video.url:
                videos[video.url.rstrip("/").rsplit("/", 1)[-1]] = video
    return videos


def _spotlight_snap(item: SpotlightItem, video: VideoObject | None) -> Snap | None:
    media_url = (video and video.content_url) or item.video_preview_url
    if not media_url:
        return None
    href = item.href or ""
    author = href.split("/")[1].lstrip("@") if href.startswith("/@") else None
    return Snap(
        id=item.snap_id or item.id or "",
        media_url=media_url,
        media_type="video",
        preview_url=item.thumbnail_url,
        timestamp=int(video.upload_date.timestamp()) if video and video.upload_date else None,
        author=author,
        title=item.description or item.title,
        extra=_compact(
            {
                "creator_name": item.creator_name,
                "views": item.view_count,
                "likes": item.like_count,
                "comments": item.comment_count,
                "shares": item.share_count,
                "keywords": video.keywords if video else None,
                "duration": video.duration if video else None,
                "width": video.width if video else None,
                "height": video.height if video else None,
                "page_url": f"{BASE_URL}{href}" if href else None,
            },
        ),
    )


def parse_search(html: str, query: str) -> Collection:
    raw_sections = find_json_value(rsc_payload(html), "sections")
    if raw_sections is None:
        raise SnapchatError(f"No search results payload found for {query!r} (page layout changed?)")
    try:
        sections = SearchSections.validate_python(raw_sections)
    except ValidationError as exc:
        raise SnapchatError(
            f"Unexpected search results data (snapchat.com layout changed?): {exc}",
        ) from exc

    videos = _ld_videos(html)
    spotlight: list[Snap] = []
    results: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        results.setdefault(section.kind, []).extend(section.items)
        if section.kind != "spotlight":
            continue
        for raw_item in section.items:
            item = validate(SpotlightItem, raw_item, "spotlight result")
            snap = _spotlight_snap(item, videos.get(item.snap_id or item.id or ""))
            if snap:
                spotlight.append(snap)

    return Collection(
        kind="search",
        key=query,
        folder=f"search/{slugify(query)}",
        url=search_url(query),
        metadata={
            "query": query,
            "counts": {k: len(v) for k, v in results.items()},
            "results": results,
        },
        groups={"spotlight": spotlight} if spotlight else {},
        raw={"sections": raw_sections},
    )


async def fetch_search(client: SnapchatClient, query: str) -> Collection:
    return parse_search(await client.get_page(search_url(query)), query)


def search_usernames(collection: Collection) -> list[str]:
    """Usernames of the accounts listed in search results ("profiles"/"users" sections)."""
    names: list[str] = []
    for kind in ("profiles", "users"):
        for item in collection.metadata["results"].get(kind, []):
            name = item.get("username") or (item.get("href") or "").rstrip("/").rsplit("@", 1)[-1]
            if name and name not in names:
                names.append(name)
    return names


# --------------------------------------------------------------------------- place


def place_url(place_id: str, slug: str | None = None) -> str:
    # The slug is cosmetic: snapchat.com resolves the page from the id alone.
    return f"{BASE_URL}/place/{quote(slug or 'place')}/{place_id}"


def _place_snap(raw: PlaceSnap) -> Snap | None:
    video = raw.video_metadata
    if video is None or not video.video_url:
        return None
    return Snap(
        id=raw.snap_id or "",
        media_url=video.video_url,
        # Place media types aren't documented ("1" is video); the downloader trusts Content-Type.
        media_type="video" if video.media_type == "1" else "unknown",
        preview_url=video.thumbnail_url or raw.story_thumbnail_url,
        timestamp=raw.posted_timestamp // 1000 if raw.posted_timestamp else None,
        author=raw.poster_username or None,
        extra=_compact(
            {
                "poster_display_name": raw.poster_display_name or None,
                "poster_user_id": raw.poster_user_id,
                "story_type": raw.orbis_story_type,
                "raw_media_type": video.media_type,
            },
        ),
    )


def _place_snaps(items: list[PlaceSnap]) -> list[Snap]:
    return [s for s in (_place_snap(i) for i in items) if s is not None]


def parse_place(html: str, place_id: str, include_sub_places: bool = True) -> Collection:
    raw_props = page_props(html)
    props = validate(PlacePageProps, raw_props, "place page")
    profile = props.place_profile
    if profile is None:
        raise SnapchatError(f"No place data found for {place_id}")
    name = profile.place_info.name or place_id

    sub_places = [
        {**sub.dump(), "component": component.component_title}
        for component in props.place_components
        for sub in component.places_info
    ]
    names = {sub.place_id: sub.place_name or sub.place_id for c in props.place_components for sub in c.places_info}

    groups: dict[str, list[Snap]] = {}
    snaps = _place_snaps(props.story_thumbnail_data)
    if snaps:
        groups["snaps"] = snaps
    if include_sub_places:
        for sub_id, items in props.component_thumbnail_data.items():
            sub_snaps = _place_snaps(items)
            if sub_snaps:
                groups[f"sub_places/{slugify(names.get(sub_id, sub_id))}_{sub_id[:8]}"] = sub_snaps

    metadata = {
        "place": profile.dump(),
        "sub_places": sub_places,
        "breadcrumbs": props.breadcrumbs,
        "pivots": props.place_pivots,
    }
    return Collection(
        kind="place",
        key=place_id,
        folder=f"place/{slugify(name)}_{place_id[:8]}",
        url=place_url(place_id, slugify(name).lower()),
        metadata=metadata,
        groups=groups,
        raw=raw_props,
    )


async def fetch_place(
    client: SnapchatClient,
    place_id: str,
    slug: str | None = None,
    include_sub_places: bool = True,
) -> Collection:
    html = await client.get_page(place_url(place_id, slug))
    return parse_place(html, place_id, include_sub_places)


# --------------------------------------------------------------------------- nearby

# Categories of snapchat.com/local "near me" pages (one per /api/local/near-me call).
NEARBY_CATEGORIES = (
    "amusement-parks", "bakeries", "bars", "bbq-restaurants", "beaches",
    "breakfast-restaurants", "breweries", "brunch", "chicken-wings", "cocktail-bars",
    "coffee-shops", "dessert-shops", "diners", "donut-shops", "family-activities",
    "food-trucks", "hookah-bars", "ice-cream-shops", "indian-restaurants",
    "italian-restaurants", "japanese-restaurants", "juice-bars", "korean-restaurants",
    "late-night-food", "live-music", "lounges", "mexican-restaurants", "museums",
    "music-venues", "nightclubs", "nightlife", "outdoor-activities", "pubs",
    "ramen-restaurants", "restaurants", "seafood-restaurants", "skate-parks", "sports-bars",
    "steakhouses", "sushi-restaurants", "thai-restaurants", "things-to-do",
    "tourist-attractions", "vietnamese-restaurants", "wineries",
)  # fmt: skip

_KM_PER_DEGREE = 111.32


def nearby_url(lat: float, lng: float, category: str, locale: str = "en-US") -> str:
    params = {"category": category, "lat": lat, "lng": lng, "locale": locale}
    return f"{BASE_URL}/api/local/near-me?{urlencode(params)}"


def distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle (haversine) distance."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def grid_points(
    lat: float,
    lng: float,
    size: int = 1,
    step_km: float = 1.0,
) -> list[tuple[float, float]]:
    """``size`` x ``size`` points centred on (lat, lng), ``step_km`` apart.

    The API returns at most 20 places per call, all very close to the point,
    so a grid is the only way to cover a wider area.
    """
    if size <= 1:
        return [(lat, lng)]
    offsets = [(i - (size - 1) / 2) * step_km for i in range(size)]
    dlat = 1 / _KM_PER_DEGREE
    dlng = 1 / (_KM_PER_DEGREE * max(math.cos(math.radians(lat)), 1e-6))
    return [
        (round(lat + dy * dlat, 6), round((lng + dx * dlng + 180) % 360 - 180, 6)) for dy in offsets for dx in offsets
    ]


def _same_query(query: NearbyQuery, lat: float, lng: float, category: str) -> bool:
    return query.category == category and abs(query.lat - lat) < 1e-6 and abs(query.lng - lng) < 1e-6


async def fetch_nearby_point(
    client: SnapchatClient,
    lat: float,
    lng: float,
    category: str,
    attempts: int = 3,
) -> NearbyResponse:
    """One ``/api/local/near-me`` call: up to 20 places of ``category`` around (lat, lng).

    The Google CDN in front of this endpoint caches responses for an hour and
    sometimes ignores lat/lng/category in its cache key, answering with another
    query's results. A unique private-use locale tag (``en-US-x-<random>``,
    still served in English) bypasses it, and the echoed ``query`` is checked.
    """
    for _ in range(attempts):
        locale = f"{client.locale}-x-{secrets.token_hex(4)}"
        try:
            text = await client.get_page(nearby_url(lat, lng, category, locale))
        except NotFoundError as exc:
            raise SnapchatError(
                f"Unknown nearby category {category!r} (see 'snapchat nearby --list-categories')",
            ) from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SnapchatError(f"Nearby API did not return JSON for {category!r}") from exc
        response = validate(NearbyResponse, data, "nearby results")
        if _same_query(response.query, lat, lng, category):
            return response
        log.info(
            "Stale cached nearby response (asked %s,%s %s, got %s), retrying",
            lat,
            lng,
            category,
            response.query.dump(),
        )
    raise SnapchatError(
        f"Nearby API kept returning cached results for another query ({category!r})",
    )


def parse_nearby(
    lat: float,
    lng: float,
    responses: list[tuple[str, NearbyResponse]],
    *,
    grid: int = 1,
    step_km: float = 1.0,
) -> Collection:
    """Merge (category, response) pairs: places deduplicated, sorted by distance."""
    categories = list(dict.fromkeys(category for category, _ in responses))
    places: dict[str, dict[str, Any]] = {}
    snaps: dict[str, dict[str, Snap]] = {}
    for category, response in responses:
        for place in response.places:
            entry = places.get(place.place_id)
            if entry is None:
                loc = place.location
                entry = places[place.place_id] = {
                    **place.dump(),
                    "distance_km": round(distance_km(lat, lng, loc.lat, loc.lng), 3) if loc else None,
                    "categories": [],
                }
            if category not in entry["categories"]:
                entry["categories"].append(category)
        for place_id, items in response.stories_by_place_id.items():
            bucket = snaps.setdefault(place_id, {})
            for snap in _place_snaps(items):
                bucket.setdefault(snap.id or snap.media_url, snap)

    ordered = sorted(
        places.values(),
        key=lambda p: (p["distance_km"] is None, p["distance_km"] or 0),
    )
    groups: dict[str, list[Snap]] = {}
    for place in ordered:
        place_snaps = list(snaps.get(place["place_id"], {}).values())
        place["snap_count"] = len(place_snaps)
        if place_snaps:
            name = place.get("place_name") or place["place_id"]
            groups[f"{slugify(name)}_{place['place_id'][:8]}"] = place_snaps

    metadata = {
        "center": {"lat": lat, "lng": lng},
        "categories": categories,
        "grid": {
            "size": grid,
            "step_km": step_km,
            "points": len(grid_points(lat, lng, grid, step_km)),
        },
        "place_count": len(ordered),
        "places": ordered,
    }
    return Collection(
        kind="nearby",
        key=f"{lat},{lng}",
        folder=f"nearby/{lat:.4f}_{lng:.4f}",
        url=nearby_url(lat, lng, categories[0] if categories else "things-to-do"),
        metadata=metadata,
        groups=groups,
        raw={"responses": [{"category": c, **r.dump()} for c, r in responses]},
    )


async def fetch_nearby(
    client: SnapchatClient,
    lat: float,
    lng: float,
    categories: list[str],
    *,
    grid: int = 1,
    step_km: float = 1.0,
    concurrency: int = 4,
) -> Collection:
    """Places (+ their recent snaps) around a point, for each category and grid point."""
    if not categories:
        raise SnapchatError("At least one nearby category is required")
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(point: tuple[float, float], category: str) -> tuple[str, NearbyResponse]:
        async with sem:
            return category, await fetch_nearby_point(client, point[0], point[1], category)

    tasks = [one(point, cat) for point in grid_points(lat, lng, grid, step_km) for cat in categories]
    responses = await asyncio.gather(*tasks)
    return parse_nearby(lat, lng, list(responses), grid=grid, step_km=step_km)
