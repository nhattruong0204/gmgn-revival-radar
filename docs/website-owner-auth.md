# Owner authentication and rotation (#21)

Owner access is disabled by default. Configure the web process separately from the
scanner: it needs the existing SQLite path, `WEB_ADMIN_ENABLED=true`, an owner
scrypt hash in `WEB_ADMIN_PASSWORD_HASH`, and the exact public HTTPS origin in
`WEB_TRUSTED_ORIGIN`. Keep `WEB_COOKIE_SECURE=true` in deployment. The web process
needs no GMGN, Helius or Telegram credentials. Session tables are created only by
the scanner-owned schema migration, never by API startup or public requests.

## Create and rotate the credential

On the trusted host, in an interactive terminal with the project virtualenv active:

```bash
python -m revival_radar.web.auth
```

Enter and confirm a strong owner password of at least 12 characters. The command
prints a fresh salted scrypt hash, not the password. Store the hash in the protected
web service environment (`/etc/revival-radar/web.env`, root-owned mode 600 in the
provided deployment layout). Do not put plaintext passwords in shell arguments,
source, chat or logs. Do not use a proxy placeholder for this locally consumed hash.
Use the interactive prompt; never pipe plaintext passwords into the command.

For rotation, generate a fresh hash, replace `WEB_ADMIN_PASSWORD_HASH`, and restart
only the web service. Every authenticated read and mutation verifies the session's
persisted proof against the current credential hash. Replacing the hash therefore
revokes all previous sessions without changing the database schema. The next login
requires the new password. A fresh hash for the same password also revokes sessions.
Existing sessions from the previous proof format will require sign-in after upgrade.

Previously accepted worker commands may still complete; rotation does not change
scanner scheduling or delivery behavior. Previews and command lookups remain bound
to their session. After signing in again request a fresh preview; never retry an
unknown delivery result blindly. Do not restore an old password hash to undo rotation:
that could restore access to matching unexpired sessions.

## Session and access boundaries

Sessions expire after the configured `WEB_SESSION_HOURS` (12 by default). Cookies
are HttpOnly, Secure and SameSite=Strict, scoped to `/api/v1/admin`. Server storage
contains hashes of the session and CSRF tokens. Logout removes the stored session
and cookie. Status, settings and command lookups/submission independently enforce
server authorization, including guessed command IDs and expired/revoked cookies.
Anonymous session-status/sign-in is intentionally allowed; public browsing needs
no login. Another session cannot read private command results.

Mutations require the matching Origin plus a valid session-bound `X-CSRF-Token`.
Malformed/non-ASCII origins are safely rejected, not allowed to raise an internal
comparison error. Sign-in checks Origin and permits ten attempts per client address
per 15 minutes. Limits are process-local; use one API worker and only trusted proxy
IP forwarding. API validation errors and logs must not echo passwords or cookies.

The admin UI polls session state about every 30 seconds while visible, immediately
rechecks it after an unauthorized protected read, and clears confirmations/private
layout when signed out or expired. Server expiry checks apply immediately even when
a browser is hidden or offline. In-flight operations are aborted locally on loss of
access; they are not assumed cancelled after server acceptance.

## Verification on 12 October 2026

17 backend owner tests pass, covering salted/fixed-cost hashing, cookies, logout,
expiry, origin/CSRF rejection, throttling, disabled/unmigrated storage, command
ownership, password-hash rotation across every protected route, and absence of
password/hash/cookie/CSRF values from public data and captured logs. Public Radar
remains readable by anonymous and expired users.

27 frontend tests, TypeScript/production build and formatting pass. The new UI
regression verifies that an unauthorized protected read causes sign-in and removes
private navigation. Chromium checks at 1440px and 390px exercise login, protected
settings, expiry, credential-hash rotation and logout using an isolated fixture
server; no live provider or Telegram requests are used. Production HTTPS launch
remains in #16/#26 and is not claimed by these local checks.
