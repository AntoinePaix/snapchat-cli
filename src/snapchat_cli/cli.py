"""Command-line interface: ``snapchat user|search|place|get ...``."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass
from enum import Enum
from functools import partial
from pathlib import Path
from typing import Annotated
from typing import Any

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn
from rich.progress import MofNCompleteColumn
from rich.progress import Progress
from rich.progress import TextColumn
from rich.progress import TimeElapsedColumn
from rich.table import Table

from snapchat_cli import __version__
from snapchat_cli.client import NotFoundError
from snapchat_cli.client import SnapchatClient
from snapchat_cli.client import SnapchatError
from snapchat_cli.downloader import DownloadStats
from snapchat_cli.downloader import download_collection
from snapchat_cli.extractors import NEARBY_CATEGORIES
from snapchat_cli.extractors import fetch_nearby
from snapchat_cli.extractors import fetch_place
from snapchat_cli.extractors import fetch_search
from snapchat_cli.extractors import fetch_user
from snapchat_cli.extractors import grid_points
from snapchat_cli.extractors import search_usernames
from snapchat_cli.models import Collection
from snapchat_cli.proxies import ProxyPool
from snapchat_cli.proxies import Rotation
from snapchat_cli.proxies import load_proxy_file
from snapchat_cli.targets import Kind
from snapchat_cli.targets import Target
from snapchat_cli.targets import parse_coordinates
from snapchat_cli.targets import parse_target

console = Console(stderr=True)
app = typer.Typer(
    help="Download public snaps and metadata from snapchat.com profiles, search (explore) pages and places.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)


class UserSection(str, Enum):
    story = "story"
    highlights = "highlights"
    spotlight = "spotlight"


# Options shared by every download command.
OutputOpt = Annotated[
    Path,
    typer.Option("--output", "-o", help="Download root folder.", rich_help_panel="Output"),
]
ConcurrencyOpt = Annotated[
    int,
    typer.Option(
        "--concurrency",
        "-c",
        min=1,
        max=64,
        help="Parallel media downloads.",
        rich_help_panel="Network",
    ),
]
MetadataOnlyOpt = Annotated[
    bool,
    typer.Option(
        "--metadata-only",
        "-m",
        help="Write manifest.json only, skip media.",
        rich_help_panel="Output",
    ),
]
PreviewsOpt = Annotated[
    bool,
    typer.Option("--previews", help="Also save thumbnails.", rich_help_panel="Output"),
]
OverlaysOpt = Annotated[
    bool,
    typer.Option(
        "--overlays/--no-overlays",
        help="Save caption/sticker overlays.",
        rich_help_panel="Output",
    ),
]
RawOpt = Annotated[
    bool,
    typer.Option(
        "--raw",
        help="Also save the untouched page state as raw.json.",
        rich_help_panel="Output",
    ),
]
ProxyOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--proxy",
        "-x",
        envvar="SNAPCHAT_PROXY",
        help=(
            "Proxy to use; repeat for several. Formats: scheme://\\[user:pass@]host:port "
            "(http, https, socks4, socks5, socks5h), host:port or host:port:user:pass."
        ),
        rich_help_panel="Proxies",
    ),
]
ProxyFileOpt = Annotated[
    Path | None,
    typer.Option(
        "--proxy-file",
        envvar="SNAPCHAT_PROXY_FILE",
        exists=True,
        dir_okay=False,
        readable=True,
        help="File with one proxy per line (# comments allowed).",
        rich_help_panel="Proxies",
    ),
]
ProxyRotationOpt = Annotated[
    Rotation,
    typer.Option(
        "--proxy-rotation",
        help="How to pick a proxy for each request. Failing proxies are skipped either way.",
        rich_help_panel="Proxies",
    ),
]
ProxyMediaOpt = Annotated[
    bool,
    typer.Option(
        "--proxy-media/--no-proxy-media",
        help="Route media downloads through the proxies too (--no-proxy-media: pages only).",
        rich_help_panel="Proxies",
    ),
]
ImpersonateOpt = Annotated[
    str,
    typer.Option("--impersonate", help="curl_cffi browser fingerprint.", rich_help_panel="Network"),
]
LocaleOpt = Annotated[
    str,
    typer.Option("--locale", help="Accept-Language for page text.", rich_help_panel="Network"),
]
VerboseOpt = Annotated[int, typer.Option("--verbose", "-v", count=True, help="-v info, -vv debug.")]


@dataclass
class Options:
    output: Path
    concurrency: int
    metadata_only: bool
    previews: bool
    overlays: bool
    raw: bool
    proxy: list[str] | None
    proxy_file: Path | None
    proxy_rotation: Rotation
    proxy_media: bool
    impersonate: str
    locale: str

    def proxy_pool(self) -> ProxyPool:
        """Proxies from --proxy and --proxy-file, validated (raises typer.BadParameter)."""
        values = list(self.proxy or [])
        if self.proxy_file:
            values += load_proxy_file(self.proxy_file)
        try:
            return ProxyPool(values, rotation=self.proxy_rotation)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="--proxy/--proxy-file") from exc


def _setup_logging(verbose: int) -> None:
    level = logging.WARNING if verbose == 0 else logging.INFO if verbose == 1 else logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
        force=True,
    )


def _opts(params: dict[str, Any]) -> tuple[Options, ProxyPool]:
    """Build the shared options from a command's parameters (its ``locals()``)."""
    _setup_logging(params["verbose"])
    opts = Options(**{f.name: params[f.name] for f in dataclasses.fields(Options)})
    return opts, opts.proxy_pool()


def _targets(values: list[str], kind: Kind | None) -> list[Target]:
    try:
        return [parse_target(v, kind) for v in values]
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@dataclass
class NearbyParams:
    categories: list[str]
    grid: int = 1
    step_km: float = 1.0


async def _fetch(
    client: SnapchatClient,
    target: Target,
    sub_places: bool,
    nearby: NearbyParams | None = None,
) -> Collection:
    if target.kind == "user":
        return await fetch_user(client, target.key)
    if target.kind == "search":
        return await fetch_search(client, target.key)
    if target.kind == "nearby":
        if nearby is None:
            raise SnapchatError("Nearby target without nearby parameters")
        lat, lng = parse_coordinates(target.key)
        return await fetch_nearby(
            client,
            lat,
            lng,
            nearby.categories,
            grid=nearby.grid,
            step_km=nearby.step_km,
        )
    return await fetch_place(client, target.key, target.slug, include_sub_places=sub_places)


async def _run(
    targets: list[Target],
    opts: Options,
    pool: ProxyPool,
    *,
    sections: list[UserSection] | None = None,
    sub_places: bool = True,
    expand_profiles: bool = False,
    max_profiles: int | None = None,
    nearby: NearbyParams | None = None,
) -> int:
    table = Table(title="Summary", show_lines=False)
    for col in ("Target", "Folder", "Snaps", "New", "Skipped", "Failed"):
        table.add_column(
            col,
            justify="right" if col in ("Snaps", "New", "Skipped", "Failed") else "left",
        )
    totals = DownloadStats()
    errors = 0
    queue = list(targets)
    seen: set[tuple[str, str]] = set()

    async with SnapchatClient(
        impersonate=opts.impersonate,
        locale=opts.locale,
        proxies=pool,
        proxy_media=opts.proxy_media,
    ) as client:
        while queue:
            target = queue.pop(0)
            if (target.kind, target.key.lower()) in seen:
                continue
            seen.add((target.kind, target.key.lower()))
            label = f"{target.kind}:{target.key}"
            try:
                with console.status(f"Fetching {label}…"):
                    collection = await _fetch(client, target, sub_places, nearby)
            except NotFoundError:
                console.print(f"[red]✗[/] {label}: not found")
                errors += 1
                continue
            except SnapchatError as exc:
                console.print(f"[red]✗[/] {label}: {exc}")
                errors += 1
                continue

            if collection.kind == "user" and sections:
                wanted = tuple(s.value for s in sections)
                collection.groups = {
                    g: s for g, s in collection.groups.items() if g.split("/")[0] in wanted
                }

            with Progress(
                TextColumn("[bold]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task(label, total=collection.snap_count)
                out_dir, stats = await download_collection(
                    client,
                    collection,
                    opts.output,
                    concurrency=opts.concurrency,
                    media=not opts.metadata_only,
                    previews=opts.previews,
                    overlays=opts.overlays,
                    raw=opts.raw,
                    on_progress=partial(progress.advance, task),
                )
            totals.merge(stats)
            for err in stats.errors:
                console.print(f"[yellow]![/] {err}")
            console.print(
                f"[green]✓[/] {label}: {collection.snap_count} snaps "
                f"({stats.downloaded} new, {stats.skipped} skipped, {stats.failed} failed) → {out_dir}",
            )
            table.add_row(
                label,
                str(out_dir),
                str(collection.snap_count),
                str(stats.downloaded),
                str(stats.skipped),
                str(stats.failed),
            )

            if expand_profiles and collection.kind == "search":
                names = search_usernames(collection)[: max_profiles or None]
                queue.extend(Target("user", n) for n in names)

    if table.row_count > 1:
        console.print(table)
    if totals.failed:
        console.print(f"[yellow]{totals.failed} media download(s) failed[/]")
    return 1 if errors or totals.failed else 0


def _execute(coro) -> None:
    try:
        code = asyncio.run(coro)
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted[/]")
        code = 130
    raise typer.Exit(code)


@app.command()
def user(
    usernames: Annotated[
        list[str],
        typer.Argument(help="Usernames or profile URLs (https://www.snapchat.com/@name)."),
    ],
    only: Annotated[
        list[UserSection] | None,
        typer.Option("--only", help="Restrict to these sections (repeatable)."),
    ] = None,
    output: OutputOpt = Path("downloads"),
    concurrency: ConcurrencyOpt = 8,
    metadata_only: MetadataOnlyOpt = False,
    previews: PreviewsOpt = False,
    overlays: OverlaysOpt = True,
    raw: RawOpt = False,
    proxy: ProxyOpt = None,
    proxy_file: ProxyFileOpt = None,
    proxy_rotation: ProxyRotationOpt = Rotation.round_robin,
    proxy_media: ProxyMediaOpt = True,
    impersonate: ImpersonateOpt = "chrome",
    locale: LocaleOpt = "en-US",
    verbose: VerboseOpt = 0,
) -> None:
    """Download a profile: current story, highlights and spotlight snaps + profile metadata."""
    opts, pool = _opts(locals())
    _execute(_run(_targets(usernames, "user"), opts, pool, sections=only))


@app.command()
def search(
    queries: Annotated[
        list[str],
        typer.Argument(help="Search terms or explore URLs (https://www.snapchat.com/explore/q)."),
    ],
    profiles: Annotated[
        bool,
        typer.Option("--profiles", help="Also download every profile listed in the results."),
    ] = False,
    max_profiles: Annotated[
        int | None,
        typer.Option("--max-profiles", min=1, help="Cap for --profiles."),
    ] = None,
    output: OutputOpt = Path("downloads"),
    concurrency: ConcurrencyOpt = 8,
    metadata_only: MetadataOnlyOpt = False,
    previews: PreviewsOpt = False,
    overlays: OverlaysOpt = True,
    raw: RawOpt = False,
    proxy: ProxyOpt = None,
    proxy_file: ProxyFileOpt = None,
    proxy_rotation: ProxyRotationOpt = Rotation.round_robin,
    proxy_media: ProxyMediaOpt = True,
    impersonate: ImpersonateOpt = "chrome",
    locale: LocaleOpt = "en-US",
    verbose: VerboseOpt = 0,
) -> None:
    """Search (explore page): spotlight videos + profiles, lenses, places, shows metadata."""
    opts, pool = _opts(locals())
    _execute(
        _run(
            _targets(queries, "search"),
            opts,
            pool,
            expand_profiles=profiles,
            max_profiles=max_profiles,
        ),
    )


@app.command()
def place(
    places: Annotated[list[str], typer.Argument(help="Place URLs or place ids (UUID).")],
    sub_places: Annotated[
        bool,
        typer.Option(
            "--sub-places/--no-sub-places",
            help="Also download snaps of places inside this one.",
        ),
    ] = True,
    output: OutputOpt = Path("downloads"),
    concurrency: ConcurrencyOpt = 8,
    metadata_only: MetadataOnlyOpt = False,
    previews: PreviewsOpt = False,
    overlays: OverlaysOpt = True,
    raw: RawOpt = False,
    proxy: ProxyOpt = None,
    proxy_file: ProxyFileOpt = None,
    proxy_rotation: ProxyRotationOpt = Rotation.round_robin,
    proxy_media: ProxyMediaOpt = True,
    impersonate: ImpersonateOpt = "chrome",
    locale: LocaleOpt = "en-US",
    verbose: VerboseOpt = 0,
) -> None:
    """Download a place: place details, reviews, hours + snaps posted there (and in sub-places)."""
    opts, pool = _opts(locals())
    _execute(_run(_targets(places, "place"), opts, pool, sub_places=sub_places))


def _list_categories(value: bool) -> None:
    if value:
        typer.echo("\n".join(NEARBY_CATEGORIES))
        raise typer.Exit()


def _nearby_categories(values: list[str] | None, all_categories: bool) -> list[str]:
    if all_categories:
        return list(NEARBY_CATEGORIES)
    categories = [c.strip().lower() for v in values or [] for c in v.split(",") if c.strip()]
    if not categories:
        raise typer.BadParameter(
            "give at least one --category (or --all-categories; see --list-categories)",
            param_hint="--category",
        )
    unknown = [c for c in categories if c not in NEARBY_CATEGORIES]
    if unknown:
        # The list may grow on snapchat.com: warn, the API answers 404 if it really is unknown.
        console.print(f"[yellow]![/] Not a known category, trying anyway: {', '.join(unknown)}")
    return list(dict.fromkeys(categories))


@app.command(context_settings={"ignore_unknown_options": True})
def nearby(
    location: Annotated[
        str,
        typer.Argument(
            metavar="LAT,LNG",
            help="Center point, e.g. 48.8566,2.3522 (negative values work: -33.8688,151.2093).",
        ),
    ],
    category: Annotated[
        list[str] | None,
        typer.Option(
            "--category",
            "-k",
            help="Place category; repeat or comma-separate for several (one API call each).",
        ),
    ] = None,
    all_categories: Annotated[
        bool,
        typer.Option("--all-categories", help="Query every known category (45 calls per point)."),
    ] = False,
    grid: Annotated[
        int,
        typer.Option(
            "--grid",
            "-g",
            min=1,
            max=15,
            help="Query a GRIDxGRID square of points around the center (each call returns 20 places max).",
        ),
    ] = 1,
    step: Annotated[
        float,
        typer.Option("--step", min=0.05, max=50.0, help="Distance between grid points, in km."),
    ] = 1.0,
    list_categories: Annotated[
        bool,
        typer.Option(
            "--list-categories",
            callback=_list_categories,
            is_eager=True,
            help="Show the known categories and exit.",
        ),
    ] = False,
    output: OutputOpt = Path("downloads"),
    concurrency: ConcurrencyOpt = 8,
    metadata_only: MetadataOnlyOpt = False,
    previews: PreviewsOpt = False,
    overlays: OverlaysOpt = True,
    raw: RawOpt = False,
    proxy: ProxyOpt = None,
    proxy_file: ProxyFileOpt = None,
    proxy_rotation: ProxyRotationOpt = Rotation.round_robin,
    proxy_media: ProxyMediaOpt = True,
    impersonate: ImpersonateOpt = "chrome",
    locale: LocaleOpt = "en-US",
    verbose: VerboseOpt = 0,
) -> None:
    """Places around a GPS point (+ their recent snaps), by category, via snapchat.com/local."""
    try:
        lat, lng = parse_coordinates(location)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="LAT,LNG") from exc
    categories = _nearby_categories(category, all_categories)
    opts, pool = _opts(locals())
    calls = len(grid_points(lat, lng, grid, step)) * len(categories)
    console.print(
        f"Searching {len(categories)} category(ies) around {lat},{lng}"
        + (f" on a {grid}x{grid} grid ({step:g} km step)" if grid > 1 else "")
        + f": {calls} API call(s)",
    )
    params = NearbyParams(categories, grid=grid, step_km=step)
    _execute(_run([Target("nearby", f"{lat},{lng}")], opts, pool, nearby=params))


@app.command()
def get(
    urls: Annotated[
        list[str],
        typer.Argument(help="Any mix of snapchat.com profile, explore and place URLs."),
    ],
    output: OutputOpt = Path("downloads"),
    concurrency: ConcurrencyOpt = 8,
    metadata_only: MetadataOnlyOpt = False,
    previews: PreviewsOpt = False,
    overlays: OverlaysOpt = True,
    raw: RawOpt = False,
    proxy: ProxyOpt = None,
    proxy_file: ProxyFileOpt = None,
    proxy_rotation: ProxyRotationOpt = Rotation.round_robin,
    proxy_media: ProxyMediaOpt = True,
    impersonate: ImpersonateOpt = "chrome",
    locale: LocaleOpt = "en-US",
    verbose: VerboseOpt = 0,
) -> None:
    """Download from snapchat.com URLs, detecting the page type automatically."""
    opts, pool = _opts(locals())
    _execute(_run(_targets(urls, None), opts, pool))


def _version(value: bool) -> None:
    if value:
        typer.echo(f"snapchat-cli {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show version and exit."),
    ] = False,
) -> None:
    pass


def main() -> None:
    app()
