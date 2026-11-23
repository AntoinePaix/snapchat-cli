"""Download public snaps and metadata from the snapchat.com web application."""

from importlib.metadata import version

from snapchat_cli.client import NotFoundError
from snapchat_cli.client import SnapchatClient
from snapchat_cli.client import SnapchatError
from snapchat_cli.extractors import fetch_nearby
from snapchat_cli.extractors import fetch_place
from snapchat_cli.extractors import fetch_search
from snapchat_cli.extractors import fetch_user
from snapchat_cli.models import Collection
from snapchat_cli.models import Snap
from snapchat_cli.proxies import ProxyPool
from snapchat_cli.proxies import Rotation

__version__ = version("snapchat-cli")

__all__ = [
    "Collection",
    "NotFoundError",
    "ProxyPool",
    "Rotation",
    "Snap",
    "SnapchatClient",
    "SnapchatError",
    "__version__",
    "fetch_nearby",
    "fetch_place",
    "fetch_search",
    "fetch_user",
]
