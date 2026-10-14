"""Complete selected-cohort CSV, with exact UTC times and safe text cells."""

import csv
import io
import json
from datetime import UTC, datetime


def safe_cell(value):
    if isinstance(value, str) and (
        value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))
    ):
        return "'" + value
    return "" if value is None else value


def export_track_record(data):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    metadata = {
        "methodology": data.methodology_version,
        "since_utc": datetime.fromtimestamp(data.since, UTC).isoformat(),
        "as_of_utc": datetime.fromtimestamp(data.as_of, UTC).isoformat(),
        "filters": json.dumps(data.filters, sort_keys=True),
        "reconciliation": json.dumps(data.reconciliation, sort_keys=True),
        "definition": data.definition,
        "exclusions": " | ".join(data.exclusions),
        "limitations": " | ".join(data.limitations),
        "price_units": (
            "Quote currency/provider identity unknown; return_pct and "
            "reported_return_pct are checkpoint price change in percent "
            "(later_price/initial_price-1)*100, not market-cap change"
        ),
    }
    from revival_radar.web.models import CheckpointResearch

    fields = list(CheckpointResearch.model_fields)
    metadata_header = "cohort_metadata_json=" + json.dumps(metadata, sort_keys=True)
    writer.writerow([*metadata, *fields, metadata_header])
    for row in data.items:
        values = row.model_dump()
        for key in (
            "timestamp",
            "saved_at",
            "sent_at",
            "due_at",
            "window_end",
            "observed_at",
            "recorded_at",
        ):
            if values[key] is not None:
                values[key] = datetime.fromtimestamp(values[key], UTC).isoformat()
        writer.writerow(
            [safe_cell(v) for v in [*metadata.values(), *(values[k] for k in fields), ""]]
        )
    return output.getvalue()


JOURNAL_PERFORMANCE_FIELDS = (
    "change_since_call_pct",
    "change_reason",
    "mfe_pct",
    "mae_pct",
    "call_market_cap",
    "latest_market_cap",
    "latest_observed_at",
    "freshness",
    "observations",
    "max_gap_seconds",
    "elapsed_seconds",
    "token_call_index",
    "token_call_count",
    "basis",
    "call_market_cap_source",
    "other_basis_observations",
)


def export_call_journal(data):
    """One row per call with the alert's performance object; same escaping as checkpoints."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    summary = data.summary
    metadata = {
        "methodology": data.methodology_version,
        "since_utc": datetime.fromtimestamp(data.since, UTC).isoformat(),
        "as_of_utc": datetime.fromtimestamp(data.as_of, UTC).isoformat(),
        "filters": json.dumps(data.filters, sort_keys=True),
        "sort": data.sort,
        "reconciliation": json.dumps(data.reconciliation, sort_keys=True),
        "calls": summary.calls,
        "measurable": summary.measurable,
        "mixed_configurations": str(summary.mixed_configurations).lower(),
        "revisions": " | ".join(summary.revisions),
        "definition": data.definition,
        "limitations": " | ".join(data.limitations),
        "units": (
            "change_since_call_pct, mfe_pct and mae_pct are market-cap change in percent "
            "versus the market cap frozen with the alert; not price change or realized P&L"
        ),
    }
    fields = [
        "alert_id",
        "called_at_utc",
        "chain",
        "contract_address",
        "symbol",
        "stage",
        "score",
        "preset",
        "revision",
        *JOURNAL_PERFORMANCE_FIELDS,
    ]
    writer.writerow(
        [*metadata, *fields, "cohort_metadata_json=" + json.dumps(metadata, sort_keys=True)]
    )
    for alert in data.items:
        performance = alert.performance.model_dump()
        values = {
            "alert_id": alert.id,
            "called_at_utc": datetime.fromtimestamp(alert.timestamp, UTC).isoformat(),
            "chain": alert.chain,
            "contract_address": alert.contract_address,
            "symbol": alert.symbol,
            "stage": alert.observation.status,
            "score": alert.score,
            "preset": alert.preset,
            "revision": alert.revision,
            **{name: performance[name] for name in JOURNAL_PERFORMANCE_FIELDS},
        }
        if values["latest_observed_at"] is not None:
            values["latest_observed_at"] = datetime.fromtimestamp(
                values["latest_observed_at"], UTC
            ).isoformat()
        writer.writerow(
            [safe_cell(v) for v in [*metadata.values(), *(values[k] for k in fields), ""]]
        )
    return output.getvalue()
