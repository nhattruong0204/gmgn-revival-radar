# signal-journal

Read ../MASTER.md first.

Job: Read immutable alert-time evidence.

Principal action: Open saved signal detail.

Justified override: Use alert time rather than latest token time in the shared stamp. Later evaluations are explicitly separate. Delivery receipt is required; pending/unknown/test deliveries cannot be sent-signal proof.

All other tokens and state contracts inherit MASTER. Scope implementation under
the relevant backlog issue; this contract does not claim that future features ship
in #51. Verify desktop/mobile, keyboard/touch, loading/empty/stale/partial/error
and permission states when migrating this surface.

#45: original alert observation/capture/delivery and checkpoint recording times
are distinct. The four-horizon coverage table links saved inputs and unresolved
states; original brief associations use captured UUIDs only, while later archives
remain visibly separate. Back links preserve the track-record/filter URL. Shared
stat labels wrap inside spans to maintain 320px/200% text reflow without obscuring
icons. No later market snapshot replaces the original alert's frozen evidence.
