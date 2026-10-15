# Issue #88 verification: Signal detail checkpoint de-duplication

Evidence is from the synthetic demo build (`VITE_DEMO_MODE=true`), built outside the repo and served on
loopback only (Playwright + Chromium, `--no-sandbox`). No production access.

## What changed

- Removed the "Observed after the alert" panel from Signal detail; it repeated the card's +1h/+6h/+24h/+72h values.
- Each observed checkpoint in the card strip now shows a visible "Observed <exact time>" line (`<time datetime>`),
  so exact observation times are reachable by keyboard, touch and screen reader with no hover-only content.
  An observed value without a stored time says "Observation time unavailable".
- Pending, missed and unavailable cells keep their text state and show no time.
- The caveat now reads "...not market-cap change; not executed-trade P&L." and appears once on the page.
- The "Saved checkpoint coverage" table and Call record are unchanged. No other layout changes.

## Evidence (this directory)

- `signal-detail-390x844.png`, `signal-detail-1366x768.png`: full page.
- `checkpoint-strip-390x844.png`, `checkpoint-strip-1366x768.png`: the strip.
- Measured at both widths: 0 "Observed after the alert" headings, 3 visible observation times plus 1 Pending
  (+72h), caveat occurrences = 1, no horizontal page scroll.

## Tests

`publications.test.tsx`: no checkpoint value rendered twice, one caveat, `<time>` with machine-readable datetime,
pending and missed states render in the strip without times or a duplicate panel.
