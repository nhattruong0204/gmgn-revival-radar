# owner

Read ../MASTER.md first.

Job: Operate one scanner through authenticated previews.

Principal action: Preview and confirm an exact change.

Justified override: Operational density can exceed public research density. Preserve keyboard-accessible dialogs, durable result history and queued/failed/uncertain states. No public/subscriber identity grants owner privileges.

All other tokens and state contracts inherit MASTER. Scope implementation under
the relevant backlog issue; this contract does not claim that future features ship
in #51. Verify desktop/mobile, keyboard/touch, loading/empty/stale/partial/error
and permission states when migrating this surface.

#40 implementation: Use the shared native modal and capture the originating control before disabling it for an async request. Restore it on cancellation. Pending confirmations cannot be dismissed/repeated through Escape. Keep exact preview values and revision checks, history and authentication behavior unchanged.
