import asyncio

import pytest
from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError

from snapchat_cli.client import SnapchatClient
from snapchat_cli.client import SnapchatError
from snapchat_cli.proxies import ProxyPool
from snapchat_cli.proxies import load_proxy_file
from snapchat_cli.proxies import mask_proxy
from snapchat_cli.proxies import normalize_proxy

A = "http://10.0.0.1:8080"
B = "http://10.0.0.2:8080"
C = "http://10.0.0.3:8080"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://1.2.3.4:8080", "http://1.2.3.4:8080"),
        ("socks5h://user:pw@proxy.example:1080", "socks5h://user:pw@proxy.example:1080"),
        ("1.2.3.4:3128", "http://1.2.3.4:3128"),
        ("1.2.3.4:3128:bob:p@ss", "http://bob:p%40ss@1.2.3.4:3128"),
        ("  1.2.3.4:3128 ", "http://1.2.3.4:3128"),
    ],
)
def test_normalize_proxy(value, expected):
    assert normalize_proxy(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "ftp://1.2.3.4:21", "http://1.2.3.4", "1.2.3.4", "a:b:c", "http://:8080"],
)
def test_normalize_proxy_invalid(value):
    with pytest.raises(ValueError):
        normalize_proxy(value)


def test_invalid_proxy_error_hides_password():
    with pytest.raises(ValueError) as info:
        normalize_proxy("host:1:user:secret:extra")
    assert "secret" not in str(info.value)


def test_mask_proxy():
    assert mask_proxy("http://bob:secret@1.2.3.4:8080") == "http://bob:***@1.2.3.4:8080"
    assert mask_proxy("http://1.2.3.4:8080") == "http://1.2.3.4:8080"
    assert mask_proxy(None) == "direct"


def test_load_proxy_file(tmp_path):
    path = tmp_path / "proxies.txt"
    path.write_text("# my proxies\n1.2.3.4:8080\n\nsocks5://5.6.7.8:1080  # eu\n")
    assert load_proxy_file(path) == ["1.2.3.4:8080", "socks5://5.6.7.8:1080"]


def test_round_robin_and_dedup():
    pool = ProxyPool([A, B, A, "10.0.0.3:8080"])
    assert pool.proxies == [A, B, C]
    assert [pool.next() for _ in range(4)] == [A, B, C, A]


def test_empty_pool_means_direct():
    pool = ProxyPool()
    assert not pool and pool.next() is None


def test_sticky_and_exclude():
    pool = ProxyPool([A, B], rotation="sticky")
    assert [pool.next() for _ in range(3)] == [A, A, A]
    assert pool.next(exclude=A) == B


def test_random_stays_in_pool():
    pool = ProxyPool([A, B, C], rotation="random")
    assert {pool.next() for _ in range(50)} <= {A, B, C}


def test_failing_proxy_is_benched_then_restored():
    pool = ProxyPool([A, B], rotation="sticky", max_failures=2, cooldown=60)
    pool.report_failure(A)
    assert pool.next() == A  # one failure: still in rotation
    pool.report_failure(A)
    assert pool.next() == B  # benched
    pool._benched_until[A] = 0  # cooldown over
    assert pool.next() == A


def test_all_benched_falls_back_to_all():
    pool = ProxyPool([A], max_failures=1)
    pool.report_failure(A)
    assert pool.next() == A


def test_success_resets_failures():
    pool = ProxyPool([A, B], rotation="sticky", max_failures=2)
    pool.report_failure(A)
    pool.report_success(A)
    pool.report_failure(A)
    assert pool.next() == A


class _Resp:
    def __init__(self, status: int, text: str = "ok"):
        self.status_code = status
        self.text = text


class FakeSession:
    """Records the proxy of each request; ``behaviour`` maps proxy -> status or exception."""

    def __init__(self, behaviour: dict):
        self.behaviour = behaviour
        self.proxies: list[str | None] = []

    async def get(self, url, proxy=None):
        self.proxies.append(proxy)
        outcome = self.behaviour.get(proxy, 200)
        if isinstance(outcome, Exception):
            raise outcome
        return _Resp(outcome)


@pytest.fixture
def no_sleep(monkeypatch):
    async def _sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", _sleep)


def _client(behaviour, **kwargs) -> tuple[SnapchatClient, FakeSession]:
    client = SnapchatClient(**kwargs)
    session = FakeSession(behaviour)
    client._session = session
    return client, session


def test_client_rotates_away_from_dead_proxy(no_sleep):
    client, session = _client(
        {A: CurlConnectionError("refused")},
        proxies=[A, B],
        proxy_rotation="sticky",
    )
    assert asyncio.run(client.get_page("https://x")) == "ok"
    assert session.proxies == [A, B]


def test_client_rotates_away_from_blocked_proxy(no_sleep):
    client, session = _client({A: 403}, proxies=[A, B], proxy_rotation="sticky")
    assert asyncio.run(client.get_page("https://x")) == "ok"
    assert session.proxies == [A, B]


def test_client_403_without_proxy_is_an_error(no_sleep):
    client, session = _client({None: 403})
    with pytest.raises(SnapchatError, match="HTTP 403"):
        asyncio.run(client.get_page("https://x"))
    assert session.proxies == [None]  # not retried: no other proxy to try


def test_client_single_proxy_argument():
    client, _ = _client({}, proxy="1.2.3.4:8080")
    assert client.proxies.proxies == ["http://1.2.3.4:8080"]
