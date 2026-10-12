# Durable owner commands: issue #23

The separate web API writes only owner sessions, previews and queued commands.
The scanner owns schema-5 migration and consumes commands through the same
controller as Telegram. It retains the single scan event and immutable settings
for each scan; command processing continues while a scan awaits provider work.

## Queue and recovery evidence

`tests/test_web_commands.py` and `tests/test_web_admin.py` cover authenticated
submission and polling, anonymous rejection, action/settings allowlists, bounded
queues, owner isolation, duplicate IDs bound to the exact request, canonical
preview payloads, expiry, one-use confirmation and stale revisions from Telegram.

Restart tests cover queued work before claim, interruption after claim and after
confirmation preparation, settings committed before SQLite acknowledgement,
transient acknowledgement failures, mismatched receipts, and uncertain Test Alert
delivery. A Test Alert sent before an acknowledgement crash is not sent again.
Settings recovery requires the atomically saved command ID, target revision and
payload fingerprint; matching settings alone cannot establish success. Interrupted
non-settings actions remain unknown and require an explicit new owner decision.
Command results and logs exclude provider credentials and transport error text.
Completion timestamps now reflect completion after asynchronous execution.

## Worker state and readiness

The owner status endpoint provides the persisted heartbeat, saved and active
revisions, allowlisted settings, and the current scanner schedule. A successful
API response only establishes that the API can read the compatible database.
`worker.available` requires an enabled worker with a heartbeat at most 30 seconds
old, no future timestamp, and no recorded graceful shutdown. A persisted running
scan is historical evidence and cannot override that availability decision.

Graceful worker cancellation publishes `stopped_at` immediately without clearing
recoverable command rows. Startup publication clears it after recovery. Abrupt
termination or a failed shutdown status write is detected through heartbeat expiry.
Settings may remain readable when unavailable; they represent the last persisted
snapshot, not proof of a live bot. Heartbeat updates use the existing asynchronous
worker; blocking the entire event loop or a slow command can make it unavailable
until processing resumes, rather than falsely claiming it is responding.

## Migration and release validation

The backed-up schema-4 migration fixture compares alerts (the cooldown source),
alert-time signals including unknown legacy fields, saved presentation/state,
outcomes and snapshots before and after migration. The controls file, revision
and Telegram polling offset remain byte-for-byte intact. No new schema version
is required for the shutdown field in the JSON state snapshot; earlier schema-5
snapshots without that field still expire by heartbeat age.

Rollback requires the matching database and controls backups with both processes
stopped. See [deployment and rollback](website-deployment.md); never change the
schema number to make an older scanner accept a newer database.

Local validation passed: 53 queue/authentication tests, all 859 Python tests,
Ruff lint/format checks, 27 frontend tests, the production build and Prettier.
Validation uses synthetic fixtures and mock transports. Live provider, Telegram,
VPS, HTTPS and production-size checks remain part of umbrella issue #16.
