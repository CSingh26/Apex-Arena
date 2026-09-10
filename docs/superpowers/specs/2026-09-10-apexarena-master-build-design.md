# ApexArena Master Build Program Design

**Date:** 2026-09-10

**Branch:** `sprints`

**Remote:** `origin/sprints`
**Source requirements:** User-supplied “ApexArena — Master Completion Sprint” brief

## Purpose

Complete ApexArena as a production-oriented Formula racing intelligence platform without replacing working systems or disguising unavailable provider data. The work proceeds from the repository’s current state, including the verified but uncommitted Live Rooms repair, and preserves one normalized event path for live and historical sessions.

## Verified Starting Point

The current working tree passes 528 backend tests with one skip, 129 frontend tests, Ruff lint, ESLint, TypeScript, and a production Next.js build. OpenF1 and Jolpica provider probes succeed locally. PostgreSQL, Redis, normalized events, deterministic battle intelligence, session rooms, replay, GPS storage, standings, SSE, and responsive Race Room surfaces already exist.

The audit also found release-blocking gaps:

- the repaired REST live worker and current schema are not deployed;
- shared replay controls are unauthenticated and replay state is not restart-safe;
- replay-facing UI surfaces can disagree about the active cursor;
- predictive strategy intelligence, bounded conversational memory, and a real optional LLM path are absent;
- historical GPS recovery and telemetry comparison are incomplete;
- provider and event error states are inconsistent in several frontend flows;
- CI still targets a removed development fixture and an optional release job is deliberately unreachable;
- production proxy protection fails open when its token is absent;
- retention, rate limiting, structured observability, dependency locking, and sustained-load coverage remain incomplete;
- the current frontend dependency graph contains known high/critical audit findings.

## Architectural Direction

Retain and strengthen the existing provider-neutral pipeline:

```text
OpenF1 / Jolpica
    -> provider adapters and provenance
    -> raw durable facts
    -> normalized ordered events
    -> deterministic state and intelligence
    -> persisted snapshots / Redis streams
    -> stable API and SSE contracts
    -> Fan and Analyst experiences
    -> optional grounded LLM conversation
```

Live ingestion and historical reconstruction use the same normalized facts, reducers, event importance, battle detection, strategy analysis, and grounding contracts. Provider-specific shapes stop at adapters. AI explains supported facts and deterministic conclusions; it never becomes timing, classification, battle, or strategy authority.

## Program Decomposition

The master build is too broad for one implementation plan. It is divided into independently testable subprograms executed in dependency order:

1. **Working-tree reconciliation and foundation** — review, format, partition, commit, and verify the existing Live Rooms repair.
2. **Security and release safety** — fail-closed proxy protection, replay authorization/rate limits, dependency remediation, CI seed repair, and release-path correction.
3. **Authoritative session lifecycle** — current-weekend resolution, dynamic sprint/standard schedules, delayed/red-flag states, hydration, restart recovery, and stale-state reconciliation.
4. **Provider resilience and provenance** — typed provider boundaries, bounded retries, rate limits, stale-while-revalidate policies, schema drift handling, and explicit freshness/quality metadata.
5. **Replay coherence** — one replay cursor, restart-safe workers, bounded rebuilds, independent shared state, and honest missing-data behavior.
6. **Strategy and tyre intelligence** — deterministic stint, degradation, pit-loss, undercut, overcut, divergence, traffic, weather, and uncertainty models.
7. **Agent intelligence** — importance gating, compact context, bounded memory, grounded optional LLM generation, deterministic fallback, budgets, and failure isolation.
8. **Telemetry and GPS experience** — useful comparisons, historical location recovery, interpolation/reconnect behavior, and capability-driven fallbacks.
9. **Fan and Analyst product modes** — distinct information hierarchy, progressive disclosure, natural timing formats, battle/strategy stories, and responsive behavior.
10. **Mobile/API foundation** — stable versioned contracts, client-safe event envelopes, authentication/session semantics, and native-client documentation.
11. **Persistence, performance, and observability** — retention, archive policies, cache-specific TTLs, structured logs, dependency health, and measured load targets.
12. **Adversarial QA and stabilization** — deterministic live-feed scenarios, E2E repair, Docker/Compose verification, provider/AI outage tests, accessibility, final audits, and documentation.

Each subprogram receives its own implementation plan and produces working, testable software. Dependencies flow forward; no later product polish can substitute for an unresolved foundation or security failure.

## Data and State Ownership

- PostgreSQL owns durable raw facts, normalized events, room metadata, replay cursor state, bounded summaries, and replay indexes.
- Redis owns short-lived distribution, current live snapshots, leases, and bounded stream catch-up.
- Provider adapters own authentication, throttling, retries, raw-schema validation, and provenance.
- The session engine owns weekend/session existence and lifecycle.
- The race-state reducer owns authoritative timing-derived session state.
- Intelligence engines consume normalized facts and bounded prior state, emitting typed derived events with confidence and evidence.
- Frontend clients consume stable public contracts and never infer provider-specific lifecycle rules.

## Error Handling

All external failures produce explicit, scoped states. Cached valid data may be served with freshness labels. Missing capability is not an error and never becomes fabricated data. Core timing, telemetry, standings, replay, and deterministic explanations remain usable when the optional AI provider is disabled or unavailable.

Security-sensitive mutating endpoints fail closed. Retries are bounded and observable. Background runs reconcile interrupted records on startup. Replay and stream recovery are idempotent and preserve monotonic sequence/cursor rules.

## Testing Strategy

Every production behavior follows red-green-refactor. Focused tests verify each commit. Phase gates run the full applicable backend/frontend suite plus lint, typing, builds, migrations, Docker, integration, replay simulation, provider-failure simulation, and E2E checks.

Deterministic scenarios cover normal races, sprint weekends, safety cars, red flags, rain, retirements, pit cycles, simultaneous battles, provider outages, reconnects, stale Redis, missing GPS, large replays, mobile layouts, and AI failure. Performance work begins with measurement and records explicit thresholds.

## Multi-Agent Review Model

The lead agent owns architecture, sequencing, integration, and final release judgment. Specialist agents receive non-overlapping tasks. No two implementers edit the same boundary concurrently. Every implementation task is reviewed by a different agent for both requirement compliance and code quality. Critical and important findings are fixed and re-reviewed before dependent work proceeds.

## Git and Push Protocol

- Work remains on `sprints` and targets `origin/sprints`.
- Produce at least 150 substantive, atomic commits on 2026-09-10 in the configured America/Phoenix timezone.
- Perform one distinct successful `git push origin sprints` after every commit; failed attempts do not count.
- Do not create empty commits, filler edits, reversals made only to increase counts, merge bubbles for counting, or artificial file churn.
- Do not rewrite history or force push.
- Each commit contains one independently explainable change and its focused verification evidence.
- Maintain an ignored execution ledger recording ordinal, commit SHA, subject, tests, push result, and phase.
- Before each commit, verify the relevant focused tests. Before each phase closes, run its complete gate.
- If 150 legitimate atomic changes cannot be justified without harming the product, stop and report the shortfall rather than corrupting history.

## Completion Standard

Completion requires evidence for every acceptance criterion in the source brief, a final independent architecture/security review, all applicable build and test gates, current documentation, an exact commit/push ledger, transparent limitations, and an explicit GO or NO-GO recommendation. Passing compilation alone is never sufficient.
