import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Every test gets an empty cache and no live API access."""
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CACHE_MODE", "default")

    def no_network(*a, **k):
        raise AssertionError("unexpected live API call")

    monkeypatch.setattr("server.exa._post", no_network)
    monkeypatch.setattr("server.apollo._get", no_network)
    return tmp_path / "cache"


@pytest.fixture
def stub(monkeypatch):
    """stub(module, fn) replaces the HTTP call; returns a list recording calls."""
    def install(target: str, fn):
        calls = []
        def wrapped(*args):
            calls.append(args)
            return fn(*args)
        monkeypatch.setattr(target, wrapped)
        return calls
    return install
