# Setup / Trigger / Confirmation scoring (issue #4)

New observations use deterministic scoring version `setup-trigger-confirmation-v1`.
Each dimension is **0–100**, independently of the overall 0–100 score. No model,
LLM or price forecast is involved. Caches, request budgets, polling, delivery controls,
strategy presets and the Solana-only default remain in place.

## Formula and weights

A dimension is the rounded percentage of its configured positive component weight
that was earned. Missing evidence earns no component and remains in the denominator;
it is not treated as observed zero data. A dimension with no configured component
weight scores zero. The overall positive score is the rounded weighted mean of the
three dimension scores. Risk deductions then reduce it, clamped at zero. Failed
core filters still cap the overall result at 39 and force `IGNORE`.

| Dimension | Components and default weights |
|---|---|
| Setup | ATH drawdown 10; surviving liquidity 10; holder retention 5; progressive base up to 15; base maturity bonus 5; compression 5 |
| Trigger | 5m volume vs baseline 15; 1h volume acceleration 10; TX acceleration 10 once; Hot Search climb 5; current Trending 5; both current discovery sources 5 |
| Confirmation | Higher low 5; higher high 5; breakout 5; retest 5 |

The default overall allocation is **Setup 30 : Trigger 40 : Confirmation 30**.
Change it with `WEIGHTS__SETUP_DIMENSION`, `WEIGHTS__TRIGGER_DIMENSION`, and
`WEIGHTS__CONFIRMATION_DIMENSION`. Shares are normalized, so they need not sum to
100. All-zero allocation produces a zero overall score. Every component remains
configurable through existing `WEIGHTS__<NAME>` settings, including new
`COMPRESSION`, `TRENDING`, and `RETEST` components. Removing a component's weight
also removes it from its dimension denominator; raw factor points are not directly
additive overall points. Telegram's **Why this alert** explains this distinction.

Existing penalties remain overall point deductions: concentration −10, insider
holding-ratio decrease −20, developer holding-ratio decrease −25, liquidity drop
−25, danger −40, sniper −5, bundler −5. Their weights and thresholds remain
configurable. Known danger independently prevents eligibility and forces `IGNORE`,
even if its penalty weight is zero. Unknown security retains the existing warning
policy and is not described as safe. Holding-ratio decreases are proxies, not proof
of selling.

## Meaningful activity

A ratio alone never earns a volume/TX bonus. The matching time window must meet its
absolute floor; volume must also meet its volume/liquidity floor.

| Setting | Default |
|---|---:|
| `MIN_TX_5M_FOR_ACCELERATION` | 10 transactions |
| `MIN_TX_1H_FOR_ACCELERATION` | 60 transactions |
| `MIN_VOLUME_5M_FOR_ACCELERATION` | $2,000 |
| `MIN_VOLUME_5M_LIQUIDITY_RATIO` | 0.01 (1%) |
| `MIN_VOLUME_1H_LIQUIDITY_RATIO` | 0.06 (6%) |
| `VOLUME_ACCELERATION_THRESHOLD` | 1.5× |
| `TX_ACCELERATION_THRESHOLD` | 1.5× |

The hourly absolute volume floor is the existing `MIN_VOLUME_1H` setting, including
preset overrides. The 5m volume ratio uses the existing finite-history baseline;
hourly volume and TX ratios use the previous fresh observation. 5m and hourly TX
acceleration can earn the single TX bonus independently, provided that same
window meets its count floor. A quiet 5m window does not erase genuine hourly
activity, and a high count in one window cannot rescue tiny activity in the other.

`Acceleration.volume_5m_to_liquidity` and `.volume_1h_to_liquidity` are uncapped
ratios shown in full details. Missing/zero liquidity or a non-finite division is
unknown, not fabricated zero. Ordinary acceleration ratios retain the existing
cap and zero-baseline policy. Equality at a floor qualifies.

The scanner and persisted diagnostics use the explicit `returning_activity` evidence
flag; zero configurable bonus weights do not invent activity or erase actual
activity evidence. Tiny surges with no qualifying activity skip candle/security
work. Existing alert eligibility still needs core filters, a detected base,
meaningful volume **or** TX acceleration, and no known dangerous flag; the separate
alert score, pause, dry-run and cooldown rules still control delivery.

## Progressive base quality

Only an available, detected stable base meeting `BASE_MIN_HOURS` receives credit.
Strict still requires 24h; Balanced 12h; Broad 8h. A shorter observed range cannot
bypass this gate merely because the scoring bands start at 6h.

| Detected duration | Fraction of `WEIGHTS__BASE` | Default base points |
|---|---:|---:|
| 6–12h | 1/3 | 5 |
| 12–24h | 8/15 | 8 |
| 24–48h | 0.8 | 12 |
| 48h+ | 1 | 15 |
| 72h+ | Optional additional maturity weight | +5 |

Set `BASE_MATURITY_HOURS=[6,12,24,48,72]` and
`BASE_MATURITY_FRACTIONS=[0.3333333333333333,0.5333333333333333,0.8,1]` as JSON arrays
in `.env`. Durations must strictly increase and fractions must be nondecreasing in
0–1. The maturity bonus uses the later of the fifth duration and the existing
`BASE_SUFFICIENT_HOURS`, preserving that setting as a minimum. Compression earns
its small Setup weight when the already-computed metric reaches
`VOLATILITY_COMPRESSION_THRESHOLD=0.25`. No chart-pattern algorithm is redesigned.

## Evidence-aware stages

Evaluate from the strongest satisfied stage downward. A high total cannot substitute
for missing chart evidence; attention-only tokens stay at `WATCH` at most.

| Stage | Default numeric requirement | Additional evidence |
|---|---:|---|
| `IGNORE` | Below 40 | Also forced by failed core filters or known danger |
| `WATCH` | 40+ | Core checks pass, but no higher evidence/score stage qualifies |
| `EARLY_REVIVAL` | 60+ | Detected base and meaningful returning activity |
| `REVIVING` | 70+ | Early evidence plus HL, HH, breakout or retest |
| `STRONG_REVIVAL` | 80+ | Base ≥48h; meaningful volume and TX levels in a matching window; HL + HH, or breakout/retest plus HL or HH |
| `CONFIRMED_REVIVAL` | 85+ | Base ≥72h; meaningful levels; HL + HH and breakout or retest |

The stage's meaningful-level condition checks absolute volume/liquidity and TX
floors in the same window. Top stages for an off-ranking token additionally require
both actual volume **and** TX acceleration, plus HL + HH and breakout/retest.
An off-ranking token can still alert without a top stage if normal eligibility
and the alert score threshold pass. Stale rank fields never earn current Hot Search,
Trending or both-source credit without current discovery provenance.

Change stage score requirements using `WATCH_SCORE_THRESHOLD`,
`EARLY_REVIVAL_SCORE_THRESHOLD`, `REVIVING_SCORE_THRESHOLD`,
`STRONG_REVIVAL_SCORE_THRESHOLD`, and `CONFIRMED_REVIVAL_SCORE_THRESHOLD`. They
must be nondecreasing, within 0–100. Change mature-stage durations with
`STRONG_BASE_MIN_HOURS=48` and `CONFIRMED_BASE_MIN_HOURS=72`; confirmed duration
must cover strong duration. Lower numeric thresholds still cannot bypass structure.
The confirmed score default is 85 so a fully evidenced watchlist revival can reach
it even without current ranking points; stronger off-ranking evidence remains mandatory.

Discovery context is saved as `ACTIVE_DISCOVERY`, `WATCHLIST_REVIVAL`, or
`RANKING_UNAVAILABLE`. Incomplete ranking coverage is not silently called a known
ranking absence, and still receives conservative off-ranking top-stage guards.

## Configuration and compatibility

The example `.env` includes all new activity/maturity/stage values. New thresholds
are configured through environment settings and applied after restart. Existing
Telegram preset controls and weight editing continue to work. Non-secret thresholds,
allocations and score version are persisted with each new observation; later edits
cannot rewrite an older alert's explanation.

No SQLite migration is needed beyond the existing schema version 3. New fields
are inside persisted signal JSON in the existing state table. Older alerts and
snapshots are neither rescored nor backfilled. Legacy dimension fields default to
unknown and remain hidden; existing stage strings such as `EARLY_WATCH` and
`HIGH_CONVICTION_REVIVAL` still render. New messages show all three dimensions
out of 100, with evidence in **Why this alert / Full details**. The explicitly
synthetic Telegram test message now uses the same current scorer.

Old and new totals have different meanings; an old 90 is not the same calculation
as a new 90. Existing stored cooldown scores remain intact until the normal cooldown
expires. Risk warnings, known-danger blocking and unknown-data handling are preserved.

Tests cover the requested tiny 2 → 4 surge, real early revival, mature confirmed
revival, pure +250% pump, dead token, maturity boundaries, null/zero liquidity,
matching-window floors, zero weights, off-ranking guards, partial ranking failures,
configuration validation, saved JSON compatibility, and scanner request gating.
