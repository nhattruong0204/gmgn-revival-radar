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
