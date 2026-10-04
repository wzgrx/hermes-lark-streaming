# 0.20.17 real-interface acceptance and smoke cleanup

Date: 2026-10-05 (Asia/Shanghai). This is a CLI/test-only maintenance;
the Gateway renderer, frozen V1 geometry, hooks and package version are unchanged.

## Actual native API check

The installed 0.20.17 managed runtime submitted one **unattached** CardKit entity
and closed it. Four full updates were accepted: sealed continuation, completed
answer, failed answer and stopped answer. Inputs included actual local terminal
exit codes 0 and 7 plus one deliberately missing callback. Continuation retained
the running snapshot; terminal variants displayed an unconfirmed result.
All four stayed inside local card budgets (serialized JSON about 6–12 KB).
No chat received this entity, no model was called and no real usage was inserted.

This verifies native schema/lifecycle acceptance, **not** a real inbound Gateway
turn or client visual expansion. Keep those acceptance stages separate.

## Defect found in the real-chat smoke command

`smoke --execute --chat-id ...` previously had no cleanup around attach/stream
errors. An already created entity could be left streaming after a failed probe;
raw transport exceptions also escaped the CLI. This was reproduced by fault
injection (the first eight new regressions failed on the old implementation),
not by deliberately breaking the user's live Gateway or sending duplicate tests.

The smoke command now:

- closes an owned entity on failure/cancellation, using a higher sequence after
  an uncertain previous response;
- sends at most once at the probe layer and reports ambiguous attachment as
  `unknown`, never as proof of delivery or an instruction to resend;
- retains the original failure separately from cleanup failure;
- emits safe structured error codes/types without raw exception messages;
- exits nonzero on failure and explicitly separates native API success from
  Gateway-turn/client-visual verification.

If creation itself times out before returning an entity ID, the probe has no ID
to close. `entity_created` means an ID was received, not proof that an uncertain
server-side creation never happened. No automatic retry is added.

This fixes the **opt-in smoke tool**, not the separate production controller.
Tests include cancellation, configuration failure and lost send/close responses.
No dependencies, credentials, sessions, database schema or service settings change.

## Local gate

**1650 passed**, with two existing SDK deprecation warnings, against the deployed
Hermes fixture. Ruff and mypy (49 source files) pass. Ten added regressions cover
the live smoke failure/reporting contract. The user chose interface/code testing
for this round, so no new inbound test message or desktop visual result is claimed.
