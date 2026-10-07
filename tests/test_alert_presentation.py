from html.parser import HTMLParser

from revival_radar.clients.telegram import format_full_alert as format_alert
from revival_radar.models.signal import Acceleration, RevivalResult, Structure

from .conftest import changed


class TelegramHTML(HTMLParser):
    """Check that length limiting never cuts through formatting or link entities."""

    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        assert tag in {"b", "i", "code", "a", "blockquote"}
        if tag == "blockquote":
            assert attrs == [("expandable", None)]
        self.tags.append(tag)

    def handle_endtag(self, tag):
        assert self.tags.pop() == tag


def test_alert_has_scannable_hierarchy_and_human_score_reasons(token):
    result = RevivalResult(
        score=85,
        status="STRONG_REVIVAL",
        eligible=True,
        components={"volume_5m": 15, "base": 15, "higher_high": 5, "drawdown": 10},
        acceleration=Acceleration(
            volume_acceleration_5m=2.5,
            volume_ratio_5m=1.8,
            tx_acceleration_5m=2,
        ),
        structure=Structure(
            available=True,
            base_detected=True,
            base_duration_hours=97,
            higher_high_detected=True,
        ),
    )
    message = format_alert(token, result)
    assert f"${token.symbol} · 85/100" in message
    assert "Strong revival" in message
    assert "5m volume above baseline +15" in message
    assert "Base confirmed · 97h" in message
    assert "Higher high" in message
    assert "Breakout unconfirmed · Retest unconfirmed" in message
    assert "+150% vs prior" in message
    assert "Vs baseline +80%" in message
    assert "True" not in message and "False" not in message
    assert "volume_5m" not in message and "STRONG_REVIVAL" not in message
    assert f"<code>{token.contract_address}</code>" in message
    assert "No trades executed" in message
    details = message.split("<blockquote expandable>", 1)[1].split("</blockquote>", 1)[0]
    assert "Hot Search" in details
    assert "5m volume above baseline +15" in details
    assert "Breakout unconfirmed" in details
    first_view = message.replace(
        f"<blockquote expandable>{details}</blockquote>", "Signal details ▾"
    )
    assert "Hot Search" not in first_view
    assert "Breakout unconfirmed" not in first_view
    assert len(first_view.splitlines()) <= 27


def test_alert_missing_data_does_not_look_like_zero_or_confirmed_safety(token):
    token = changed(token, volume_5m=None, tx_5m=None, security={})
    result = RevivalResult(score=65, status="EARLY_WATCH", eligible=False)
    message = format_alert(token, result)
    assert "Volume 5m <b>unavailable</b>" in message
    assert "Top 10 holders unavailable" in message
    assert "Security assessment unavailable; absence is not safety" in message
    assert "Unavailable · insufficient candle history" in message
    assert "Base confirmed" not in message


def test_alert_preserves_risk_deductions_and_bounds_hostile_html(token):
    token = changed(token, symbol="<>&🦊" * 25, security={"dangerous": True})
    result = RevivalResult(
        score=60,
        status="<STRONG_REVIVAL>" * 100,
        eligible=False,
        components={
            "volume_5m": 15,
            "base": 15,
            "higher_high": 5,
            "concentration_penalty": -10,
            "danger_penalty": -40,
        },
        warnings=[f"Source {i} <>&🦊" * 1000 for i in range(30)],
        structure=Structure(available=True, base_detected=True, base_duration_hours=97),
    )
    message = format_alert(token, result)
    assert "Known dangerous-token flag (-40)" in message
    assert "High holder concentration (-10)" in message
    assert "additional data notes" in message
    assert "&lt;" in message and "&amp;" in message
    assert len(message.encode("utf-16-le")) // 2 < 4096
    parser = TelegramHTML()
    parser.feed(message)
    parser.close()
    assert parser.tags == []
    assert f"<code>{token.contract_address}</code>" in message
    assert "No trades executed" in message
    visible = message.split("<blockquote expandable>", 1)[0]
    assert "Known dangerous-token flag (-40)" in visible
    assert "High holder concentration (-10)" in visible


def test_alert_zero_volume_is_visible_not_missing(token):
    token = changed(token, volume_5m=0, tx_5m=0)
    message = format_alert(token, RevivalResult(score=0, status="IGNORE", eligible=False))
    assert "Volume 5m <b>$0.00</b>" in message
    assert "5m <b>0</b>" in message
