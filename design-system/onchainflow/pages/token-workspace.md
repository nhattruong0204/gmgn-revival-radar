# token-workspace

Read ../MASTER.md first.

Job: Inspect support, counter-evidence and missing data for one exact chain/address.

Principal action: Read evidence / create source-linked brief (#43).

Justified override: Allow wider charts with contained scrolling and keyboard/table fallback (#42). Keep saved evaluation, later market observation and candle freshness separate. Preserve back/filter context; unavailable data is not a prediction.

All other tokens and state contracts inherit MASTER. Scope implementation under
the relevant backlog issue; this contract does not claim that future features ship
in #51. Verify desktop/mobile, keyboard/touch, loading/empty/stale/partial/error
and permission states when migrating this surface.

#40 implementation: The chart precedes activity summaries. Mobile keeps the score, full chain/contract and exact evaluation timestamp prominent; market metrics follow the chart in a disclosure. Score dimensions/components have one disclosure; duplicated component-reason strings are omitted from narrative support, while counter-evidence stays visible. Chart values have a named scrolling/data-table fallback.

#42 implementation decision: Keep the existing React/SVG renderer for bounded
saved history (supported horizon ≤168 hourly bars), with no runtime dependency.
Linked OHLC/volume panels use one time scale and crosshair; hollow rising/equal
and filled falling bodies supplement color. Native pan/zoom/range buttons provide
non-drag alternatives. Responsive SVG axes stay at 12 CSS px instead of shrinking
inside a fixed viewBox. Full OHLCV table and UTC CSV preserve numeric evidence.

UI UX Pro Max searches: `financial chart comparison --domain chart` verified
candlestick/linked volume guidance and keyboard/table/zoom fallback; `dragging
movements --domain ux` verified single-pointer alternatives; `derived state memo
--stack react` cautioned against speculative memoization. Adopt those behaviors,
retain reviewed palette, and reject automatic provider/library changes. Lightweight
Charts (Apache-2.0 with attribution considerations) would add a renderer but no
market feed; its built-in interactions still need an accessible HTML fallback.
ApexCharts likewise adds licensing/bundle review. Neither is needed for this bounded
SVG workspace; no new license, CDN, font, framework or runtime package ships.

Verification budgets for the final measurement: shipped gzip JS increase ≤10 KiB
against published base; chart range/inspection update ≤100ms on local Chromium
at the supported 168-bar maximum. These are bounded development gates, not VPS
capacity or real-device certification. Existing steady/burst scanner gates remain
unchanged. No production deployment or market-data accuracy certification.
