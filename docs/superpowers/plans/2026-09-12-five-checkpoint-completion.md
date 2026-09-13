# ApexArena Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement reviewed work units within each checkpoint. Do not commit individual work units.

**Goal:** Complete the remaining master product requirements without changing the already-pushed foundation commit, using no more than three additional substantive, verified commits and separate pushes.

**Architecture:** Preserve the normalized factual event pipeline and durable replay ownership. Add bounded deterministic race intelligence before optional language generation; expose the same evidence-aware contracts to web and future native clients. Extend existing working components instead of building parallel pipelines.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy/PostgreSQL, Redis/SSE, Next.js/React/TypeScript, pytest, Vitest and Playwright.

**Spec:** `docs/superpowers/specs/2026-09-10-apexarena-master-build-design.md`, original 67-section master brief, and the user's September 12 replacement instruction.

## Global constraints and accounting

- The old 150-commit/push and five-push targets are superseded. Start after `975380967a5321900761f3002dd4b78061c69ef8`; foundation checkpoint 1 is committed and separately pushed as `066a36edb7d9b62686c48f5d1bde3275b5cbad12`. Do not amend, rewrite or force-push it.
- The user's September13 packaging direction permits **two or three more pushes**. Use three because the remaining work naturally separates into: (2) deterministic race intelligence; (3) grounded conversation plus integrated product surfaces; (4) final adversarial acceptance and release stabilization. The old checkpoint3 and checkpoint4 sections below are implementation work units inside new delivery checkpoint3, not separate commits or pushes.
- Use the existing `sprints` checkout and push each completed checkpoint separately to `origin/sprints`. No force pushes, history rewriting, empty commits, or extra plan-only commits.
- Each checkpoint can contain several independently tested/reviewed work units. Only the integration owner commits after the complete checkpoint passes its gates.
- Preserve public reads, independent operator authorization, lease fencing, source provenance and replay ordering. Never read real environment secrets or test against user databases.
- Production deployment, main promotion, credential rotation, production migrations and paid provider usage are separate authorization boundaries. Code completion is not production deployment approval.
- Unknown provider facts remain unknown. Estimates include evidence and uncertainty; optional AI cannot mutate facts or block factual ingestion.
- Maintain one implementation owner per source copy. Independent units may proceed in isolated source copies; the integration owner merges their reviewed changes and verifies the combined candidate before each delivery commit. Tests precede behavior changes. Review findings are corrected inside the current checkpoint.

## Remaining-work audit at the starting revision

Working foundations include session discovery, normalized persistence, live timing, basic battle detection, durable replay ownership/recovery, GPS storage, championship caching and responsive room navigation. Prior verification recorded 699 backend tests, 211 frontend tests and 13 browser scenarios; those totals do not establish missing-feature coverage.

Five reproduced integration defects remain: concurrent intake can skip reduced facts; worker restart loses intelligence history; immutable GPS windows discard samples while retaining cache hits; replay restart fails other viewers' discussion cursors; late pagination can contaminate another room or generation.

Missing or partial master scope includes strategy/tyre histories and estimates, lifecycle/control semantics, weather trends, telemetry history/comparison, agent claim memory and optional generation, Fan race narrative, driver/team context, capability/provenance consistency, native contracts, retention/rate limiting/structured logging and broad adversarial acceptance. Dependency audit also requires remediation and fresh verification. Detailed source-backed audit artifacts are retained in the local SDD workspace; this committed plan preserves the actionable scope.

## Checkpoint 1 — Reliable ordered ingestion, recovery and bounded infrastructure

**Existing boundaries:** `backend/app/services/event_pipeline.py`, `race_intelligence.py`, `live_ingestion.py`, `backend/app/api/room_streaming.py`, `backend/app/services/room_replay.py`, room repository/models, `frontend/src/lib/use-driver-locations.ts`, `frontend/src/components/race-rooms/room-experience.tsx`, provider clients, core logging/settings, dependency manifests and locks.

- [x] Reproduce and repair intake ordering across ingest/batch/flush; serialize source persistence, reduction and derived draining per session without blocking unrelated sessions.
- [x] Reconstruct intelligence from durable source history before new live intake, without historical external side effects; prove uninterrupted/restarted battle, overtake and qualifying parity.
- [x] Make retained GPS samples agree with cached window coverage; test dense non-aligned forward ticks and backward/forward seeks.
- [x] Introduce a durable discussion-generation contract across restart, HTTP backlog and SSE reconnect; test two viewers and missed reset recovery.
- [x] Guard pagination/bootstrap/reset completions by room and generation, including transports that ignore abort.
- [x] Bound provider retries and Retry-After, classify schema failures, coalesce bounded metadata caches with explicit stale fallback.
- [x] Implement redacted JSON logging and trusted-boundary request/concurrency limits. Document persistent replay/source retention and bounded transient caches; do not introduce destructive pruning without a verified archive/restore contract.
- [x] Resolve dependency advisories using compatible reviewed versions and reproducible installs. Removed the old TestClient/httpx and CommonJS-tooling warnings; one new upstream Starlette/AnyIO alias warning remains visible, not suppressed.
- [x] Run focused RED/GREEN regressions, full unit/integration gates, dependency audit and independent checkpoint review. Final development evidence: 819 backend tests (all opt-ins, no skips; one upstream warning), 240 frontend tests, full lint/typecheck/format checks, production-image build/migration/startup and 13 browser scenarios. Package audits report no known vulnerabilities. This is local synthetic evidence, not hosted deployment or a final master security certification.

## Checkpoint 2 — Deterministic race and strategy intelligence

**Existing boundaries:** domain models/intelligence, normalization, race state/coordinator/rebuild, snapshots and API schemas. New focused modules should own factual history, strategy estimates and weather/control transitions rather than enlarge the coordinator indiscriminately.

### Implementation order and recovery decisions

1. Establish atomic critical-projection progress before adding derived strategy families: source facts remain durable first; derived rows, resolved summaries, coherent snapshot and completion watermark commit as one guarded unit. Retry pending work before subsequent intake, including periodic recovery of a final failed source. Optional notifications and generation remain outside this guarantee.
2. Existing history without progress markers is explicitly `historical_effects_unverified`, reconstructed without external effects. Do not silently certify or rewrite legacy history. Algorithm/config changes require explicit handling. A guarded offline rebuild can repair historical projections, but is never invoked automatically by live recovery.
3. Apply common writer exclusion to live, debug, backfill and offline rebuild. Maintenance refuses an active conflicting writer. Whole rebuild replacement must be transactional or safely refused; runtime recovery never renumbers source history.
4. Stored append order is authoritative for live/restart/replay. Align deterministic rebuild with that order and test late facts explicitly; do not claim parity between different orderings. Bound reconstruction bookkeeping separately from domain-history bounds.
5. Add independent typed history/strategy modules with evidence-addressable corrections, conservative estimates, explicit missing capabilities and bounded collections; integrate them into the reviewed projection seam only after recovery passes review.
6. Add lifecycle/control/weather and battle context, then verify source-to-API/replay parity, independent review and full checkpoint gates before commit 2.

Detailed retained history stays private to the deterministic projection, not in every hot timing/SSE state payload. Publish compact summaries and a revision reference; persist/reuse immutable detail checkpoints atomically with critical snapshots/progress and serve exact cursor-bounded details through a bounded reader. Preserve detached public isolation. Measure relevant-event cost and full-prefix restart separately; a compact payload is not proof of constant-time recovery.

These are development-only decisions. No production data rewrite, external deployment, historic chat regeneration or end-to-end exactly-once guarantee is authorized or claimed.

### Approved integration policies (verification still pending)

- Operational capture uses a configurable 12-hour window anchored to a separately persisted initial schedule. Later display-schedule corrections do not extend the window. Expiry is not sporting finish; compatible committed finish evidence remains valid after capture expires.
- Cancellation is sticky for a session identity. Missing, false or older catalog metadata cannot automatically reopen it. A mistaken provider cancellation requires an explicitly reviewed correction workflow; cancellation is not relabeled as finish.
- Detailed history uses immutable base checkpoint B, last relevant source H and consumed public cursor C. Checkpoint on a relevant input after 64 relevant inputs or a 2,048-sequence gap; unrelated telemetry does not serialize history. Exact selected reads are bounded and may report unavailable, never substitute latest history for an older cursor. Validate actual storage amplification and responsiveness before acceptance.
- Analysis time is the maximum accepted source-fact timestamp through C, separate from the playback display clock. Derived rows, wall time and future session data cannot advance historical analysis. Provider lap-start timing remains explicitly approximate.
- Coordinate a semantic-manifest `race-v2` identity across persistence, readers and writers. Older non-empty projections remain factually readable but explicitly incompatible/unverified; no automatic rewrite or adoption is authorized. Refuse incompatible intake before provider work and document the old-runtime or future migration rollout choice.
- Post-terminal results settlement is a separate, newly explicit opt-in, disabled by default. When enabled, use the existing live owner and processor with a fixed six-hour / 72-attempt window, five-minute cadence, one concurrent request, and bounded response sizes. Later corrections append new facts; they neither reopen high-frequency capture nor rewrite old replay facts. This is a finite acquisition policy, not proof that results can never change afterward.

- [ ] Add typed capability/provenance envelopes and authoritative lifecycle transitions, including delayed, neutralized, suspended, resumed and terminal states.
- [ ] Normalize sector/control semantics and preserve unknown DRS/geometry facts; distinguish deployment from end messages.
- [ ] Exclude active/unconfirmed capture from historical-finalization shortcuts and add bounded, explicitly enabled terminal-result settlement through the existing live owner, preserving later corrections as new cursor-bound facts.
- [ ] Maintain bounded evidence-addressable lap/stint/weather histories, corrections, tyre starting age and replay-safe snapshots/reconstruction.
- [ ] Compute representative pace with pit/neutralized/deleted/outlier exclusions, tyre degradation uncertainty and observed pit-loss baselines.
- [ ] Derive traffic/rejoin, tyre offset, undercut/overcut, pit windows, neutralization opportunities and strategy divergence only where evidence supports estimates.
- [ ] Enrich battle scoring with observed attempts, pace/tyre/team/championship/final-lap context; do not turn proximity into reported DRS usage.
- [ ] Integrate all derived families into one deterministic rebuild and replay contract; test live/restart/rebuild/seek parity and missing evidence.
- [ ] Independently review, verify and make checkpoint commit/push 2.

## Delivery checkpoint 3, work unit A — Grounded conversations and optional generation

**Existing boundaries:** discussion service, room message/evidence storage, agent profiles and settings. New focused modules own structured claims and generation policy.

- [ ] Persist bounded claims, evidence, confidence, predictions, outcomes and revisions scoped to room and discussion generation.
- [ ] Build deterministic agent reasoning from checkpoint-2 facts; enforce cursor-bounded recall and no future leakage after seek/restart.
- [ ] Add an optional provider adapter downstream of factual ingestion with validated output, timeout/cancellation, concurrency, cache, token/cost budgets and kill switch.
  - Newly functional paid generation requires a separate explicit opt-in, disabled by default. Existing placeholder `ai_enabled` settings or a preexisting API key must not silently activate paid calls after upgrade. Development verification uses fake providers only.
- [ ] Keep deterministic fallback explicit and available for missing credentials, unsupported claims, provider failures and exhausted budgets.
- [ ] Test fake-provider success/failure and claim contradiction/revision behavior without real paid calls; expose actual component health.
- [ ] Independently review and verify this work unit; do not commit or push until the checkpoint4 product-surface work unit is also complete.

## Delivery checkpoint 3, work unit B — Integrated Fan, Analyst, telemetry and native contracts

**Existing boundaries:** room experience, command center, timing/map components, API client/types, location storage and session routes.

- [ ] Add bounded historical car telemetry and aligned driver/lap comparison with capability-specific completeness and query limits.
- [ ] Deliver Fan summaries of what happened, why it matters and what to watch, using the shared deterministic evidence model.
- [ ] Deliver Analyst strategy/stint/pace/sector/telemetry charts including RPM and clear units, uncertainty and missing-data states.
- [ ] Join live driver/team context with independently fresh season context; use canonical timing formatting throughout.
- [ ] Add only supported map layers and reduced-motion-aware animation; preserve selection, keyboard navigation and bounded interpolation.
- [ ] Surface recoverable pagination/provider errors and freshness consistently; verify new screens at existing six viewport widths.
- [ ] Publish versioned native bootstrap/SSE/auth/error/deprecation guidance and contract tests. No separate native app is required.
- [ ] Independently review, run the combined conversation/product gates, then make one delivery-checkpoint3 commit and separate push.

## Delivery checkpoint 4 — Master acceptance and release stabilization

- [ ] Extend isolated synthetic fixtures into advancing live and outage scenarios without production mock fallbacks.
- [ ] Exercise normal/sprint weekends, weather transition, pits/strategy, SC/VSC end, red flag/resume, incidents/penalties, missing capabilities, schema drift, provider outage, AI timeout/budget/kill, restart/rebuild, reconnect, seek and bounded large replay across backend and browser paths.
- [ ] Run measured latency/memory/query/load checks, accessibility/keyboard/contrast/reduced-motion checks and a fresh visual product review. Correct regressions, not just document them.
- [ ] Run final independent architecture/security review, dependency audits, clean-install builds, migrations, full tests and isolated browser acceptance at the actual final candidate.
- [ ] Update README, setup, provider, engine, replay, AI, native, retention and deployment documentation to implemented behavior; remove stale claims/dead configuration.
- [ ] Record exact evidence, remaining external rollout actions and final commit/push receipts. Commit and push delivery checkpoint4 only when its acceptance is satisfied.

## Completion rule

Three remaining pushes are a ceiling and packaging constraint, not evidence of completion. Do not label unchecked requirements complete, reuse old test results as fresh runs, or claim real-provider/hosted/production verification from synthetic local evidence. Final accounting is the untouched foundation push plus exactly three additional delivery pushes unless all remaining work genuinely fits in two without weakening review or acceptance.

## Original §54 adversarial acceptance matrix

These are the original twenty scenarios, not a replacement set. Each needs final-candidate evidence, expected degraded states where appropriate, and corrections for actual failures. Focused unit coverage alone does not establish a working browser flow.

| # | Scenario | Required observable result |
| --- | --- | --- |
| 1 | No current session | Honest upcoming/completed state; no fabricated live room. |
| 2 | Current qualifying | Correct phase/cutoff/timing and qualifying room context. |
| 3 | Sprint weekend | Correct sprint-session hierarchy without a normal-weekend assumption. |
| 4 | Normal race start | Automatic population and advancing timing/factual events. |
| 5 | Safety car | Deployment/end state and evidence-aware strategy/reactions. |
| 6 | Red flag | Suspension/resumption without false completion. |
| 7 | Heavy rain | Observed weather change and uncertain tyre/strategy implications. |
| 8 | Retirement | Driver status changes without phantom overtakes/battles. |
| 9 | Provider disconnect | Bounded retry and explicit stale/degraded state. |
| 10 | User reconnect | Durable cursor catch-up and no duplicates or stale generation. |
| 11 | OpenF1 unavailable | Available capabilities remain usable; missing ones are labeled. |
| 12 | AI unavailable | Facts/timing remain responsive; deterministic fallback is explicit. |
| 13 | Historical GPS missing | Replay remains usable with an honest missing-GPS map state. |
| 14 | Large replay | Bounded memory/queries, measured responsiveness, coherent seek. |
| 15 | Simultaneous battles | Independent battle identity, histories and importance. |
| 16 | Rapid post-pit position changes | Pit-cycle context avoids false on-track pass claims. |
| 17 | Mobile viewport | Core Fan/Analyst navigation, charts and controls remain usable. |
| 18 | Slow connection | Loading/retry feedback, cancellation and no stale-room overwrite. |
| 19 | Mid-session application restart | Correct factual/intelligence recovery without duplicate effects. |
| 20 | Stale Redis state | Durable authority wins; freshness and cursor recovery are correct. |
