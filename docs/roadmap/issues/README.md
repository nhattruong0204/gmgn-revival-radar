# V2 issue drafts

Drafted by the 2026-10-14 audit. Created on GitHub on 2026-10-14 as #62–#75 (mapping below). Original creation commands, kept for reference:

```bash
gh label create v2 --color 0e8a16 --force
for l in v2-p0 v2-p1 v2-p2 data-correctness ux research scoring needs-owner-approval release backend frontend tests docs scanner bug blocked-by-v2 deferred; do gh label create "$l" --force; done
gh issue create --title '[V2][P0] Call performance read model: latest market cap, change since call, MFE/MAE' --body-file docs/roadmap/issues/01-v2-d1.md --label v2 --label v2-p0 --label data-correctness --label backend
gh issue create --title '[V2][P0] Alert pages drop the risk warnings saved with the call' --body-file docs/roadmap/issues/02-v2-d2.md --label v2 --label v2-p0 --label data-correctness --label bug
gh issue create --title '[V2][P0] Data-integrity acceptance suite for published calls' --body-file docs/roadmap/issues/03-v2-d3.md --label v2 --label v2-p0 --label data-correctness --label tests
gh issue create --title '[V2][P0] Reusable CallPerformanceCard component' --body-file docs/roadmap/issues/04-v2-u1.md --label v2 --label v2-p0 --label ux --label frontend
gh issue create --title '[V2][P0] Track Record as a call performance journal (follow-up to #45)' --body-file docs/roadmap/issues/05-v2-u2.md --label v2 --label v2-p0 --label ux --label frontend --label backend
gh issue create --title '[V2][P1] Signals feed with call performance and token grouping' --body-file docs/roadmap/issues/06-v2-u3.md --label v2 --label v2-p1 --label ux --label frontend
gh issue create --title '[V2][P1] Plain-language call reasons and risks; stop leaking rule keys' --body-file docs/roadmap/issues/07-v2-u4.md --label v2 --label v2-p1 --label ux --label backend --label frontend
gh issue create --title '[V2][P1] Token page: calls on this token and what changed since each' --body-file docs/roadmap/issues/08-v2-u5.md --label v2 --label v2-p1 --label ux --label frontend
gh issue create --title '[V2][P0] Published-call policy: stage gate and evidence-based re-alerts (owner approval required)' --body-file docs/roadmap/issues/09-v2-q1.md --label v2 --label v2-p0 --label scoring --label needs-owner-approval
gh issue create --title '[V2][P1] Reproducible research export and weekly signal-quality report' --body-file docs/roadmap/issues/10-v2-q2.md --label v2 --label v2-p1 --label research
gh issue create --title '[V2][P1] Record per-scan market regime context' --body-file docs/roadmap/issues/11-v2-q3.md --label v2 --label v2-p1 --label research --label scanner
gh issue create --title '[V2][P1] Define and document market-cap vs price performance (circulating vs FDV)' --body-file docs/roadmap/issues/12-v2-q4.md --label v2 --label v2-p1 --label data-correctness --label docs
gh issue create --title '[V2][P2] Retain replay-grade evidence and build a point-in-time replay harness' --body-file docs/roadmap/issues/13-v2-q5.md --label v2 --label v2-p2 --label research
gh issue create --title '[V2][P1] V2 release gate: data parity, call cards, browser and load evidence' --body-file docs/roadmap/issues/14-v2-g1.md --label v2 --label v2-p1 --label release
```

| File | Priority | Size | Title |
|---|---|---|---|
| `01-v2-d1.md` | P0 | M | [V2][P0] Call performance read model: latest market cap, change since call, MFE/MAE |
| `02-v2-d2.md` | P0 | S | [V2][P0] Alert pages drop the risk warnings saved with the call |
| `03-v2-d3.md` | P0 | M | [V2][P0] Data-integrity acceptance suite for published calls |
| `04-v2-u1.md` | P0 | M | [V2][P0] Reusable CallPerformanceCard component |
| `05-v2-u2.md` | P0 | L | [V2][P0] Track Record as a call performance journal (follow-up to #45) |
| `06-v2-u3.md` | P1 | M | [V2][P1] Signals feed with call performance and token grouping |
| `07-v2-u4.md` | P1 | S | [V2][P1] Plain-language call reasons and risks; stop leaking rule keys |
| `08-v2-u5.md` | P1 | S | [V2][P1] Token page: calls on this token and what changed since each |
| `09-v2-q1.md` | P0 | M | [V2][P0] Published-call policy: stage gate and evidence-based re-alerts (owner approval required) |
| `10-v2-q2.md` | P1 | M | [V2][P1] Reproducible research export and weekly signal-quality report |
| `11-v2-q3.md` | P1 | S | [V2][P1] Record per-scan market regime context |
| `12-v2-q4.md` | P1 | S | [V2][P1] Define and document market-cap vs price performance (circulating vs FDV) |
| `13-v2-q5.md` | P2 | L | [V2][P2] Retain replay-grade evidence and build a point-in-time replay harness |
| `14-v2-g1.md` | P1 | S | [V2][P1] V2 release gate: data parity, call cards, browser and load evidence |

## Created issues

| Draft | Issue |
|---|---|
| V2-D1 | #62 |
| V2-D2 | #63 |
| V2-D3 | #64 |
| V2-U1 | #65 |
| V2-U2 | #66 |
| V2-U3 | #67 |
| V2-U4 | #68 |
| V2-U5 | #69 |
| V2-Q1 | #70 |
| V2-Q2 | #71 |
| V2-Q3 | #72 |
| V2-Q4 | #73 |
| V2-Q5 | #74 |
| V2-G1 | #75 |
