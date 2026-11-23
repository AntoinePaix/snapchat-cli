# snapchat-cli

[![CI](https://github.com/AntoinePaix/snapchat-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/AntoinePaix/snapchat-cli/actions/workflows/ci.yml)

`snapchat` is a Python package and command-line tool that downloads public snaps and metadata from the
snapchat.com web app: profiles, search (explore) pages, places and places near a GPS point.

- No login and no API key: it reads the same server-rendered data that www.snapchat.com sends to a browser
- Requests with [curl_cffi](https://github.com/lexiforest/curl_cffi), impersonating a real browser's TLS fingerprint
- Parallel media downloads with `asyncio`; files already downloaded are skipped
- HTTP(S) and SOCKS proxies, with round-robin, random or sticky rotation and failover
- Page data validated with pydantic, so a layout change gives a clear error
- Command `snapchat`, or `python -m snapchat_cli`, or use it as a library

Requires Python 3.11 or later.

## Installation

Pick one of the options below. They all install the `snapchat` command.

### With uv

```bash
# As a tool, available everywhere:
uv tool install git+https://github.com/AntoinePaix/snapchat-cli

# Or from a clone (runs from the project's own virtual environment):
git clone https://github.com/AntoinePaix/snapchat-cli
cd snapchat-cli
uv sync
uv run snapchat --help
```

### With pip

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install git+https://github.com/AntoinePaix/snapchat-cli
```

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

# Nearby: places around a GPS point, plus their recent snaps, by category
snapchat nearby 48.8566,2.3522 -k restaurants
snapchat nearby -33.8688,151.2093 -k bars,museums -k coffee-shops   # several categories
snapchat nearby 40.7128,-74.006 -k things-to-do --grid 3 --step 1    # 3x3 points, 1 km apart
snapchat nearby --list-categories

# Any mix of URLs: the page type is detected for you
snapchat get https://www.snapchat.com/@nasa https://www.snapchat.com/explore/disney
```

From a clone installed with `uv sync`, prefix the commands with `uv run`. Files that are already downloaded are
skipped, so running a command again only fetches new snaps. The exit code is `1` if a target or a download failed.

### Common options

Same options, with the same names, in every command (and in the other scraper repos):

| Option | Default | Description |
| --- | --- | --- |
| `-o`, `--output` | `downloads` | Download root folder |
| `-c`, `--concurrency` | `8` | Parallel media downloads |
| `-x`, `--proxy` | | Proxy to use; repeat for several (see [Proxies](#proxies)) |
| `--proxy-file` | | File with one proxy per line |
| `--impersonate` | `chrome` | curl_cffi browser fingerprint |
| `--timeout` | `30` | Timeout of each request, in seconds |
| `--retries` | `3` | Retries per request on network errors or 429/5xx responses |
| `-v`, `--verbose` | | Show debug logs |
| `-q`, `--quiet` | | Only show warnings and errors |
| `--version` | | Show the version and exit |

Options specific to snapchat-cli:

| Option | Default | Description |
| --- | --- | --- |
| `-m`, `--metadata-only` | off | Write `manifest.json` only, skip media |
| `--previews` | off | Also save thumbnails |
| `--overlays / --no-overlays` | on | Save caption/sticker overlay images |
| `--raw` | off | Also save the untouched page state as `raw.json` |
| `--proxy-rotation` | `round-robin` | `round-robin`, `random` or `sticky` |
| `--proxy-media / --no-proxy-media` | on | Also send media downloads through the proxies |
| `--locale` | `en-US` | `Accept-Language` sent with requests; sets the language of category names, review text and similar strings |

### Nearby places

`snapchat nearby LAT,LNG` asks the API behind snapchat.com's "near me" pages for places around a point.

- **Categories**: the API takes one category per call, so the command makes one call per category and merges the
  results. Give several with `-k` repeated or comma-separated, or use `--all-categories` (45 calls).
  `--list-categories` shows them all: `restaurants`, `bars`, `coffee-shops`, `museums`, `tourist-attractions`,
  `things-to-do`, `nightclubs`…
- **Results**: each call returns at most 20 places, the closest to the point (usually within 1–2 km), with up to 5
  recent snaps per place. There is no radius or paging.
- **Wider areas**: to cover more ground, `--grid N --step KM` queries an N×N square of points centred on the
  location. That is N×N×categories calls, and places found more than once are merged.

## Proxies

```bash
snapchat user nasa -x socks5h://user:pass@proxy.example:1080
snapchat search disney -x 10.0.0.1:3128 -x 10.0.0.2:3128:user:pass --proxy-rotation random
snapchat place <url> --proxy-file proxies.txt --no-proxy-media   # proxies for pages only
```

- **Formats**: `scheme://[user:pass@]host:port` (`http`, `https`, `socks4`, `socks4a`, `socks5`, `socks5h`),
  `host:port`, or `host:port:user:pass`. The two short forms are treated as HTTP proxies.
- **Proxy file**: one proxy per line. Blank lines and `#` comments are ignored.
- **Environment variables**: `SNAPCHAT_PROXY` (space-separated list) and `SNAPCHAT_PROXY_FILE` set the same options
  as `--proxy` and `--proxy-file`. Without any proxy option, curl's own `HTTPS_PROXY` and `ALL_PROXY` variables
  still apply.
- **Rotation**: a proxy is picked for every request.
  - `round-robin` cycles through the list.
  - `random` picks one at random.
  - `sticky` keeps the first working proxy until it fails.
- **Failures**: if a request fails through a proxy (connection error, or HTTP 403, 407 or 429), the retry goes
  through a different proxy after a short pause. A proxy that fails 3 times in a row is skipped for 60 seconds. If
  every proxy is being skipped, they are all used anyway.
- **Logs**: proxy passwords are shown as `***`.
- **`--no-proxy-media`**: page requests go through the proxies, while media downloads connect directly. Media is
  most of the traffic, so this saves proxy bandwidth.

## Output

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
├── place/Disney_California_Adventure_b0fe0fce/
│   ├── manifest.json                     # place profile, hours, ratings, reviews, sub-places
│   ├── snaps/...
│   └── sub_places/<name>_<id>/...
└── nearby/48.8566_2.3522/
    ├── manifest.json                     # places sorted by distance, categories, grid
    └── <place name>_<id>/<date>_<snapId>.mp4
```

File names start with the UTC post date, so they sort in date order. Each snap in `manifest.json` records its
source URLs, author, timestamp, extra stats (views, likes and so on, when the page has them) and the local `file`
it was saved to. For `nearby`, `manifest.json` lists every place with its distance in km, the categories it
matched, its address, rating, opening hours and snap count, sorted by distance.

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

`SnapchatClient` accepts `impersonate`, `locale`, `proxies`, `proxy_rotation`, `proxy_media`, `timeout` and
`retries`. For example `SnapchatClient(proxies=["socks5h://host:1080", "10.0.0.2:3128"], proxy_rotation="random")`,
or pass a `ProxyPool` you built yourself. Errors raise `snapchat_cli.SnapchatError` (`NotFoundError` for a missing
page). The library logs with the `logging` module under the `snapchat_cli` logger.

## How it works

| Page | Rendering | Data source |
| --- | --- | --- |
| `/@user` | Next.js Pages Router | `__NEXT_DATA__` → `userProfile`, `story`, `curatedHighlights`, `spotlightHighlights` |
| `/place/<slug>/<id>` | Pages Router | `__NEXT_DATA__` → `placeProfile`, `storythumbnailData`, `componentThumbnailData` |
| `/explore/<query>` | App Router (RSC) | `self.__next_f` stream → `sections[]`, plus schema.org `VideoObject` JSON-LD |
| `/api/local/near-me` | JSON API | `?category=&lat=&lng=&locale=` → `places[]`, `stories_by_place_id` |

Pydantic models in `src/snapchat_cli/schemas.py` validate the raw page JSON. They map camelCase keys to snake_case,
unwrap Snapchat's `{"value": x}` wrappers and keep unknown fields. `src/snapchat_cli/extractors.py` turns the
validated data into `Snap` and `Collection` objects through pure `parse_*` functions, which are tested offline
against saved pages in `tests/fixtures/`. If snapchat.com changes its data, you get a clear "layout changed?" error
instead of a `KeyError`. To fix it, refresh the fixtures and adjust the schemas. Media and JSON files are written
with `aiofiles`.

The nearby endpoint sits behind a CDN cache that sometimes answers with another query's results. The client sends a
unique locale tag on each call (`en-US-x-<random>`, still answered in English), checks that the response matches the
request, and retries otherwise.

## Limitations

- Only public content that www.snapchat.com shows to a logged-out visitor is available. Private accounts and
  friends-only stories are not.
- The web app returns a fixed set of results per page (for example about 15 to 25 spotlight items per search), and
  it never loads more. Its client does not use the `*Cursor` fields in the page data.
- Story snaps expire after 24 hours, so their media URLs stop working after that.
- This is unofficial and may break whenever snapchat.com changes. Use it only for content you are allowed to
  download, and respect Snapchat's Terms of Service and the rights of the people who posted it.

## Development

The four scraper repos (snapchat-cli, tiktok-scraper, vinted-api, scraping-classement-atout-france) share the same
tooling: [uv](https://docs.astral.sh/uv/), [ruff](https://docs.astral.sh/ruff/), [pytest](https://docs.pytest.org/),
[pre-commit](https://pre-commit.com/) and the same GitHub Actions pipeline.

```bash
uv sync --all-extras                # project + dev tools (pytest, ruff, pre-commit)
uv run pre-commit install           # once: run the hooks on every commit

uv run pytest                       # offline tests
uv run pytest -m live               # smoke tests against the real website
uv run ruff check --fix . && uv run ruff format .
uv run pre-commit run --all-files   # every hook, as the CI does
```

Without uv: `pip install -e . --group dev` (`--group` needs pip 25.1+), then run the same commands without
the `uv run` prefix.

- **Pre-commit hooks**: they block `.env` files, `.har` captures and `token.json` files, scan for secrets with
  gitleaks, refuse files over 1 MB, check YAML and TOML files, fix trailing whitespace and final newlines, run
  `ruff check --fix` and `ruff format`, and keep `uv.lock` in sync with `pyproject.toml`.
- **CI** (`.github/workflows/ci.yml`): on every push to `main` and every pull request, one job runs all the
  pre-commit hooks, and another runs the offline tests on Python 3.11, 3.12, 3.13 and 3.14.
- **Code style**: ruff with 120-character lines, one import per line and absolute imports only (see
  `pyproject.toml`).

### Repository layout

```
.github/workflows/ci.yml   lint (pre-commit) + tests on Python 3.11 to 3.14
src/snapchat_cli/
├── cli.py          # command line (typer): user, search, place, nearby, get
├── client.py       # HTTP client: curl_cffi, retries, proxies
├── proxies.py      # proxy parsing, rotation and failover
├── targets.py      # URL / username / place id detection
├── parsing.py      # page data extraction (__NEXT_DATA__, RSC stream, JSON-LD)
├── schemas.py      # pydantic models of the raw page data
├── extractors.py   # raw data -> Snap / Collection
├── models.py       # Snap / Collection data structures
└── downloader.py   # media and manifest.json writing
tests/              # pytest suite, gzipped page fixtures, live smoke tests
```

## License

[MIT](LICENSE)
