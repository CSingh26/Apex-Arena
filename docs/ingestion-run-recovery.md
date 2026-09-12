# Historical ingestion-run recovery

Historical OpenF1 ingestion records a durable heartbeat while a run is active.
API, combined, legacy `all`, and dedicated ingestor processes perform one
startup sweep. A `running` `historical_rest` row whose latest heartbeat is more
than 30 minutes old becomes `failed` with `worker interrupted`, so the session
can be retried through the existing ingestion or backfill flow.

Recovery changes only the run's status, end time, and safe error reason. It
preserves inserted/duplicate counters, the last event timestamp, run metadata,
stored provider facts, and backfill checkpoints. Completed, partial, already
failed, fresh, and non-historical rows are not changed. This is an age-bounded
recovery signal, not proof that a process is alive or dead.

## First rollout

Legacy rows retain a null heartbeat, so their age falls back to `started_at`;
the migration does not invent evidence that an old worker is live. Drain or stop
legacy historical ingestion workers before starting the first new application
process. If a drained run began less than 30 minutes ago, wait until its
`started_at` crosses that threshold (or reconcile it explicitly) before relying
on the one-shot startup sweep. Starting the new version while a legacy worker is
still processing an older run can classify that healthy run as interrupted.

The startup sweep is intentionally one-shot. It does not start ingestion, call
OpenF1, reset provider data, or continuously monitor workers.
