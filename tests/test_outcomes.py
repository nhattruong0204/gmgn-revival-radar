import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from revival_radar.clients.telegram import TelegramClient
from revival_radar.config_context import configuration_context
from revival_radar.models.signal import RevivalResult
from revival_radar.scanner import Scanner
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository

from .conftest import changed


def sent_alert(config, repo, token, status="sent"):
    config.dry_run = False
    signal = RevivalResult(
        score=90,
        status="CONFIRMED_REVIVAL",
        eligible=True,
        setup_score=80,
        trigger_score=100,
        confirmation_score=75,
    )
    identity = repo.reserve_alert(token, signal, config, configuration_context(config, 3))
    repo.finish_alert(identity, status, 123)
    return identity


def test_capture_alert_fields_atomically_and_preserve_original_snapshot(config, repo, token):
    identity = sent_alert(config, repo, token)
    saved = dict(repo.db.execute("SELECT * FROM alert_signals").fetchone())
    assert saved == dict(
        alert_id=identity,
        price=token.price,
        market_cap=token.market_cap,
        score=90,
        setup_score=80,
        trigger_score=100,
        confirmation_score=75,
        stage="CONFIRMED_REVIVAL",
        preset="Strict",
        revision=3,
    )
    repo.save_snapshot(changed(token, timestamp=token.timestamp + 400, price=10))
    assert dict(repo.db.execute("SELECT * FROM alert_signals").fetchone()) == saved
    assert (
        repo.reserve_alert(token, RevivalResult(score=90, status="REVIVING", eligible=True), config)
        is None
    )
    assert repo.db.execute("SELECT COUNT(*) FROM alert_signals").fetchone()[0] == 1


@pytest.mark.parametrize("hours", [1, 6, 24, 72])
def test_first_checkpoint_wins_across_restart(config, repo, token, hours):
    identity = sent_alert(config, repo, token)
    observed = changed(
        token, timestamp=token.timestamp + hours * 3600 + 150, price=token.price * 1.2
    )
    assert (
        repo.observe_outcomes(changed(observed, timestamp=token.timestamp + hours * 3600 - 1)) == 0
    )
    assert repo.observe_outcomes(observed) == 1
    assert (
        repo.observe_outcomes(changed(observed, timestamp=observed.timestamp + 150, price=1)) == 0
    )
    path = repo.db.execute("PRAGMA database_list").fetchone()["file"]
    other = connect(Path(path))
    try:
        reopened = Repository(other)
        assert reopened.observe_outcomes(observed) == 0
        row = other.execute("SELECT * FROM signal_outcomes").fetchone()
        assert row["alert_id"] == identity and row["horizon_hours"] == hours
        assert row["observed_at"] == observed.timestamp
        assert row["due_at"] == token.timestamp + hours * 3600
        assert row["price"] == observed.price and row["market_cap"] == observed.market_cap
        assert row["return_pct"] == pytest.approx(20)
    finally:
        other.close()


@pytest.mark.parametrize("initial", [None, 0])
def test_unknown_or_zero_start_price_never_creates_return(config, repo, token, initial):
    token = changed(token, price=initial)
    sent_alert(config, repo, token)
    repo.observe_outcomes(changed(token, timestamp=token.timestamp + 3600, price=1))
    assert repo.db.execute("SELECT return_pct FROM signal_outcomes").fetchone()[0] is None
    summary = repo.outcome_summary(0, token.timestamp + 4000)["1"]
    assert summary["observed"] == 1 and summary["returns_available"] == 0
    assert summary["median_return_pct"] is None


def test_market_cap_only_and_no_data_retry(config, repo, token):
    sent_alert(config, repo, token)
    observation = changed(token, timestamp=token.timestamp + 3600, price=None, market_cap=None)
    assert repo.observe_outcomes(observation) == 0
    assert repo.due_outcome_tokens(observation.timestamp, ["sol"], 10)
    assert repo.observe_outcomes(changed(observation, market_cap=500000)) == 1
    row = repo.db.execute("SELECT * FROM signal_outcomes").fetchone()
    assert row["price"] is None and row["market_cap"] == 500000 and row["return_pct"] is None
    assert not repo.due_outcome_tokens(observation.timestamp, ["sol"], 10)


@pytest.mark.parametrize("status", ["failed", "unknown"])
def test_nonconfirmed_deliveries_never_track(config, repo, token, status):
    sent_alert(config, repo, token, status)
    assert repo.observe_outcomes(changed(token, timestamp=token.timestamp + 3600)) == 0
    assert not repo.due_outcome_tokens(token.timestamp + 3600, ["sol"], 10)


def test_all_horizons_no_duplicates_no_interpolation_and_retention(config, repo, token):
    identity = sent_alert(config, repo, token)
    for hours in [1, 6, 24, 72]:
        assert (
            repo.observe_outcomes(
                changed(token, timestamp=token.timestamp + hours * 3600, price=token.price * 2)
            )
            == 1
        )
    assert repo.db.execute("SELECT COUNT(*) FROM signal_outcomes").fetchone()[0] == 4
    repo.prune_diagnostics(token.timestamp + 100 * 3600)
    assert repo.db.execute("SELECT COUNT(*) FROM signal_outcomes").fetchone()[0] == 4
    assert repo.presentation_detail("a", identity)
    summary = repo.health(token.timestamp + 71 * 3600, token.timestamp + 73 * 3600)["outcomes"][
        "72"
    ]
    assert summary["observed"] == 1 and summary["median_return_pct"] == 100


def test_late_snapshot_does_not_fill_earlier_checkpoints(config, repo, token):
    sent_alert(config, repo, token)
    assert repo.observe_outcomes(changed(token, timestamp=token.timestamp + 3 * 3600)) == 0
    summary = repo.outcome_summary(0, token.timestamp + 3 * 3600)
    assert summary["1"]["missed"] == 1 and summary["6"]["pending"] == 1


def test_chain_identity_and_budget_limit(config, repo, token):
    sent_alert(config, repo, token)
    now = token.timestamp + 3600
    assert not repo.due_outcome_tokens(now, ["base"], 10)
    assert not repo.due_outcome_tokens(now, ["sol"], 0)
    assert len(repo.due_outcome_tokens(now, ["sol"], 1)) == 1
    other = changed(
        token, contract_address="Dther111111111111111111111111111111111111111", timestamp=now
    )
    assert repo.observe_outcomes(other) == 0


class OutcomeSource:
    def __init__(self, token, fail=False):
        self.token, self.fail, self.calls = token, fail, 0

    async def discover(self, chain, source):
        return []

    async def enrich_market(self, token):
        self.calls += 1
        if self.fail:
            raise RuntimeError("secret-bearing error must not be logged")
        return changed(self.token, timestamp=token.timestamp, discovery_source=set())


@pytest.mark.parametrize("hours", [24, 72])
async def test_tracking_continues_without_discovery_after_watchlist_expires(
    config, repo, token, hours
):
    sent_alert(config, repo, token)
    config.dry_run = True
    source = OutcomeSource(changed(token, price=token.price * 1.3))
    now = token.timestamp + hours * 3600 + 300
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("No Telegram"))
    ) as http:
        with patch("time.time", return_value=now):
            report = await Scanner(config, source, repo, TelegramClient(config, http)).scan_once()
    assert source.calls == 1 and report.errors == 0
    assert report.funnel["sol"]["outcome_polled"] == 1
    assert report.sent == report.potential_alerts == 0
    assert repo.db.execute("SELECT return_pct FROM signal_outcomes").fetchone()[0] == pytest.approx(
        30
    )


async def test_budget_zero_defers_outcome_and_failure_is_retryable(config, repo, token, caplog):
    sent_alert(config, repo, token)
    config.dry_run = True
    config.max_market_enrich_per_scan = 0
    source = OutcomeSource(token, fail=True)
    now = token.timestamp + 3600
    async with httpx.AsyncClient() as http:
        with patch("time.time", return_value=now):
            first = await Scanner(config, source, repo, TelegramClient(config, http)).scan_once()
            config.max_market_enrich_per_scan = 1
            second = await Scanner(config, source, repo, TelegramClient(config, http)).scan_once()
    assert first.errors == 0 and second.errors == 1 and source.calls == 1
    assert "secret-bearing" not in caplog.text
    assert repo.due_outcome_tokens(now + 150, ["sol"], 10)
    assert not repo.db.execute("SELECT * FROM signal_outcomes").fetchall()


def test_v3_migration_backfills_only_saved_signal_and_leaves_bytes_unchanged(config, repo, token):
    identity = sent_alert(config, repo, token)
    saved_alert = tuple(repo.db.execute("SELECT * FROM alerts").fetchone())
    saved_state = repo.get_state(f"telegram:alert:{identity}")
    with repo.db:
        repo.db.execute("DROP TABLE signal_outcomes")
        repo.db.execute("DROP TABLE alert_signals")
        repo.db.execute("PRAGMA user_version=3")
        repo.db.execute(
            "INSERT INTO alerts(timestamp,chain,contract_address,score,reason) VALUES(?,?,?,?,?)",
            (0, *token.key, 91, "[]"),
        )
    other = connect(Path(repo.db.execute("PRAGMA database_list").fetchone()["file"]))
    try:
        assert other.execute("PRAGMA user_version").fetchone()[0] == 4
        assert (
            tuple(other.execute("SELECT * FROM alerts WHERE id=?", (identity,)).fetchone())
            == saved_alert
        )
        assert Repository(other).get_state(f"telegram:alert:{identity}") == saved_state
        signals = [
            dict(row) for row in other.execute("SELECT * FROM alert_signals ORDER BY alert_id")
        ]
        assert signals[0]["setup_score"] == 80 and signals[0]["price"] == token.price
        assert (
            signals[1]["price"] is None
            and signals[1]["stage"] is None
            and signals[1]["score"] == 91
        )
        assert "fixture-key" not in json.dumps(signals)
    finally:
        other.close()


async def test_normal_market_poll_reused_without_extra_checkpoint_request(config, repo, token):
    sent_alert(config, repo, token)
    config.dry_run = True
    source = OutcomeSource(
        changed(token, timestamp=token.timestamp + 3600, price=token.price * 1.1)
    )

    async def discover(chain, source_name):
        return [source.token]

    source.discover = discover
    async with httpx.AsyncClient() as http:
        with patch("time.time", return_value=source.token.timestamp):
            report = await Scanner(config, source, repo, TelegramClient(config, http)).scan_once()
    assert source.calls == 1 and report.errors == 0
    assert report.funnel["sol"]["outcome_polled"] == 0
    assert repo.db.execute("SELECT COUNT(*) FROM signal_outcomes").fetchone()[0] == 1


def test_same_evm_address_on_different_chains_stays_separate(config, repo, token):
    base = changed(token, chain="base", contract_address="0x" + "a" * 40)
    robinhood = changed(base, chain="robinhood")
    first = sent_alert(config, repo, base)
    second = sent_alert(config, repo, robinhood)
    assert first != second
    assert repo.observe_outcomes(changed(base, timestamp=token.timestamp + 3600)) == 1
    due = repo.due_outcome_tokens(token.timestamp + 3600, ["base", "robinhood"], 10)
    assert [entry.chain for entry in due] == ["robinhood"]


def test_duplicate_checkpoint_from_concurrent_connections(config, repo, token):
    from concurrent.futures import ThreadPoolExecutor

    sent_alert(config, repo, token)
    path = Path(repo.db.execute("PRAGMA database_list").fetchone()["file"])

    def observe(price):
        db = connect(path)
        try:
            return Repository(db).observe_outcomes(
                changed(token, timestamp=token.timestamp + 3600, price=price)
            )
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(observe, [1, 2]))
    assert sum(counts) == 1
    assert repo.db.execute("SELECT COUNT(*) FROM signal_outcomes").fetchone()[0] == 1


def test_failed_migration_rolls_back_and_can_retry(config, repo, token, monkeypatch):
    sent_alert(config, repo, token)
    path = Path(repo.db.execute("PRAGMA database_list").fetchone()["file"])
    with repo.db:
        repo.db.execute("DROP TABLE signal_outcomes")
        repo.db.execute("DROP TABLE alert_signals")
        repo.db.execute("PRAGMA user_version=3")

    def fail(db):
        db.execute("CREATE TABLE partial_migration (id INTEGER)")
        raise RuntimeError("interrupted migration")

    with monkeypatch.context() as patcher:
        patcher.setattr("revival_radar.storage.database._migrate_outcomes", fail)
        with pytest.raises(RuntimeError, match="interrupted"):
            connect(path)
    assert repo.db.execute("PRAGMA user_version").fetchone()[0] == 3
    assert not repo.db.execute(
        "SELECT name FROM sqlite_master WHERE name='partial_migration'"
    ).fetchone()
    migrated = connect(path)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 4
    migrated.close()


def test_corrupt_legacy_presentation_stays_unknown_without_breaking_migration(config, repo, token):
    identity = sent_alert(config, repo, token)
    repo.set_state(f"telegram:alert:{identity}", "{invalid JSON")
    path = Path(repo.db.execute("PRAGMA database_list").fetchone()["file"])
    with repo.db:
        repo.db.execute("DROP TABLE signal_outcomes")
        repo.db.execute("DROP TABLE alert_signals")
        repo.db.execute("PRAGMA user_version=3")
    migrated = connect(path)
    try:
        row = migrated.execute("SELECT * FROM alert_signals").fetchone()
        assert row["price"] is None and row["setup_score"] is None
        assert Repository(migrated).get_state(f"telegram:alert:{identity}") == "{invalid JSON"
    finally:
        migrated.close()


def test_nonfinite_calculated_return_is_unknown(config, repo, token):
    sent_alert(config, repo, changed(token, price=1e-300))
    assert repo.observe_outcomes(changed(token, timestamp=token.timestamp + 3600, price=1e300)) == 1
    assert repo.db.execute("SELECT return_pct FROM signal_outcomes").fetchone()[0] is None
