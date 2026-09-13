# Sprint 1 session architecture

Apex Arena represents an F1 weekend as a schedule of normalized sessions. Provider names are mapped at the boundary to `PRACTICE_1`, `PRACTICE_2`, `PRACTICE_3`, `QUALIFYING`, `SPRINT_QUALIFYING`, `SPRINT`, and `RACE`.

The calendar comes from Jolpica; OpenF1 supplies matching meeting and session identifiers plus live and historical data. The matching service requires compatible schedule and meeting metadata, and leaves ambiguous matches unresolved rather than attaching the wrong data.

Race Rooms are created only by the explicit catalog synchronization process after a live session is confirmed or a completed session has provider data. Upcoming sessions remain schedule-only and return a clear `future_read_only` state.

The lightweight API surface is:

- `GET /api/v1/season/{season}/weekends`
- `GET /api/v1/weekends/{event_slug}`
- `GET /api/v1/weekends/{event_slug}/sessions`
- `GET /api/v1/sessions/{session_id}`
- `GET /api/v1/sessions/{session_id}/capabilities`
- `GET /api/v1/sessions/{session_id}/room`

Capability responses describe timing, telemetry, location, weather, race control, pit-stop, stint, and results availability. `unknown` is intentional when the provider has not supplied endpoint-level evidence. Heavy telemetry remains a historical-ingestion concern and is never fetched when listing a season calendar.

Redis continues to carry normalized session events and state. Both live OpenF1 messages and historical REST backfills run through the same normalized event pipeline, so room consumers do not need separate live and replay data models.
