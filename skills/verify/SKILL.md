---
name: verify
description: Use when about to declare any work complete, done, or fixed — before a recap, a ticket closure, or telling someone the work works. Enforces live-probe-before-done.
---

# Verify before done

## Overview

A "done" claim is only allowed to stand on a live probe of the changed path. This
skill exists because the documented failure class is not "we can't verify" — it
is "claimed done on an indirect signal": a scheduler tick counted as a Slack
send, a recap repeating last session's stale claims, a documentation pass
reported applied that was never applied.

**Violating the letter of this gate is violating the spirit of it.** "The work is
obviously fine" is the exact thought this skill exists to catch.

## The gate (run in order, before any done claim)

1. **Live probe the changed path end-to-end.** Real sends, real reads, real job
   traces — not scheduler ticks, not cache hits, not "no errors appeared".
   - Workato recipe/skill/asset → REQUIRED: produce a pass/fail table with
     every row citing a job trace line or job ID.
   - Slack send → the message exists in the thread, permalink in hand.
   - Script/harness change → ran it against real input, observed the new output.
2. **Re-verify prior-session claims you are about to repeat.** Any "done",
   "fixed", or "consent granted" from an earlier session or handoff gets a live
   re-check before it appears in a recap. Stale claims repeat only if
   re-verified true.
3. **Close the list with what was NOT verified.** Every completion claim ends
   with either the specific unverified steps or the single line "Nothing
   unverified." An unverified step that reads as covered is worse than an
   admitted gap.

## Output shape

The verdict is a short table or list: claim → evidence (citation) → verdict. No
evidence, no row reads "done".

## Red flags — stop and go run the probe

- "The scheduler fired, so the notification went out"
- "It should work now" / "No errors in the log, so it's fine"
- "That was confirmed in the earlier session" (without re-checking)
- "Verification can wait until someone asks"
- "Verifying is overkill for this small change"
