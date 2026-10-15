# Issue #70 verification: Methodology "Live publishing rule"

Evidence is from the synthetic demo build (`VITE_DEMO_MODE=true`), built outside the repo and served on
loopback only (Playwright + Chromium, `--no-sandbox`). No production access.

- New panel on the Methodology page states the saved publishing rule from `/api/v1/meta`
  `publishing_policy` (min stage, score threshold, re-alert rule). Demo data uses the code defaults
  (WATCH, 75, re-alert evidence off), which preserve current behaviour.
- Screenshots: `methodology-390x844.png`, `methodology-1366x768.png` (full page),
  `publishing-rule-390x844.png`, `publishing-rule-1366x768.png` (panel). Horizontal overflow is 0 at both
  widths (`metrics.json`).
- Text-only panel: no new interactive controls, so no new focus or touch targets; colors reuse the existing
  panel tokens. Unit tests cover the default rule, the stage-gated rule with re-alert evidence, and the
  unavailable state (no guessed rule).
- Owner setting controls (Admin runtime settings select for `alert_min_stage` and the Enabled/Disabled
  select for `realert_requires_new_evidence`) need owner sign-in and are not part of the public demo build.
