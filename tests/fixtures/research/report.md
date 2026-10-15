# Signal-quality report

Window: 2027-01-15T00:00:00Z to 2027-01-17T00:00:00Z (UTC). Observations are read up to the end of the window. Cohort and universe sample start: 2027-01-15T00:00:00Z. Schema version 7.

> **Sample warning.** 4 calls on 3 tokens. Calls overlap on a few tokens, outcome windows differ in length, and the window is one market regime. Treat everything below as descriptive, not statistically established.

## 1. Funnel

| Stage | Count |
| --- | ---: |
| Scans completed (finished) | 48 |
| Scans that reported errors | 1 |
| Evaluations | 97 |
| Distinct tokens evaluated | 5 |
| Eligible evaluations | 72 of 97 (74.2%) |
| Evaluations with score >= 60 | 12 of 97 (12.4%) |
| ... of which eligible | 12 of 12 (100.0%) |
| Potential alerts (sum over scans) | 3 |
| Sent calls | 4 |
| Distinct tokens called | 3 |

Daily (UTC):

| Date | Evaluations | Tokens | Eligible | <40 | 40-59 | >=60 | Potential alerts | Sent |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2027-01-15 | 72 | 5 | 48 | 36 | 24 | 12 | 3 | 4 |
| 2027-01-16 | 25 | 2 | 24 | 1 | 24 | 0 | 0 | 0 |

## 2. Call outcomes (observed market cap)

| Metric | Value |
| --- | --- |
| Calls in window | 4 |
| Calls with a known change since call | 3 (unknown, excluded: 1; no_call_mc: 1) |
| Positive since call | 2 of 3 (66.7%) |
| Negative since call | 1 of 3 (33.3%) |
| Exactly 0% | 0 of 3 (0.0%) |
| Median change since call | +10.0% |
| Mean change since call | +16.0% |
| Median observed MFE | +10.0% (n=3) |
| Median observed MAE | +10.0% (n=3) |
| Calls with MFE >= +25% | 1 of 3 (33.3%) |
| Calls with MFE >= +50% | 1 of 3 (33.3%) |

## 3. Cohort baseline (forward market-cap change)

| Cohort | Horizon | n (ok) | Coverage ok / pending / missing / no_base | Median | Mean | Positive | Median MFE | Median MAE | MAE <= -30% |
| --- | --- | ---: | --- | ---: | ---: | --- | ---: | ---: | --- |
| A. Actual calls | +6h | 4 | 4 / 0 / 0 / 0 | +5.0% | +10.0% | 2 of 4 (50.0%) | +10.0% | +0.0% | 0 of 4 (0.0%) |
| A. Actual calls | +24h | 4 | 4 / 0 / 0 / 0 | +5.0% | +12.0% | 2 of 4 (50.0%) | +10.0% | +0.0% | 0 of 4 (0.0%) |
| B. Eligible, score 40-59, not called | +6h | 8 | 8 / 0 / 0 / 0 | +0.0% | -1.2% | 1 of 8 (12.5%) | +0.0% | +0.0% | 0 of 8 (0.0%) |
| B. Eligible, score 40-59, not called | +24h | 5 | 5 / 3 / 0 / 0 | +0.0% | -0.4% | 1 of 5 (20.0%) | +0.0% | +0.0% | 0 of 5 (0.0%) |
| C. Eligible, score < 40 | +6h | 2 | 2 / 0 / 0 / 0 | +0.0% | +0.0% | 0 of 2 (0.0%) | +0.0% | +0.0% | 0 of 2 (0.0%) |
| C. Eligible, score < 40 | +24h | 2 | 2 / 0 / 0 / 0 | +0.0% | +0.0% | 0 of 2 (0.0%) | +0.0% | +0.0% | 0 of 2 (0.0%) |
| D. Ineligible (failed core filters) | +6h | 2 | 2 / 0 / 1 / 2 | +10.0% | +10.0% | 1 of 2 (50.0%) | +30.0% | +0.0% | 0 of 2 (0.0%) |
| D. Ineligible (failed core filters) | +24h | 2 | 2 / 0 / 1 / 2 | +20.0% | +20.0% | 2 of 2 (100.0%) | +60.0% | +0.0% | 0 of 2 (0.0%) |
| E. Eligible, score >= 60, not called | +6h | 2 | 2 / 0 / 0 / 0 | +100.0% | +100.0% | 1 of 2 (50.0%) | +100.0% | +0.0% | 0 of 2 (0.0%) |
| E. Eligible, score >= 60, not called | +24h | 2 | 2 / 0 / 0 / 0 | +100.0% | +100.0% | 1 of 2 (50.0%) | +100.0% | +0.0% | 0 of 2 (0.0%) |

**Survivorship and coverage bias.** Samples exist only while the scanner kept saving snapshots for a token. Tokens that fall out of discovery (most ineligible tokens) stop being tracked, so their later outcomes are `missing` and are excluded rather than counted as losses or zeros; the surviving subset can bias both tails. Cohorts are not independent: the same token appears in several 6h buckets and cohorts, and forward windows overlap, so effective sample size is smaller than n. All cohorts share one market regime.

## 4. Rank correlations with change since call (hypotheses)

| Feature | n (pairs with both known) | Spearman rho vs change since call |
| --- | ---: | ---: |
| Overall score | 3 | +1.00 |
| Setup score | 3 | -0.50 |
| Trigger score | 3 | +1.00 |
| Confirmation score | 3 | -0.50 |
| Base duration (hours) | 3 | -0.50 |
| Bundler ratio | 3 | -0.50 |
| 1h price change at call | 3 | +0.50 |
| 5m volume acceleration | 3 | +0.50 |

These are hypotheses, not findings: calls overlap heavily on a few tokens, have outcome windows of different length, and come from one market regime. Pairs with an unknown value on either side are dropped, never counted as 0.

## 5. First versus repeat calls

| Group | Calls | n with known change | Median change | Positive |
| --- | ---: | ---: | ---: | --- |
| First call on a token | 3 | 2 | +19.0% | 1 of 2 (50.0%) |
| Repeat call on a token | 1 | 1 | +10.0% | 1 of 1 (100.0%) |
| Score >= 50 | 2 | 2 | +30.0% | 2 of 2 (100.0%) |
| Score < 50 | 2 | 1 | -12.0% | 0 of 1 (0.0%) |

A repeat call has a shorter elapsed time on average, so first-versus-repeat is confounded with horizon length. First/repeat counts every delivered call up to the cutoff, including those before the window.

## 6. Missed movers in the revival universe

Revival universe: first observation with market cap >= $300,000, drawdown from ATH >= 40%, token age >= 3 days, tracked >= 6h. Tokens with unknown drawdown or age are excluded. 2 tokens qualify.

| Group | Tokens | Median 24h MFE | MFE >= +50% | MFE >= +100% |
| --- | ---: | ---: | --- | --- |
| Called | 1 | +10.0% | 0 of 1 (0.0%) | 0 of 1 (0.0%) |
| Not called | 1 | +60.0% | 1 of 1 (100.0%) | 0 of 1 (0.0%) |

Top missed movers (not called; 24h MFE from first observation):

| Token | Address | MC at first obs | Drawdown | 24h MFE | To peak | Max score | Eligible | Top rejection reasons (evaluations) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TE | TETETE | $1.00M | 50% | +60% | 10.0h | 12 | no | no_base (11), no_returning_activity (11) |

Very large raw movers are often brand-new low-cap launches outside this universe; missing them is not evidence of a scoring failure.

## 7. Late calls

Late call: within the 24h before the call the token's saved market cap rose at least +50% from an earlier low (call market cap included). 1 of 3 (33.3%) of calls with saved pre-call snapshots (1 without pre-call snapshots, excluded).

| Call | Token | Called at (UTC) | Run-up in prior 24h | Rise to call from low | Change since call |
| ---: | --- | --- | ---: | ---: | ---: |
| 4 | CCC | 2027-01-15T04:00:00Z | +100% | +100% | +50.0% |

## 8. What this does not support

- Nothing here establishes that any score, stage or component predicts success, or that a
  different weight set or threshold would be better. Do not tune on one week of data.
- Results are market-cap observations from ~5-minute saved snapshots. They are not executable
  returns: fees, slippage, liquidity depth, latency and position sizing are ignored.
- Samples are small, overlapping (the same token is called and sampled repeatedly), taken in
  one market regime, and have outcome windows of different length. No significance is claimed.
- The cohort baseline suffers survivorship and coverage bias (see above); a cohort with many
  `missing` samples is not comparable to one with few.
- Rank correlations are exploratory hypotheses with the stated n; they are not tested
  relationships and make no causal claim.
- Market-cap basis (circulating versus fully diluted) is unverified for rank-sourced rows
  (docs/performance-metrics.md); ATH-based figures use the provider's basis.
- "Missed" and "late" are descriptive labels over saved data. They do not say a missed token
  should have been called, and nothing here is a forecast, a safety rating or investment advice.
