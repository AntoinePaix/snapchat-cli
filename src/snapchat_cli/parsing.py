"""Extract the JSON state embedded in snapchat.com HTML pages.

snapchat.com serves two kinds of Next.js pages:

* Pages Router pages (profiles ``/@user``, places ``/place/...``) embed their
  whole state in ``<script id="__NEXT_DATA__">``.
* App Router pages (``/explore/<query>``, spotlight pages) stream React Server
  Component payloads via ``self.__next_f.push([1, "..."])`` script tags.

Both also carry schema.org ``application/ld+json`` blocks.
"""

from __future__ import annotations

import json
import re
from typing import Any

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)
_NEXT_F_RE = re.compile(r"self\.__next_f\.push\((\[.*?\])\)</script>", re.DOTALL)
_LD_JSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)

_decoder = json.JSONDecoder()


def next_data(html: str) -> dict[str, Any] | None:
    """Return the parsed ``__NEXT_DATA__`` object, or ``None`` if absent."""
    match = _NEXT_DATA_RE.search(html)
    return json.loads(match.group(1)) if match else None


def page_props(html: str) -> dict[str, Any]:
    """Shortcut for ``__NEXT_DATA__.props.pageProps`` (empty dict if absent)."""
    data = next_data(html) or {}
    return data.get("props", {}).get("pageProps", {}) or {}


def rsc_payload(html: str) -> str:
    """Concatenate the text chunks of the React Server Components stream."""
    parts: list[str] = []
    for raw in _NEXT_F_RE.findall(html):
        try:
            chunk = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if len(chunk) > 1 and chunk[0] == 1 and isinstance(chunk[1], str):
            parts.append(chunk[1])
    return "".join(parts)


def find_json_value(text: str, key: str) -> Any | None:
    """Decode the JSON value following the first ``"key":`` in ``text``."""
    needle = f'"{key}":'
    idx = text.find(needle)
    if idx < 0:
        return None
    value, _ = _decoder.raw_decode(text, idx + len(needle))
    return clean_rsc(value)


def clean_rsc(value: Any) -> Any:
    """Normalise RSC-specific encodings (``"$undefined"``, ``"$$"`` escapes, refs)."""
    if isinstance(value, dict):
        return {k: clean_rsc(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_rsc(v) for v in value]
    if isinstance(value, str) and value.startswith("$"):
        if value.startswith("$$"):
            return value[1:]
        # "$undefined", or a "$<id>:path" back-reference to an earlier value: drop it.
        if value == "$undefined" or re.match(r"^\$[0-9a-f]+:", value):
            return None
    return value


def ld_json(html: str) -> list[dict[str, Any]]:
    """All schema.org JSON-LD blocks in the page."""
    blocks = []
    for raw in _LD_JSON_RE.findall(html):
        try:
            blocks.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return blocks


def unwrap(value: Any) -> Any:
    """Snapchat wraps many protobuf optionals as ``{"value": x}``; unwrap them."""
    if isinstance(value, dict) and set(value) == {"value"}:
        return value["value"]
    return value
