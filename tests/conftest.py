import gzip
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return gzip.decompress((FIXTURES / f"{name}.html.gz").read_bytes()).decode("utf-8")


@pytest.fixture(scope="session")
def user_html() -> str:
    return load_fixture("user")


@pytest.fixture(scope="session")
def explore_html() -> str:
    return load_fixture("explore")


@pytest.fixture(scope="session")
def place_html() -> str:
    return load_fixture("place")
