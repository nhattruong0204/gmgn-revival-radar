import sqlite3
import threading

from revival_radar.models.signal import RevivalResult
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository

from .conftest import changed


def result(score=77):
    return RevivalResult(score=score, status="REVIVING", eligible=True)


def test_snapshots_nullable_dedup_and_persistence(repo, token, history):
    for s in history:
        repo.save_snapshot(s)
    repo.save_snapshot(token)
    repo.save_snapshot(token)
    assert repo.db.execute("SELECT COUNT(*) FROM token_snapshots").fetchone()[0] == 5
    row = repo.db.execute("SELECT volume_1m FROM token_snapshots LIMIT 1").fetchone()
    assert row[0] is None
    assert len(repo.history(token)) == 4
    assert repo.db.execute("PRAGMA user_version").fetchone()[0] == 4
    assert len(repo.watchlist("sol", 0, 10)) == 1


def test_cooldown_realert_and_pending(repo, token, config):
    config.dry_run = False
    first = repo.reserve_alert(token, result(), config)
    assert first
    assert repo.reserve_alert(token, result(100), config) is None
    repo.finish_alert(first, "sent", 123)
    assert (
        repo.reserve_alert(changed(token, timestamp=token.timestamp + 300), result(86), config)
        is None
    )
    second = repo.reserve_alert(changed(token, timestamp=token.timestamp + 300), result(89), config)
    assert second
    repo.finish_alert(second, "sent", 124)
    assert (
        repo.reserve_alert(changed(token, timestamp=token.timestamp + 600), result(95), config)
        is None
    )
    assert repo.reserve_alert(
        changed(token, timestamp=token.timestamp + 7 * 3600), result(75), config
    )


def test_dry_run_failed_and_uncertain_delivery(repo, token, config):
    assert repo.reserve_alert(token, result(), config) is None
    config.dry_run = False
    first = repo.reserve_alert(token, result(), config)
    repo.finish_alert(first, "failed")
    second = repo.reserve_alert(token, result(), config)
    assert second
    repo.finish_alert(second, "unknown")
    assert repo.reserve_alert(token, result(100), config) is None


def test_cooldown_survives_reconnect(tmp_path, token, config):
    config.dry_run = False
    path = tmp_path / "restart.db"
    db = connect(path)
    repo = Repository(db)
    first = repo.reserve_alert(token, result(), config)
    repo.finish_alert(first, "sent", 42)
    db.close()
    db = connect(path)
    assert Repository(db).reserve_alert(token, result(), config) is None
    db.close()


def test_temporary_sqlite_lock(tmp_path, token):
    path = tmp_path / "locked.db"
    db = connect(path, 1000)
    blocker = sqlite3.connect(path, check_same_thread=False)
    blocker.execute("BEGIN IMMEDIATE")
    timer = threading.Timer(0.1, blocker.commit)
    timer.start()
    Repository(db).save_snapshot(token)
    timer.join()
    assert db.execute("SELECT count(*) FROM token_snapshots").fetchone()[0] == 1
    blocker.close()
    db.close()
