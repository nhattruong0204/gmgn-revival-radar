# Seven-day signal and scoring review (V2)

Window: 2026-10-07 13:36 → 2026-10-14 13:36 UTC. Source: consistent production
DB copy (see `ALERT_RECORD_CONSISTENCY.md`). Scripts: `docs/audit/v2-analysis/`.
Every number below is reproducible from those scripts against a DB copy.

> **Sample warning.** 43 calls on 16 tokens, heavily overlapping (one token has 12
> calls), during one market regime, with outcome windows of different lengths.
> Nothing here is statistically significant. Treat correlations as hypotheses.

## 1. Funnel (Fact)

| Stage | Count (7 days) |
|---|---:|
| Scans completed | 2,019 (every ~5 min; 12 scans had a source error) |
| Discovery source | Solana **Hot Search only**, top 20 per scan (`discovery_limit=20`) |
| Evaluations | 102,782 |
| Distinct tokens evaluated | 2,759 |
| Eligible evaluations (passed core filters + base + returning activity) | 402 |
| Evaluations with score ≥ 60 | 16 (and **0** on 10-09, 10-10, 10-11, 10-14) |
| Potential alerts (eligible and ≥ threshold) | 121 |
| Sent calls | 43 (calls #7–#49) |
| Distinct tokens called | 13 |

Daily distinct tokens fell from 1,226 (10-08) to ~250–330 (10-11 → 10-14).

## 2. The single biggest cause of poor calls (Fact)

On **Custom revision 9 (≈ 2026-10-09 15:00 UTC)** `alert_score_threshold` was lowered
from **60 to 40**. The stage ladder starts at WATCH = 40, so since then any
eligible **WATCH** observation is published as a call. 40 of the 43 calls in the
window are WATCH-stage; only 3 are EARLY_REVIVAL and none is REVIVING or higher.

Combined with `alert_cooldown_hours = 6` and `realert_score_increase = 10`
(re-alert only needs the cooldown to expire), the same bleeding token is called
repeatedly as it declines. Example: HOOKED was called at $7.40M → $5.68M → $5.13M →
$3.64M → $1.96M (calls #14, #23, #27, #36, #47).

There is **no stage gate** in the alert decision: `scanner.py` alerts on
`eligible and score >= alert_score_threshold` regardless of stage.

**Hypothesis:** the threshold was lowered because the radar was silent (see
`docs/audit/ALERT_PLAN_2026-10-09.md`). That turned a "revival call" product into
a "something on Hot Search is mildly active" feed, which is what the owner now
perceives as low quality.

## 3. Outcomes of the calls (Fact, observed market cap)

| Metric (43 calls with alert-time MC) | Value |
|---|---:|
| Positive / negative since call | 7 / 36 |
| Median / mean change since call | −11.5% / −20.2% |
| Median observed MFE / MAE | +7.4% / −21.7% |
| Calls with MFE ≥ +25% / ≥ +50% | 7 / **0** |
| First call per token (n=13) median | −26.3% |
| Repeat calls (n=30) median | −9.6% (shorter elapsed time — confounded) |
| Score ≥ 50 (n=14) vs < 50 (n=29) median | −15.0% vs −9.5% |

Existing price checkpoints (Track Record API, 24h horizon): 30 returns available,
9 positive, 21 negative, 13 pending, 0 missed.

## 4. Fair baseline (Fact, with stated bias)

One sample per token per 6-hour bucket from 2026-10-09 15:00 (threshold-40 era),
forward market-cap change from saved snapshots:

| Cohort | +6h n | +6h median | +24h n | +24h median | +24h % positive | +24h MAE ≤ −30% |
|---|---:|---:|---:|---:|---:|---:|
| A. Actual calls | 31 | −2.9% | 24 | −17.5% | 38% | 33% |
| B. Eligible, score 40–59, not called | 24 | −3.3% | 17 | −15.8% | 35% | 29% |
| C. Eligible, score < 40 | 46 | −5.4% | 37 | −16.7% | 22% | 35% |
| D. Ineligible (failed core filters) | 3,130 | −3.8% | 585 | −15.2% | 36% | 51% |

**Finding:** calls perform like every other Hot Search token: about −15% to −17% at
24h. The current scoring produces **no measurable edge** over the baseline in
this window. Cohort D has severe survivorship bias (most rejected tokens stop
being tracked: 3,212 missing at +24h), which inflates its tails.

**Rank correlation with change since call (Spearman, n≈43):** overall score
**+0.01**; Setup +0.27; Trigger +0.26; Confirmation +0.21; base duration +0.24;
bundler ratio −0.30; 1h price change at call −0.20; 5m volume acceleration −0.15.
Overall score shows no relationship with outcome. Hypotheses worth testing on a
larger sample: (a) longer bases and lower bundler share help; (b) calling into a
1h price spike hurts (chasing); (c) 5m volume acceleration is noise at this
threshold.

## 5. Missed and late signals

Within a "revival universe" (MC ≥ $300K, ATH drawdown ≥ 40%, age ≥ 3 days,
≥ 6h tracked): 53 tokens; called n=12, median 24h MFE +9.7%; not called n=41,
median +11.3%. Only 7 tokens reached +50% MFE; 5 of them were not called, all
rejected for `no_base` + `no_returning_activity`.

**Late signal example (Fact):** HOTBOT (`8nnaeW…`) rose +236% from $1.42M over
13.5h while being rejected (`no_base`, max score 12); it was then called (#28) at
$4.53M after the run. It is −3.8% since the call.

The largest raw movers (+300% to +1,900%) were brand-new $10K–$50K launches with
no history. They are outside the revival thesis; missing them is **not** a
scoring failure and they should not drive optimisation.

## 6. Scoring component audit (code + data)

| Component | Assessment |
|---|---|
| Drawdown (Setup 10) | Earned by almost every called token (all are 50–95% below ATH). Non-discriminating. ATH comes from GMGN `ath_market_cap`; provenance not independently verified. |
| Liquidity survival (10), holder retention (5) | Almost always earned; non-discriminating at current floors. |
| Progressive base (≤15) + maturity (5) | `BASE_MIN_HOURS=8` under Custom lets 8–12h ranges count as a "base". Most calls had 8–20h bases. Positive-but-weak correlation suggests requiring longer bases. |
| Compression (5) | Rarely earned; fine. |
| 5m volume vs baseline (15) | Largest single Trigger weight. Absolute floors exist ($2K and 1% of liquidity), so tiny-baseline spikes are already guarded. Weak/negative relation here. |
| 1h volume (10), TX (10) | Hourly ratio vs previous observation, not a matched-time baseline: sensitive to noise. |
| Hot Search climb / Trending | Discovery is Hot Search only, so "both sources" (5) and Trending (5) are almost never earnable → Trigger max effectively 80%. |
| HL / HH / breakout / retest (5 each) | Detected from 1h candles with limited lookback; breakout/retest rarely true. Confirmation is small (30% of total) and binary. |
| Penalties | Bundler penalty only −5 while bundler ratio is the strongest negative signal observed. Insider ratio is usually unknown (GMGN null) and so never penalised. |
| Missingness | Missing evidence stays in the denominator (good, conservative), but missing *risk* evidence earns no penalty (optimistic bias). |

## 7. Market regime

All 49 calls are Solana memecoins in one week. The whole Hot Search universe
declined ~15% per 24h. A revival strategy is expected to struggle in a falling
regime; the scanner has no regime awareness. **Hypothesis:** a simple regime
filter (e.g. median 24h change of the discovered universe) would have suppressed
most of these calls.

## 8. What this does and does not support

- Supported: the threshold-40 configuration publishes WATCH-quality calls that do
  not outperform comparable tokens; repeated calls on declining tokens dominate.
- Not supported: claims that any score component *predicts* success, or that a
  specific new weight set is better. There is not enough data to optimise, and
  tuning on these 43 calls would overfit.

## 9. Recommendations (require owner approval; no live change made)

1. Restore a **stage gate**: publish calls only at `EARLY_REVIVAL` or higher
   (score ≥ 60 with base + returning activity). Optionally keep WATCH as an
   unpublished, website-only "watchlist" stream. Measure for 14 days.
2. **Re-alert only on new evidence**: require a stage upgrade or a new structural
   event (HL/HH/breakout) and a price *above* the previous call's price, not just
   an elapsed cooldown.
3. Raise `BASE_MIN_HOURS` back toward 12–24h for published calls.
4. Strengthen the bundler penalty only after (1)–(3) have produced a cleaner
   sample. Do not retune weights on this week's data.
5. Start logging a cheap regime indicator per scan (universe median 1h/24h change)
   as explanatory evidence first, not as a score input.
