import json
from pathlib import Path

import pytest

from revival_radar.config import Settings
from revival_radar.demo import DemoSource
from revival_radar.models.token import Candle, TokenSnapshot
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository


@pytest.fixture
def config():
    return Settings(
        _env_file=None,
        enabled_chains="sol",
        gmgn_api_key="fixture-key",
        request_spacing_seconds=0.00001,
    )


@pytest.fixture
def scenario():
    return DemoSource().data[0]


@pytest.fixture
def token(scenario):
    return TokenSnapshot(**scenario["token"])


@pytest.fixture
def history(scenario):
    return [TokenSnapshot(**s) for s in scenario["history"]]


@pytest.fixture
def candles(scenario):
    return [Candle(**c) for c in scenario["candles"]]


@pytest.fixture
def repo(tmp_path):
    db = connect(tmp_path / "test.db")
    yield Repository(db)
    db.close()


@pytest.fixture
def responses():
    return json.loads((Path(__file__).parent / "fixtures/gmgn.json").read_text())


def changed(token, **kwargs):
    return TokenSnapshot(**(token.model_dump() | kwargs))
