# ApexArena Five-Checkpoint Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement reviewed work units within each checkpoint. Do not commit individual work units.

**Goal:** Complete the remaining master product requirements in five substantive, verified commits and five separate pushes.

**Architecture:** Preserve the normalized factual event pipeline and durable replay ownership. Add bounded deterministic race intelligence before optional language generation; expose the same evidence-aware contracts to web and future native clients. Extend existing working components instead of building parallel pipelines.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy/PostgreSQL, Redis/SSE, Next.js/React/TypeScript, pytest, Vitest and Playwright.

**Spec:** `docs/superpowers/specs/2026-09-10-apexarena-master-build-design.md`, original 67-section master brief, and the user's September 12 replacement instruction.

## Global constraints and accounting

- The old 150-commit/push target is superseded. Start after `975380967a5321900761f3002dd4b78061c69ef8`; new checkpoint count is 0/5.
- Use the existing `sprints` checkout and push each completed checkpoint separately to `origin/sprints`. No force pushes, history rewriting, empty commits, or extra plan-only commits.
- Each checkpoint can contain several independently tested/reviewed work units. Only the integration owner commits after the complete checkpoint passes its gates.
- Preserve public reads, independent operator authorization, lease fencing, source provenance and replay ordering. Never read real environment secrets or test against user databases.
- Production deployment, main promotion, credential rotation, production migrations and paid provider usage are separate authorization boundaries. Code completion is not production deployment approval.
- Unknown provider facts remain unknown. Estimates include evidence and uncertainty; optional AI cannot mutate facts or block factual ingestion.
- Maintain one implementation-agent owner at a time; independent read-only review can proceed concurrently. Tests precede behavior changes. Review findings are corrected inside the current checkpoint.

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

- [ ] Add typed capability/provenance envelopes and authoritative lifecycle transitions, including delayed, neutralized, suspended, resumed and terminal states.
- [ ] Normalize sector/control semantics and preserve unknown DRS/geometry facts; distinguish deployment from end messages.
- [ ] Maintain bounded evidence-addressable lap/stint/weather histories, corrections, tyre starting age and replay-safe snapshots/reconstruction.
- [ ] Compute representative pace with pit/neutralized/deleted/outlier exclusions, tyre degradation uncertainty and observed pit-loss baselines.
- [ ] Derive traffic/rejoin, tyre offset, undercut/overcut, pit windows, neutralization opportunities and strategy divergence only where evidence supports estimates.
- [ ] Enrich battle scoring with observed attempts, pace/tyre/team/championship/final-lap context; do not turn proximity into reported DRS usage.
- [ ] Integrate all derived families into one deterministic rebuild and replay contract; test live/restart/rebuild/seek parity and missing evidence.
- [ ] Independently review, verify and make checkpoint commit/push 2.

## Checkpoint 3 — Grounded conversations and optional generation

**Existing boundaries:** discussion service, room message/evidence storage, agent profiles and settings. New focused modules own structured claims and generation policy.

- [ ] Persist bounded claims, evidence, confidence, predictions, outcomes and revisions scoped to room and discussion generation.
- [ ] Build deterministic agent reasoning from checkpoint-2 facts; enforce cursor-bounded recall and no future leakage after seek/restart.
- [ ] Add an optional provider adapter downstream of factual ingestion with validated output, timeout/cancellation, concurrency, cache, token/cost budgets and kill switch.
- [ ] Keep deterministic fallback explicit and available for missing credentials, unsupported claims, provider failures and exhausted budgets.
- [ ] Test fake-provider success/failure and claim contradiction/revision behavior without real paid calls; expose actual component health.
- [ ] Independently review, verify and make checkpoint commit/push 3.

## Checkpoint 4 — Integrated Fan, Analyst, telemetry and native contracts

**Existing boundaries:** room experience, command center, timing/map components, API client/types, location storage and session routes.

- [ ] Add bounded historical car telemetry and aligned driver/lap comparison with capability-specific completeness and query limits.
- [ ] Deliver Fan summaries of what happened, why it matters and what to watch, using the shared deterministic evidence model.
- [ ] Deliver Analyst strategy/stint/pace/sector/telemetry charts including RPM and clear units, uncertainty and missing-data states.
- [ ] Join live driver/team context with independently fresh season context; use canonical timing formatting throughout.
- [ ] Add only supported map layers and reduced-motion-aware animation; preserve selection, keyboard navigation and bounded interpolation.
- [ ] Surface recoverable pagination/provider errors and freshness consistently; verify new screens at existing six viewport widths.
- [ ] Publish versioned native bootstrap/SSE/auth/error/deprecation guidance and contract tests. No separate native app is required.
- [ ] Independently review, verify and make checkpoint commit/push 4.

## Checkpoint 5 — Master acceptance and release stabilization

- [ ] Extend isolated synthetic fixtures into advancing live and outage scenarios without production mock fallbacks.
- [ ] Exercise normal/sprint weekends, weather transition, pits/strategy, SC/VSC end, red flag/resume, incidents/penalties, missing capabilities, schema drift, provider outage, AI timeout/budget/kill, restart/rebuild, reconnect, seek and bounded large replay across backend and browser paths.
- [ ] Run measured latency/memory/query/load checks, accessibility/keyboard/contrast/reduced-motion checks and a fresh visual product review. Correct regressions, not just document them.
- [ ] Run final independent architecture/security review, dependency audits, clean-install builds, migrations, full tests and isolated browser acceptance at the actual final candidate.
- [ ] Update README, setup, provider, engine, replay, AI, native, retention and deployment documentation to implemented behavior; remove stale claims/dead configuration.
- [ ] Record exact evidence, remaining external rollout actions and final commit/push receipts. Commit and push checkpoint 5 only when its acceptance is satisfied.

## Completion rule

Five pushes are a packaging constraint, not evidence of completion. Do not label unchecked requirements complete, reuse old test results as fresh runs, or claim real-provider/hosted/production verification from synthetic local evidence.

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
