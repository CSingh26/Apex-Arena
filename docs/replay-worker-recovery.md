# Replay worker ownership and restart recovery

Replay workers use the nullable `replay_owner_token` and
`replay_owner_expires_at` columns added by migration `20260912_0016` (after
`20260911_0015`). Ownership is internal and is absent from public playback
responses. API database connections may remain pooled; replay ownership does
not require a session advisory lock.

## First rollout prerequisite

Drain and stop **every legacy API/combined/all process that can run replay
workers before starting this version**. Prevent replay control requests during
that drain, apply the additive migration through the approved deployment
procedure, then start the new version and reopen controls. An ownerless row
cannot distinguish a healthy legacy worker from an interrupted one. A rolling
overlap with legacy replay workers is therefore unsafe, even with the new
columns present. Rolling overlap among this version's lease-aware workers is
supported. No deployment or production migration was performed as part of
this change.

Before rolling back to code without ownership, drain lease-aware replay
workers as well. The additive nullable columns can remain while old code is
running; schema rollback must follow the deployment procedure and must not
remove columns used by running workers.

## Recovery behavior

Each API/combined/all lifespan runs reconciliation before accepting traffic.
It pauses existing, unpaused playback rows only when the room is `replaying`,
its mode is `replay` or `archived`, and there is no unexpired owner. Playback
and room status change in one transaction. Both cursors, playback and room
lap fields, speed, start time and last-event time survive. Missing playback
rows are not created; LIVE and non-running states are excluded. Repeating
reconciliation makes no further changes.

Leases last 30 seconds and renew every 10 seconds using PostgreSQL wall time.
Renewal runs independently of initialization, long event/discussion awaits,
and paused playback loops. One additional startup sweep after 30 seconds
recovers a worker that died shortly before startup while its lease was still
valid. This is a bounded startup recovery pass, not a continuous orphan monitor.
The sweep is cancelled on shutdown. Initial recovery failure fails startup
and closes services; a deferred failure is logged with its exception type.

Claims, renewal, release, recovery, and replay mutations take the same playback
row lock. Operations that also change the room lock playback before room.
They recheck ownership using database time after obtaining the lock. An expired
worker cannot renew, advance cursors, complete/fail a room, reset discussion,
or release a replacement worker's token. Playback and room running/terminal
transitions commit together. Ownership loss stops local work; database failures
leave expiry available for recovery. Closing the service cancels in-flight
initialization and renewal as well as workers.

Recovery leaves the room paused. Use the existing explicit resume control to
rebuild the recorded prefix and continue from the preserved event cursor.
Restart retains its existing meaning: reset playback and discussion.

## Controls across API instances

An instance can operate its own worker. A control without a local worker claims
a temporary operation lease and releases it on success, error, or cancellation.
A healthy owner on another instance causes HTTP 409 with a retry message for
start, restart, resume, pause, speed, and seek controls. Clients may retry, but
there is no guarantee that the next request reaches the owning instance.
No affinity settings or deployment routing were changed.

This change does not implement cross-process command routing or independent
per-viewer playback. Room controls remain shared. Shared reducer isolation and
exactly-once discussion generation/publication remain separate replay coherence
work: a stale in-flight external effect can occur before a fenced SQL write is
rejected, and Redis publication is not transactionally coupled to SQL ownership.

## Local transaction verification

`backend/tests/test_replay_ownership.py` accepts `TEST_REPLAY_POSTGRES_URL` for
an explicitly selected localhost PostgreSQL database. It creates and drops a
unique schema per test. Without that variable the PostgreSQL tests skip; the
ordinary coordinator and lifespan tests still run. Use a disposable database,
never a production tunnel. Tests observe `pg_blocking_pids` to verify real row
lock contention, exercise expiry and replacement fencing, force paired-update
rollback with a SQL constraint, and run the actual deferred lifespan sweep.
