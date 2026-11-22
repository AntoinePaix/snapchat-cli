# snapchat-cli

Download public snaps and metadata from the snapchat.com web app: profiles, search (explore) pages and places.

It needs no login and no API key. It reads the same server-rendered data that www.snapchat.com sends to a browser, using [curl_cffi](https://github.com/lexiforest/curl_cffi) with browser TLS impersonation and `asyncio` to download media in parallel.

## Install

```bash
uv pip install -e .          # or: pip install -e .
uv pip install -e '.[dev]'   # with test dependencies
```

This installs the `snapchat` command (also runnable as `python -m snapchat_cli`).

## Usage

```bash
# Profile: current story, curated highlights, spotlight snaps and profile info
snapchat user nasa
snapchat user https://www.snapchat.com/@nasa --only highlights

# Search / explore: spotlight videos, plus profiles, lenses, places and shows as metadata
snapchat search disney
snapchat search "disney land" --profiles --max-profiles 5   # also download the profiles found

# Place: details, hours, reviews, photos, plus snaps posted there and in sub-places
snapchat place https://www.snapchat.com/place/disney-california-adventure-park/b0fe0fce-9994-11e8-9ce9-bf074d92c723
snapchat place b0fe0fce-9994-11e8-9ce9-bf074d92c723 --no-sub-places

# Any mix of URLs: the page type is detected for you
snapchat get https://www.snapchat.com/@nasa https://www.snapchat.com/explore/disney
```

Options shared by every command:

| Option | Default | Meaning |
|---|---|---|
| `-o, --output DIR` | `downloads` | Download root folder |
| `-c, --concurrency N` | `8` | Parallel media downloads |
| `-m, --metadata-only` | off | Write `manifest.json` only, skip media |
| `--previews` | off | Also save thumbnails |
| `--overlays / --no-overlays` | on | Save caption/sticker overlay images |
| `--raw` | off | Also save the untouched page state as `raw.json` |
| `-x, --proxy PROXY` | none | Proxy to use; repeat for several (see [Proxies](#proxies)) |
| `--proxy-file FILE` | none | File with one proxy per line |
| `--proxy-rotation` | `round-robin` | `round-robin`, `random` or `sticky` |
| `--proxy-media / --no-proxy-media` | on | Also send media downloads through the proxies |
| `--impersonate NAME` | `chrome` | curl_cffi browser fingerprint |
| `--locale TAG` | `en-US` | `Accept-Language` sent with requests; sets the language of category names, review text and similar strings |
| `-v` / `-vv` | | Info or debug logging |

Files that are already downloaded are skipped, so running a command again only fetches new snaps.

## Proxies

```bash
snapchat user nasa -x socks5h://user:pass@proxy.example:1080
snapchat search disney -x 10.0.0.1:3128 -x 10.0.0.2:3128:user:pass --proxy-rotation random
snapchat place <url> --proxy-file proxies.txt --no-proxy-media   # proxies for pages only
```

- **Formats**: `scheme://[user:pass@]host:port` (`http`, `https`, `socks4`, `socks4a`, `socks5`, `socks5h`), `host:port`, or `host:port:user:pass`. The two short forms are treated as HTTP proxies.
- **Proxy file**: one proxy per line. Blank lines and `#` comments are ignored.
- **Environment variables**: `SNAPCHAT_PROXY` (space-separated list) and `SNAPCHAT_PROXY_FILE` set the same options as `--proxy` and `--proxy-file`. Without any proxy option, curl's own `HTTPS_PROXY` and `ALL_PROXY` variables still apply.
- **Rotation**: a proxy is picked for every request.
  - `round-robin` cycles through the list.
  - `random` picks one at random.
  - `sticky` keeps the first working proxy until it fails.
- **Failures**: if a request fails through a proxy (connection error, or HTTP 403, 407 or 429), the retry goes through a different proxy after a short pause. A proxy that fails 3 times in a row is skipped for 60 seconds. If every proxy is being skipped, they are all used anyway.
- **Logs**: proxy passwords are shown as `***`.
- **`--no-proxy-media`**: page requests go through the proxies, while media downloads connect directly. Media is most of the traffic, so this saves proxy bandwidth.

## Output layout

```
downloads/
├── user/nasa/
│   ├── manifest.json                     # profile, highlight index, lenses + every snap
│   ├── story/20261122_101500_<snapId>.jpg
│   ├── highlights/Launch_of_Artemis_II_c19cac68/...
│   └── spotlight/...
├── search/disney/
│   ├── manifest.json                     # results by kind: spotlight, profiles, lenses, places...
│   └── spotlight/<date>_<snapId>.mp4
└── place/Disney_California_Adventure_b0fe0fce/
    ├── manifest.json                     # place profile, hours, ratings, reviews, sub-places
    ├── snaps/...
    └── sub_places/<name>_<id>/...
```

File names start with the UTC post date, so they sort in date order. Each snap in `manifest.json` records its source URLs, author, timestamp, extra stats (views, likes and so on, when the page has them) and the local `file` it was saved to.

## Library use

```python
import asyncio
from pathlib import Path
from snapchat_cli import SnapchatClient
from snapchat_cli import fetch_user
from snapchat_cli.downloader import download_collection


async def main():
    async with SnapchatClient() as client:
        profile = await fetch_user(client, "nasa")
        print(profile.metadata["profile"]["title"], profile.snap_count)
        await download_collection(client, profile, Path("downloads"))


asyncio.run(main())
```

To use proxies from code, pass them to the client: `SnapchatClient(proxies=["socks5h://host:1080", "10.0.0.2:3128"], proxy_rotation="random")`, or pass a `ProxyPool` you built yourself.

## How it works

| Page | Rendering | Data source |
|---|---|---|
| `/@user` | Next.js Pages Router | `__NEXT_DATA__` → `userProfile`, `story`, `curatedHighlights`, `spotlightHighlights` |
| `/place/<slug>/<id>` | Pages Router | `__NEXT_DATA__` → `placeProfile`, `storythumbnailData`, `componentThumbnailData` |
| `/explore/<query>` | App Router (RSC) | `self.__next_f` stream → `sections[]`, plus schema.org `VideoObject` JSON-LD |

Pydantic models in `src/snapchat_cli/schemas.py` validate the raw page JSON. They map camelCase keys to snake_case, unwrap Snapchat's `{"value": x}` wrappers and keep unknown fields. `src/snapchat_cli/extractors.py` turns the validated data into `Snap` and `Collection` objects through pure `parse_*` functions, which are tested offline against saved pages in `tests/fixtures/`. If snapchat.com changes its data, you get a clear "layout changed?" error instead of a `KeyError`. To fix it, refresh the fixtures and adjust the schemas. Media and JSON files are written with `aiofiles`.

## Limitations

- Only public content that www.snapchat.com shows to a logged-out visitor is available. Private accounts and friends-only stories are not.
- The web app returns a fixed set of results per page (for example about 15 to 25 spotlight items per search), and it never loads more. Its client does not use the `*Cursor` fields in the page data.
- Story snaps expire after 24 hours, so their media URLs stop working after that.

## Tests and lint

```bash
pytest -m "not network"   # offline parser tests
pytest                    # plus a live smoke test against snapchat.com

ruff check --fix src tests && ruff format src tests   # one import per line, trailing commas (COM812), formatting

pre-commit install           # once: lint, format and secret checks on every commit (needs a git repo)
pre-commit run --all-files   # run all hooks by hand
```

The pre-commit hooks block `.env` files, `.har` captures and `token.json` files, scan staged files for secrets with gitleaks, refuse files over 1 MB (such as downloaded snaps), and run ruff.

## Repository layout

```
src/snapchat_cli/   the package (client, parsing, extractors, downloader, CLI)
tests/              pytest suite + gzipped HTML fixtures
research/           earlier reverse-engineering notes (Lens Studio gRPC, Android MITM); not part of the package
```

Use this tool only for content you are allowed to download, and respect Snapchat's Terms of Service and the rights of the people who posted it.
