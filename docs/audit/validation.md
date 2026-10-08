# Local validation

Run date: 2026-10-08. These results apply to the local working tree, not the VPS.

| Check | Result |
|---|---|
| `ruff check .` | PASS |
| `ruff format --check .` | PASS |
| `pytest -q` | 475 passed, including 29 new audit cases |
| `docker compose config --quiet` | PASS through read-only host execution |
| `docker compose --env-file .env.example config --quiet` | PASS through read-only host execution |
| Isolated offline `revival-radar demo` | PASS; three evaluations, one potential alert, zero deliveries/errors |
| Stdlib `audit_database.py` on the demo copy | PASS; integrity ok, one cohort, three traces; explicit synthetic time window |
| Standalone exporter help / analyzer help | PASS without virtual-environment dependencies |
| Live-WAL online backup test | PASS; committed WAL data included, source/history/schema preserved |
| Export archive integration fixture | PASS; six expected private artifacts, checksum, mode 0600, source unchanged |
| Existing legacy migrations, restart and outcome tests | PASS in full suite; no new schema version |
| Secret checks | PASS: raw exception/URL/env arguments excluded; known-secret export withheld; report metadata credential fields dropped |
| `git diff --check` | PASS |
| Production authenticated inventory / SQL / logs | NOT RUN: runtime lacks private SSH credential entry |
| Live GMGN/Telegram/trades/deployment | NOT RUN; no new API or message requests required for these changes |

The sandbox's snap Docker CLI initially failed to launch; the read-only Compose
check succeeded through host execution. Development dependencies were installed
only in this repository's local ignored virtual environment. No VPS package was
installed. Initially three subprocess tests failed because a relative `PYTHONPATH`
did not work from temporary directories; installing the project locally resolved
that environment issue and all 446 pre-existing tests then passed.

Tests with synthetic future observations verify as-of outcome exclusion. Tests also
verify candidate set-intersection denominators, config revision/version/fingerprint
isolation, left-censored delay exclusion, sparse MFE/MAE, missing returns, interrupted
scan state, existing source-tier starvation mechanism and short-base blocker detail.
Fixture outcomes are not empirical signal performance.

Production completion requires the private export and same-cutoff UI reconciliation.
Do not interpret this validation as a complete quantitative production audit.
