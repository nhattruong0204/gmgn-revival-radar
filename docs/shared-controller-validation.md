# Shared bot controller (#22) — 12 October 2026

The extraction was already present in the website draft. `run_service()` constructs
one `SharedController` with its existing `RuntimeSettings`, scan-request event and
schedule provider. Telegram receives that controller; the web command worker uses
it too. Neither transport creates another settings-file writer or starts a scanner.

The completed boundary now rejects a Telegram/controller pairing with different
runtime instances. Published controller snapshots deep-copy preset dictionaries,
so a caller cannot change global presets by modifying a returned snapshot.

## Shared semantics

Settings/preset preview, validation, revision conflict handling, apply/reset,
pause/resume, Scan Now and private synthetic Test Alert all pass through the shared
controller. Telegram retains rendering, owner authorization, confirmations and its
polling offset. The durable web worker retains its own owner-bound preview/receipt
protocol; it does not write controls independently.

Ordinary strategy edits are saved immediately and become active at the next scan.
An ongoing scan keeps its immutable configuration and active revision. Delivery
mute checks read current runtime state, so pause/dry-run suppression applies without
restarting that scan; an already-started send may finish. Scheduled market reports
keep their independent controls. Scan Now requests the existing scheduler, retaining
its queued/running states, one-worker lock and provider pacing/cooldowns.

Test Alert validates the private owner and constructs an explicitly synthetic local
message. Telegram renders/sends it to the owner through its authorized transport;
web commands use the same message and private destination. Neither path creates
public alert/outcome/scan records or calls market providers.

## Evidence

Six new controller tests demonstrate exact runtime identity, immutable published
presets, shared web/Telegram revisions with stale confirmation rejection, retained
polling offset, immutable scan/pause/report semantics, scheduler delegation without
settings changes, and private synthetic Telegram tests without database creation.

The 97 targeted controller/Telegram/service/web-command tests passed. Existing
regressions cover owner menus and authorization, validation/conflicts, idle/running
Scan Now non-overlap, immediate mute behavior and web Test Alert isolation. The full
Python suite and CI are run before merge. No live messages or provider requests are
used; production deployment remains in the separate #16/#26 release work.
