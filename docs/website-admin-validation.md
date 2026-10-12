# Private diagnostics and controls: issue #24

The owner overview renders scan timing, a discovery/evaluation funnel, provider
snapshot and failure counts, saved evidence coverage, missing/rejected fields,
private delivery counts, and best candidates/near misses. Recent scan history is
an authenticated, bounded projection of the newest 20 records from the last 24
hours. It excludes raw source errors and other scan payloads. Unfinished records
are labeled as records; worker availability depends on the heartbeat, not these
records. Private status is not added to public API responses.

Grouped settings retain enabled chains, exclusions and Strict/Balanced/Broad
presets. Unchanged polling responses preserve unsaved form edits. The saved/active
revision notice explains next-scan activation and the immediate signal pause,
which remains independent of scheduled market reports.

Every action uses the durable preview/confirm workflow. The dialog presents only
changed before/after values, reviewed revision and expiry. Confirmation submits
only the persisted preview ID and reviewed revision; later settings responses
cannot silently replace that revision. Expired or conflicting previews disable
confirmation and instruct the owner to cancel and request a fresh preview. The
worker remains authoritative if expiry or conflict occurs after submission.
Cancellation submits no confirmation. Reset and private synthetic Test Alert
require the same explicit confirmation. Keyboard focus stays inside the dialog;
Escape cancels before submission. Tables remain visible and scrollable on mobile.

Accepted requests display their queued/running state during polling. Recorded
results include the controller's message, with distinct failed and uncertain
states and no optimistic success. History retains IDs and lifecycle timestamps.
Unknown delivery requires inspection before an explicit new owner decision; the
worker does not automatically resend. Synthetic Test Alerts target only the
private owner and never enter public alerts or outcome records.

## Validation

- 860 Python tests passed, including bounded/private scan-history checks and the
  existing owner authorization, shared revision, single-scan, preview binding,
  crash recovery and private synthetic-delivery tests.
- 36 frontend tests passed, including diagnostics/offline state, before/after,
  cancellation, exact confirmation request, expiry/conflict, failed/unknown
  outcomes, unsaved-edit preservation and keyboard focus/Escape.
- Ruff, Prettier, production TypeScript/Vite build and diff checks passed.
- Chromium at 1440px and 390px exercised real cookie sign-in, owner navigation,
  diagnostics, settings preview/confirmation, reset cancellation and Test Alert
  confirmation through a local worker with mock-only Telegram transport. Both
  had no browser errors or horizontal page overflow; the public alert list stayed
  empty. No live Telegram message or provider request was made.

Production VPS/HTTPS and real-data operational acceptance remain in issue #16.
See [owner authentication](website-owner-auth.md),
[durable command validation](website-command-validation.md) and
[deployment/rollback](website-deployment.md).
