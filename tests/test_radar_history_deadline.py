"""Radar latest-per-token reads: equivalence with the previous definition and deadline."""

import json
import time

import pytest

from revival_radar.config import Settings
from revival_radar.demo import DemoSource
from revival_radar.models.token import TokenSnapshot
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository
from revival_radar.web.config import WebConfig
from revival_radar.web.queries import PublicQueries, public_reader

from .conftest import changed
from .test_web_public import NOW, observe

HOUR = 3600
FILTER_DEFAULTS = dict(
    chain=None,
    status=None,
    search="",
    min_score=0,
    include_ignore=True,
    sort="score",
    order="desc",
    page=1,
    page_size=25,
)


def legacy_radar(queries: PublicQueries, until, *, age_hours, **f):
    """The pre-#98 definition: correlated latest-evaluation lookup per identity."""
    filters, arguments = ["e.score>=?"], [f["min_score"]]
    if f["status"]:
        filters.append("e.status=?")
        arguments.append(f["status"])
    elif not f["include_ignore"]:
        filters.append("e.status!='IGNORE'")
    if f["chain"]:
        filters.append("e.chain=?")
        arguments.append(f["chain"])
    if f["search"]:
        escaped = f["search"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(
            "(e.symbol LIKE ? ESCAPE '\\' OR "
            "public_field(st.value,e.chain,e.contract_address,e.timestamp,e.score,"
            "e.status,e.eligible,'name') LIKE ? ESCAPE '\\' OR instr(e.contract_address,?)>0)"
        )
        arguments.extend((f"%{escaped}%", f"%{escaped}%", f["search"]))
    cte = """WITH identities AS (
        SELECT DISTINCT chain,contract_address FROM evaluations
        WHERE timestamp>? AND timestamp<=?
    ), latest AS (
        SELECT e.* FROM identities i JOIN evaluations e ON e.id=(
            SELECT candidate.id FROM evaluations candidate
            JOIN scan_runs s ON s.id=candidate.scan_id
            WHERE candidate.chain=i.chain AND candidate.contract_address=i.contract_address
            AND candidate.timestamp>? AND candidate.timestamp<=?
            AND s.finished IS NOT NULL AND s.finished<=?
            ORDER BY candidate.timestamp DESC,candidate.id DESC LIMIT 1
        )
    ) """
    selection = (
        "FROM latest e LEFT JOIN state st ON st.key='telegram:evaluation:' || e.id WHERE "
        + " AND ".join(filters)
    )
    start = until - age_hours * 3600
    parameters = (start, until, start, until, until, *arguments)
    total = queries.db.execute(cte + "SELECT COUNT(*) " + selection, parameters).fetchone()[0]
    sort = f["sort"]
    if sort in ("score", "timestamp"):
        sorting = f"e.{sort}"
    else:
        sorting = (
            "public_field(st.value,e.chain,e.contract_address,e.timestamp,e.score,"
            f"e.status,e.eligible,'{sort}')"
        )
    rows = queries.db.execute(
        cte
        + "SELECT e.* "
        + selection
        + f" ORDER BY ({sorting}) IS NULL ASC,({sorting}) {f['order'].upper()},"
        "e.timestamp DESC,e.chain,e.contract_address LIMIT ? OFFSET ?",
        (*parameters, f["page_size"], (f["page"] - 1) * f["page_size"]),
    ).fetchall()
    return total, [queries._evaluation(row).model_dump(mode="json") for row in rows]


def digest(page):
    return page.total, [item.model_dump(mode="json") for item in page.items]


COMBINATIONS = [
    {},
    dict(min_score=40, include_ignore=False),
    dict(status="WATCH"),
    dict(status="IGNORE"),
    dict(chain="sol", sort="timestamp"),
    dict(chain="eth"),
    dict(sort="market_cap", order="asc"),
    dict(sort="liquidity"),
    dict(sort="volume_5m", order="asc", page_size=3),
    dict(search="alpha"),
    dict(search="ALPHA", sort="market_cap"),
    dict(search="100%_"),
    dict(page=2, page_size=4),
    dict(page=99, page_size=4),
    dict(min_score=101 - 1, sort="timestamp", order="asc"),
]


def populate(repo, token, config):
    """Multiple evaluations per token, an unfinished scan, future rows and odd payloads."""
    for index in range(14):
        address = "Fixture" + "abcdefghijkmnpqrstuvwxyz"[index] * 36
        for step, offset in enumerate((200 * HOUR, 90 * HOUR, 30 * HOUR, 2 * HOUR)):
            if index % 5 == 0 and step == 3:
                continue  # some tokens only have older evidence inside wide windows
            observe(
                repo,
                changed(
                    token,
                    contract_address=address,
                    symbol=f"S{index}",
                    name="Alpha Token" if index % 3 == 0 else f"Name {index}",
                    market_cap=1000.0 * (index + 1) * (step + 1),
                    liquidity=50.0 * ((index * 7) % 11),
                ),
                config,
                timestamp=NOW - offset - index,
                score=(index * 13 + step * 7) % 90,
                status="WATCH" if index % 2 else "IGNORE",
            )
        # newer unfinished and future evaluations must never become "latest"
        observe(
            repo,
            changed(token, contract_address=address, symbol=f"S{index}"),
            config,
            timestamp=NOW - 10,
            score=99,
            finished=False,
        )
        observe(
            repo,
            changed(token, contract_address=address, symbol=f"S{index}"),
            config,
            timestamp=NOW + 50,
            score=98,
        )
    # same-timestamp tie: higher evaluation id wins
    for score in (10, 20):
        observe(
            repo,
            changed(token, contract_address="Tie" + "y" * 40, symbol="TIE"),
            config,
            timestamp=NOW - 5 * HOUR,
            score=score,
        )
    # damaged and missing presentations still list, with unknown sort values
    repo.db.execute(
        "UPDATE state SET value='{broken' WHERE key=("
        "SELECT 'telegram:evaluation:' || MIN(id) FROM evaluations "
        "WHERE timestamp>?)",
        (NOW - 3 * HOUR,),
    )
    repo.db.execute(
        "DELETE FROM state WHERE key=("
        "SELECT 'telegram:evaluation:' || MAX(id) FROM evaluations "
        "WHERE timestamp<=? AND timestamp>?)",
        (NOW - 20 * HOUR, NOW - 40 * HOUR),
    )
    # operator-quarantined evaluation and scan are hidden before choosing the latest
    ids = [
        r[0]
        for r in repo.db.execute(
            "SELECT id FROM evaluations WHERE timestamp>? ORDER BY id LIMIT 3", (NOW - 3 * HOUR,)
        )
    ]
    repo.record_origin("evaluation", ids[1], "synthetic")
    scan_id = repo.db.execute("SELECT scan_id FROM evaluations WHERE id=?", (ids[2],)).fetchone()[0]
    repo.record_origin("scan", scan_id, "test")
    repo.db.commit()


@pytest.fixture(scope="module")
def populated(tmp_path_factory):
    path = tmp_path_factory.mktemp("radar") / "fixture.db"
    repo = Repository(connect(path))
    settings = Settings(
        _env_file=None,
        enabled_chains="sol",
        gmgn_api_key="fixture-key",
        request_spacing_seconds=0.00001,
    )
    populate(repo, TokenSnapshot(**DemoSource().data[0]["token"]), settings)
    repo.db.close()
    return path


@pytest.mark.parametrize("age_hours", [1, 4, 24, 72, 168])
@pytest.mark.parametrize("filters", COMBINATIONS)
def test_radar_matches_previous_definition(populated, age_hours, filters):
    web = WebConfig(database_path=populated)
    arguments = {**FILTER_DEFAULTS, **filters}
    with public_reader(web) as queries:
        new = digest(queries.radar(NOW, age_hours=age_hours, **arguments))
        old = legacy_radar(queries, NOW, age_hours=age_hours, **arguments)
    assert new == old
    if filters == {} and age_hours == 168:
        assert new[0] > 5


def test_populated_fixture_exercises_ties_and_hidden_rows(populated):
    with public_reader(WebConfig(database_path=populated)) as queries:
        page = queries.radar(NOW, age_hours=24, **FILTER_DEFAULTS)
        tie = [i for i in page.items if i.symbol == "TIE"]
        assert [i.score for i in tie] == [20]
        assert all(i.score != 99 and i.score != 98 for i in page.items)
        # Page past the end still reports the real total.
        far = queries.radar(NOW, age_hours=24, **{**FILTER_DEFAULTS, "page": 50})
        assert far.items == [] and far.total == page.total > 0


def bulk_history(path, *, tokens, per_token, window_hours):
    """Production-shaped history: ~100K evaluations with presentations in state."""
    db = connect(path)
    try:
        scans = 600
        step = window_hours * HOUR / scans
        db.executemany(
            "INSERT INTO scan_runs(id,started,finished) VALUES(?,?,?)",
            [
                (
                    i + 1,
                    NOW - window_hours * HOUR + i * step,
                    NOW - window_hours * HOUR + i * step + 5,
                )
                for i in range(scans)
            ],
        )
        rows, states = [], []
        evaluation = 0
        payload = json.dumps({"token": {}, "signal": {}})
        for n in range(per_token):
            for t in range(tokens):
                evaluation += 1
                scan = (n * 7 + t) % scans + 1
                stamp = NOW - window_hours * HOUR + (scan - 1) * step + 1
                rows.append(
                    (
                        evaluation,
                        scan,
                        stamp,
                        "sol" if t % 4 else "eth",
                        f"Synthetic{t:05d}" + "z" * 30,
                        f"T{t}",
                        (t * 31 + n) % 80,
                        "IGNORE" if (t + n) % 9 else "WATCH",
                        0,
                        "[]",
                        "[]",
                        "[]",
                        "token",
                        "[]",
                    )
                )
                states.append((f"telegram:evaluation:{evaluation}", payload, stamp))
        db.executemany(
            "INSERT OR IGNORE INTO evaluations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
        )
        db.executemany("INSERT INTO state(key,value,updated_at) VALUES(?,?,?)", states)
        db.commit()
        return len(rows)
    finally:
        db.close()


@pytest.mark.parametrize("age_hours", [24, 72, 168])
def test_large_history_stays_within_read_deadline_for_every_window(tmp_path, age_hours):
    path = tmp_path / "large.db"
    count = bulk_history(path, tokens=2800, per_token=36, window_hours=168)
    assert count > 100_000
    config = WebConfig(database_path=path)  # the unchanged 1.5 s deadline
    assert config.web_read_timeout_ms == 1500
    combos = [
        {},
        dict(min_score=40, include_ignore=False),
        dict(status="WATCH", sort="timestamp"),
        dict(chain="eth"),
        dict(page=40),
        dict(page=999),
    ]
    for extra in combos:
        arguments = {**FILTER_DEFAULTS, **extra}
        started = time.monotonic()
        with public_reader(config) as queries:
            page = queries.radar(NOW + 1, age_hours=age_hours, **arguments)
        elapsed = time.monotonic() - started
        assert elapsed < 1.5, (age_hours, extra, elapsed)
        assert page.total >= 0
    # Window membership is real: wider windows contain at least as many tokens.
    with public_reader(config) as queries:
        totals = [
            queries.radar(NOW + 1, age_hours=hours, **FILTER_DEFAULTS).total
            for hours in (1, 24, 168)
        ]
    assert totals == sorted(totals) and totals[-1] > 2000
