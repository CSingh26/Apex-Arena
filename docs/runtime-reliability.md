# Runtime reliability boundaries

## Provider metadata

Calendar requests coalesce through a bounded eight-year cache. Valid responses are fresh for
ten minutes; refresh failures may return explicitly stale data for at most 24 hours. A
30-second failed-request cooldown also applies to empty caches. Each provider fetch has a
20-second deadline. Calendar status is recalculated against the caller's current time,
not retained as a permanently live or upcoming cached status. Entries retain at most 100
races and two million serialized payload bytes. Jolpica pagination rejects invalid totals,
non-positive page sizes, totals above 10,000, and more than 100 pages.

Weather retains at most 64 small session projections, not historical response lists.
Successful results have a 30-second refresh interval; failures use a ten-second cooldown.
A prior usable projection may survive a failed refresh for up to five minutes, labelled
stale. The four-second total lookup deadline includes cache-lock wait. Fresh hits do not
wait behind unrelated lookups. Provider responses above 10,000 samples are rejected before
local projection. These limits do not bound response bytes already buffered upstream.

`source_checked_at` records the successful metadata fetch time, while weather `sampled_at`
records the provider observation time. A recently fetched historical measurement is not a
recent weather observation. `source_age_seconds` and `source_stale` describe the cached
projection, not a forecast or assurance that the provider is current. Refresh is
request-driven, with stale fallback on failure; there is no background refresh worker.

Provider retries retain their bounded attempt counts. Numeric `Retry-After` is capped at
five seconds; malformed, non-finite and HTTP-date values use the existing exponential
fallback. Cancellation propagates. This is not a universal deadline for every provider
operation or protection against unlimited concurrent work before API admission.

## Ordered ingestion and restart

Intake, batch intake and flush share a per-session critical section across source storage,
reduction and derived draining. Initialization reconstructs the durable source prefix before
new live intake. Reconstruction warms deterministic detector histories without republishing
historical Redis events, discussion, snapshots or summaries.

This is process-local serialization, not a distributed writer lease. The singleton live
ingestor remains required. A committed source whose later consumer fails still needs a
separate durable recovery contract; startup reconstruction does not backfill historical
missing derived events. Those constraints must not be represented as exactly-once delivery.

## Replay discussion generations

Migration `20260912_0018` introduces a durable room discussion generation. An owned restart
atomically advances it, resets playback and clears the old generated conversation/evidence.
New writes must name the expected generation. Playback and its generation are read in one
SQL snapshot. HTTP responses reject observed room/playback generation mismatch with 409;
clients refresh rather than blindly repeat a potentially committed mutation.

Message pages and SSE reconcile against PostgreSQL. Redis reset notifications are hints,
not the source of truth. SSE message IDs are `generation:sequence`; reconnecting viewers
can recover even if a reset notification was missed. Old numeric cursors remain parseable
but cannot encode the old generation. Drain old backend and frontend instances before
enabling restart during rollout. Do not run mixed versions against the new reset contract.

## Logging and admission

`LOG_FORMAT=json` emits bounded structured records with an explicit context allowlist.
Known credential labels, authorization values and URL user-info are redacted; raw exception
values/tracebacks are omitted. Pretty output escapes embedded line breaks. Existing root
handlers receive the same redaction filter; known Uvicorn handlers use the safe formatter.
Callers must still avoid raw headers, payloads and unlabelled secrets. Arbitrary handlers
registered later are not automatically instrumented.

See [rate limits](rate-limits.md) for aggregate admission and full-response stream leases.
See [cost controls](deployment-cost-controls.md) for persistent source/replay storage.
No source pruning, production migration, deployment or real credential rotation is performed
by these development checks.

## Reproducible verification

Backend runtime and development dependencies are pinned with hashes in `requirements.lock`
and `requirements-dev.lock`. Containers install the runtime lock; CI installs the development
lock with `--require-hashes`. The frontend uses `npm ci` and its committed lockfile.
Vitest configuration is ESM (`vitest.config.mts`). Do not run tests with real dotenv files:
use a clean export or disable Settings dotenv before importing the application and supply
only disposable datastore URLs. `env -i` alone does not disable dotenv discovery.

The current Starlette TestClient emits an upstream AnyIO deprecated-alias warning. It is
not suppressed and is distinct from application failures. Dependency audit results and
test counts belong to a specific checkpoint receipt, not an evergreen guarantee.
