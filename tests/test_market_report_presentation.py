"""Historical report messages stay truthful, escaped, bounded and inspectable."""

from copy import deepcopy
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser

import pytest

from revival_radar.market_report import (
    market_evidence_pages,
    market_evidence_summary,
    market_report_buttons,
    market_report_page,
)

NOW = datetime(2026, 10, 10, 13, 0, tzinfo=UTC).timestamp()


def observation(identity=11, *, score=65, timestamp=NOW - 60):
    return {
        "id": identity,
        "timestamp": timestamp,
        "score": score,
        "status": "EARLY_REVIVAL",
        "eligible": True,
        "rejection_reasons": [],
        "missing_fields": [],
        "warnings": [],
        "token": {
            "market_cap": 4_560_000,
            "liquidity": 564_400,
            "security": {"dangerous": False, "flags": []},
            "data_warnings": [],
        },
        "signal": {"setup_score": 70, "trigger_score": 60, "confirmation_score": 25},
        "configuration": {"settings": {"history_max_gap_seconds": 900}},
    }


def entry(index=0):
    return {
        "chain": "sol",
        "contract_address": "1" * 32 + "ABCDEFGHJK"[index],
        "symbol": f"TOKEN{index}",
        "observations": 12,
        "peak": observation(11 + index * 2, score=80, timestamp=NOW - 3600),
        "latest": observation(12 + index * 2),
    }


@pytest.fixture
def report():
    return {
        "since": NOW - 4 * 3600,
        "until": NOW,
        "timezone": "Asia/Bangkok",
        "hours": 4,
        "chain": "sol",
        "evaluations": 480,
        "unique_tokens": 40,
        "completed_scans": 48,
        "incomplete_scans": 0,
        "last_finished": NOW - 60,
        "first_observation": NOW - 4 * 3600 + 30,
        "multiple_configurations": False,
        "entries": [entry()],
    }


class TelegramHTML(HTMLParser):
    """The report owns all markup; hostile token text must never create tags."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack = []

    def handle_starttag(self, tag, attrs):
        assert tag == "b"
        assert not attrs
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag

    def handle_entityref(self, name):
        assert name in {"amp", "lt", "gt", "quot"}

    def handle_charref(self, name):
        assert name == "x27"


def assert_valid_message(message):
    assert len(message) <= 4096
    assert len(message.encode("utf-16-le")) // 2 <= 4096
    parser = TelegramHTML()
    parser.feed(message)
    parser.close()
    assert not parser.stack


def test_report_peak_and_latest_are_distinct_and_timezone_explicit(report):
    text = market_report_page(report)
    assert "Solana top 1 · 4h" in text
    assert "10 Oct 16:00 → 10 Oct 20:00" in text
    assert "Asia/Bangkok (UTC+07:00)" in text
    assert "80 → 65" in text
    assert "Peak 10/10 19:00 · Latest 1m ago · 12 obs" in text
    assert "S/T/C 70/60/25" in text
    assert "MC $4.56M · Liq $564.4K" in text
    assert "Below-threshold rows included" in text
    assert "Narrative/catalysts unassessed" in text
    assert "not return probabilities" in text
    assert "not the whole market or live quotes" in text
    assert_valid_message(text)


def test_rank_order_is_preserved_without_alert_threshold_filtering(report):
    low, high = entry(), entry(1)
    low["peak"]["score"] = 5
    low["latest"]["score"] = 0
    low["latest"]["eligible"] = False
    low["latest"]["rejection_reasons"] = ["score_below_threshold"]
    high["peak"]["score"] = 95
    report["entries"] = [low, high]
    text = market_report_page(report)
    assert text.index("1. $TOKEN0") < text.index("2. $TOKEN1")
    assert "5 → 0" in text
    assert "Latest block: score below threshold" in text
    assert len(market_report_buttons(report)) == 2


def test_latest_missing_data_is_not_replaced_by_peak_payload(report):
    latest = report["entries"][0]["latest"]
    latest.update(token=None, signal=None, score=None, timestamp=None)
    text = market_report_page(report)
    assert "80 → ?" in text
    assert "Latest age unknown" in text
    assert "S/T/C ?/?/? · MC ? · Liq ?" in text
    assert "Saved details unavailable (legacy)" in text
    assert "$4.56M" not in text
    buttons = market_report_buttons(report)[0]
    assert [item.get("callback_data") for item in buttons if "callback_data" in item] == [
        "e:11:report",
        "e:12:report",
    ]


@pytest.mark.parametrize(
    ("security", "expected"),
    [
        ({"dangerous": True}, "dangerous-token flag"),
        ({"mint_renounced": False}, "mint authority active"),
        ({"freeze_renounced": False}, "freeze authority active"),
        ({"flags": ["<danger>&flag"]}, "&lt;danger&gt;&amp;flag"),
    ],
)
def test_known_current_risk_visible_even_without_rejection_reason(report, security, expected):
    report["entries"][0]["latest"]["token"]["security"] = security
    text = market_report_page(report)
    assert "⚠ KNOWN RISK latest" in text
    assert expected in text
    assert_valid_message(text)


def test_peak_risk_does_not_claim_latest_is_dangerous(report):
    report["entries"][0]["peak"]["token"]["security"]["dangerous"] = True
    text = market_report_page(report)
    assert "KNOWN RISK at peak" in text
    assert "KNOWN RISK latest" not in text


def test_legacy_danger_blocker_remains_visible_without_token_payload(report):
    latest = report["entries"][0]["latest"]
    latest.update(token=None, signal=None, rejection_reasons=["security_dangerous"])
    text = market_report_page(report)
    assert "KNOWN RISK latest" in text
    assert "Saved details unavailable (legacy)" in text


def test_unknown_security_is_not_reported_as_safe(report):
    report["entries"][0]["latest"]["token"]["security"] = {}
    text = market_report_page(report)
    assert "Risk unknown / checks incomplete" in text
    assert "safe" not in text.lower()


def test_blockers_missing_data_and_warning_are_visible(report):
    latest = report["entries"][0]["latest"]
    latest["rejection_reasons"] = ["no_base", "score_below_threshold"]
    latest["missing_fields"] = ["baseline_history", "security.insider_ratio"]
    latest["warnings"] = ["Insider holdings unavailable"]
    text = market_report_page(report)
    assert "Blocked peak/latest 0/2 · Missing peak/latest 0/2" in text
    assert "Latest block: no base" in text
    assert "Latest missing: baseline history" in text
    assert "Note: Insider holdings unavailable" in text


def test_stale_latest_uses_observation_configuration_and_frozen_until(report):
    latest = report["entries"][0]["latest"]
    latest["timestamp"] = NOW - 700
    latest["configuration"]["settings"]["history_max_gap_seconds"] = 600
    assert "Latest STALE 11m ago" in market_report_page(report)
    report["until"] = NOW - 200
    assert "Latest 8m ago" in market_report_page(report)


def test_legacy_freshness_and_future_timestamp_are_explicit(report):
    latest = report["entries"][0]["latest"]
    latest["configuration"] = {}
    latest["timestamp"] = NOW - 7200
    assert "Latest STALE 2.0h ago" in market_report_page(report)
    latest["timestamp"] = NOW + 60
    assert "Latest future timestamp" in market_report_page(report)


def test_sparse_partial_incomplete_and_mixed_configuration_notices(report):
    report.update(
        incomplete_scans=2,
        first_observation=report["since"] + 1800,
        multiple_configurations=True,
    )
    report["entries"][0]["observations"] = 1
    text = market_report_page(report)
    assert "2 incomplete scans at cutoff; coverage may have gaps" in text
    assert "Partial window: first saved observation 10/10 16:30" in text
    assert "Sparse samples" in text
    assert "Multiple configurations in window; scores may not be comparable" in text
    assert_valid_message(text)


def test_empty_window_is_not_filled_with_tickers_or_claimed_market_coverage(report):
    report.update(entries=[], evaluations=0, unique_tokens=0, completed_scans=0)
    text = market_report_page(report)
    assert "No saved Solana evaluations in this window" in text
    assert "No completed scans in this window" in text
    assert "0 evaluations · 0 tracked tickers" in text
    assert market_report_buttons(report) == []
    assert_valid_message(text)


def test_ten_adversarial_rows_preserve_all_known_risks_and_valid_bounded_html(report):
    report.update(
        entries=[entry(index) for index in range(10)],
        incomplete_scans=10**12,
        completed_scans=0,
        first_observation=report["since"] + 1800,
        multiple_configurations=True,
    )
    hostile = '<script attr="">&&💥' * 1000
    for item in report["entries"]:
        item["symbol"] = hostile
        item["observations"] = 1
        item["latest"]["status"] = hostile
        item["latest"]["token"]["security"] = {"dangerous": True, "flags": [hostile]}
        item["peak"]["token"]["security"] = {"dangerous": True}
        item["latest"]["rejection_reasons"] = [hostile] * 50
        item["latest"]["missing_fields"] = [hostile] * 50
        item["latest"]["warnings"] = [hostile]
    text = market_report_page(report)
    assert text.count("KNOWN RISK latest") == 10
    assert text.count("Blocked peak/latest 0/50 · Missing peak/latest 0/50") == 10
    assert "<script" not in text
    for rank in range(1, 11):
        assert f"<b>{rank}. $" in text
    assert_valid_message(text)


def test_only_ten_distinct_solana_entries_and_no_input_mutation(report):
    rows = [entry(0), entry(0), entry(1) | {"chain": "bsc"}]
    rows += [entry(index) for index in range(1, 10)]
    rows.append(entry(0) | {"contract_address": "2" * 32, "symbol": "EXCLUDED11"})
    report["entries"] = rows
    original = deepcopy(report)
    text = market_report_page(report)
    assert "Solana top 10" in text
    assert "EXCLUDED11" not in text
    assert len(market_report_buttons(report)) == 10
    assert report == original
    assert_valid_message(text)


def test_durable_saved_detail_buttons_and_gmgn_links_match_each_rank(report):
    report["entries"] = [entry(0), entry(1)]
    rows = market_report_buttons(report)
    assert rows[0] == [
        {"text": "1. Peak evidence", "callback_data": "e:11:report"},
        {"text": "1. Latest evidence", "callback_data": "e:12:report"},
        {"text": "1. GMGN", "url": "https://gmgn.ai/sol/token/" + "1" * 32 + "A"},
    ]
    assert rows[1][0]["callback_data"] == "e:13:report"
    assert all(
        len(button.get("callback_data", "").encode()) <= 64 for row in rows for button in row
    )


@pytest.mark.parametrize("identity", [0, -1, True, "11", "11:full", 10**18, 2**63])
def test_invalid_or_unsupported_saved_ids_do_not_create_detail_callbacks(report, identity):
    for key in ("peak", "latest"):
        report["entries"][0][key]["id"] = identity
    assert market_report_buttons(report)[0] == [
        {"text": "1. GMGN", "url": "https://gmgn.ai/sol/token/" + "1" * 32 + "A"}
    ]


def test_same_peak_and_latest_evaluation_creates_one_detail_button(report):
    report["entries"][0]["latest"] = deepcopy(report["entries"][0]["peak"])
    assert len(market_report_buttons(report)[0]) == 2


def test_invalid_timezone_and_numeric_values_are_unknown_without_breaking_message(report):
    report["timezone"] = '<script>"&'
    latest = report["entries"][0]["latest"]
    latest["score"] = float("nan")
    latest["token"]["market_cap"] = float("inf")
    latest["signal"]["setup_score"] = None
    text = market_report_page(report)
    assert "UTC (report timezone unavailable)" in text
    assert "80 → ?" in text
    assert "S/T/C ?/60/25 · MC ?" in text
    assert_valid_message(text)


def test_peak_blockers_and_missing_evidence_remain_visible_when_latest_clean(report):
    peak = report["entries"][0]["peak"]
    peak.update(
        eligible=False,
        rejection_reasons=["liquidity_below_minimum", "score_below_threshold"],
        missing_fields=["baseline_history"],
    )
    text = market_report_page(report)
    assert "Blocked peak/latest 2/0 · Missing peak/latest 1/0" in text
    assert "Peak block: liquidity below minimum" in text
    assert "Peak missing: baseline history" in text
    assert_valid_message(text)


def test_legacy_peak_notice_and_evidence_button_survive_valid_latest(report):
    report["entries"][0]["peak"].update(token=None, signal=None)
    text = market_report_page(report)
    assert "Peak details unavailable (legacy)" in text
    assert "80 → 65" in text
    assert market_report_buttons(report)[0][0]["callback_data"] == "e:11:report"
    assert_valid_message(text)


def test_different_peak_and_latest_risks_do_not_imply_the_same_finding(report):
    report["entries"][0]["peak"]["token"]["security"] = {"freeze_renounced": False}
    report["entries"][0]["latest"]["token"]["security"] = {"mint_renounced": False}
    text = market_report_page(report)
    assert "KNOWN RISK latest: mint authority active (risk also at peak)" in text


def test_delayed_report_measures_latest_age_at_creation_not_window_cutoff(report):
    report["generated_at"] = NOW + 2 * 3600
    text = market_report_page(report)
    assert "Latest STALE 2.0h ago" in text
    assert "age at report creation" in text
    assert "10 Oct 16:00 → 10 Oct 20:00" in text


def test_ten_legacy_peaks_preserve_counts_known_risks_and_message_limit(report):
    report.update(
        entries=[entry(index) for index in range(10)],
        evaluations=10**12,
        unique_tokens=10**12,
        completed_scans=0,
        incomplete_scans=10**12,
        first_observation=report["since"] + 1800,
        multiple_configurations=True,
    )
    for item in report["entries"]:
        item["symbol"] = "<&💥" * 100
        item["observations"] = 10**12
        item["peak"].update(token=None, signal=None, rejection_reasons=["security_dangerous"])
        item["latest"]["token"]["security"] = {"dangerous": True, "flags": ["x" * 100]}
        item["latest"]["timestamp"] = 0
        item["latest"]["score"] = 100
        item["latest"]["signal"] = {
            "setup_score": 100,
            "trigger_score": 100,
            "confirmation_score": 100,
        }
        item["latest"]["rejection_reasons"] = ["x" * 100] * 50
        item["latest"]["missing_fields"] = ["y" * 100] * 50
    text = market_report_page(report)
    assert text.count("KNOWN RISK latest") == 10
    assert text.count("Peak details unavailable") == 10
    assert text.count("Blocked peak/latest 1/50 · Missing peak/latest 0/50") == 10
    assert_valid_message(text)


def saved_detail(**evaluation):
    obs = observation()
    return {
        "token": obs["token"] | {"symbol": "TOKEN<&"},
        "signal": obs["signal"],
        "evaluation": {
            "id": obs["id"],
            "timestamp": obs["timestamp"],
            "eligible": False,
            "rejection_reasons": [],
            "missing_fields": [],
            "warnings": [],
            **evaluation,
        },
    }


def test_evidence_pages_keep_every_blocker_and_missing_field_escaped_and_ordered():
    blockers = [f"blocker_{index} <tag>&💥" for index in range(100)]
    missing = [f"missing_{index} <tag>&💥" for index in range(100)]
    detail = saved_detail(rejection_reasons=blockers, missing_fields=missing)
    original = deepcopy(detail)
    pages = market_evidence_pages(detail)
    assert len(pages) > 1
    text = "\n".join(pages)
    for index, reason in enumerate(blockers, 1):
        assert f"Blocker {index}/100: {reason}" in unescape(text)
    for index, field in enumerate(missing, 1):
        assert f"Missing field {index}/100: {field}" in unescape(text)
    assert "Eligible at observation: no" in text
    assert "<tag>" not in text and "TOKEN&lt;&amp;" in text
    for page in pages:
        assert_valid_message(page)
    assert detail == original


def test_one_long_evidence_reason_is_split_without_losing_text_or_entities():
    reason = "BEGIN-" + '<danger attr="💥">&' * 1000 + "-END"
    pages = market_evidence_pages(saved_detail(rejection_reasons=[reason]))
    segments = [
        unescape(line.split(": ", 1)[1])
        for page in pages
        for line in page.splitlines()
        if line.startswith("• Blocker 1/1")
    ]
    assert "".join(segments) == reason
    assert len(pages) > 3
    for page in pages:
        assert_valid_message(page)


def test_legacy_evidence_uses_metadata_before_token_validation_and_keeps_danger():
    detail = saved_detail(
        rejection_reasons=["security_dangerous"], missing_fields=["security.dangerous"]
    )
    detail.update(token=None, signal=None)
    pages = market_evidence_pages(detail)
    assert "KNOWN RISK: dangerous-token flag" in pages[0]
    assert "Saved token/signal details unavailable (legacy)" in pages[0]
    assert "Blocker 1/1: security_dangerous" in pages[0]
    assert "Missing field 1/1: security.dangerous" in pages[0]
    assert_valid_message(pages[0])


def test_evidence_warnings_and_security_flags_are_complete_and_absence_is_not_safety():
    detail = saved_detail(warnings=["Saved warning"])
    detail["token"]["data_warnings"] = ["Token warning"]
    detail["signal"]["warnings"] = ["Signal warning", "Saved warning"]
    detail["token"]["security"] = {"dangerous": True, "flags": ["one", "two"]}
    text = "\n".join(market_evidence_pages(detail))
    for value in ("Saved warning", "Token warning", "Signal warning", "one", "two"):
        assert value in text
    assert "Warning 3/3: Signal warning" in text
    assert "other risks may be unassessed" in text
    assert "safe" not in text.lower()


def test_missing_evidence_lists_remain_unavailable_instead_of_none_recorded():
    detail = saved_detail()
    detail["evaluation"].pop("rejection_reasons")
    detail["evaluation"]["missing_fields"] = "malformed"
    text = "\n".join(market_evidence_pages(detail))
    assert "Blockers: unavailable" in text
    assert "Missing fields: unavailable" in text
    assert "Blockers: none recorded" not in text


def test_compact_evidence_summary_respects_available_space_and_escapes_values():
    evaluation = saved_detail(
        rejection_reasons=['<blocker attr="unsafe">'], missing_fields=["baseline_history"]
    )["evaluation"]
    summary = market_evidence_summary(evaluation, 400)
    assert "Eligible at observation: no · Blockers 1 · Missing 1" in summary
    assert "&lt;blocker attr=&quot;unsafe&quot;&gt;" in summary
    assert "Missing: baseline_history" in summary
    assert len(summary.encode("utf-16-le")) // 2 <= 400
    assert market_evidence_summary(evaluation, 10) == ""
