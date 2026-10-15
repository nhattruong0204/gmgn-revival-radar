# V2 audit analysis scripts

> **Superseded.** `python -m revival_radar.research` (issue #71, see
> [`docs/research-export.md`](../../research-export.md)) reproduces these analyses with
> explicit windows, tests, a manifest and CSV export. These prototypes are kept only as the
> record behind the audit documents.

Prototype scripts behind `SEVEN_DAY_SIGNAL_REVIEW.md` and
`ALERT_RECORD_CONSISTENCY.md`. Run them only against a **copy** of the database
(they open it with `mode=ro`), never the live scanner file:

```bash
python3 call_performance.py /path/to/copy.db        # per-call MC change, MFE/MAE (writes copy.db.perf.json)
python3 cohort_baseline.py /path/to/copy.db         # calls vs eligible/ineligible baselines at +6h/+24h
python3 revival_universe_movers.py /path/to/copy.db  # movers in the revival universe and rejection reasons
```

Cohort start dates are hard-coded to the audit window (2026-10-09 15:00 UTC,
when the threshold-40 configuration began). Productised in issue #71.
Outputs reflect saved ~5-minute snapshots, not intrabar highs/lows.
