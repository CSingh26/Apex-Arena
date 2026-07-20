<div align="center">

# Apex Arena

### Formula racing, interpreted live.

**A public 2026-season fan experience where five specialist AI agents turn race data into an evidence-linked conversation.**

[![Version](https://img.shields.io/badge/version-1.0.0-ff5d78?style=flat-square)](https://github.com/CSingh26/Apex-Arena/releases/tag/v1.0.0)
[![Verify and publish](https://github.com/CSingh26/Apex-Arena/actions/workflows/release.yml/badge.svg)](https://github.com/CSingh26/Apex-Arena/actions/workflows/release.yml)
[![Backend image](https://img.shields.io/badge/GHCR-backend-51e5d4?style=flat-square&logo=docker)](https://github.com/CSingh26/Apex-Arena/pkgs/container/apex-arena-backend)
[![Frontend image](https://img.shields.io/badge/GHCR-frontend-6d8cff?style=flat-square&logo=docker)](https://github.com/CSingh26/Apex-Arena/pkgs/container/apex-arena-frontend)
[![License](https://img.shields.io/badge/license-AGPL--3.0--only-a779ff?style=flat-square)](LICENSE)

</div>

![Apex Arena landing experience](docs/images/apex-arena-home.jpg)

## The race is more than a timing screen

Apex Arena follows Formula racing as a living argument. It unifies timing, telemetry,
race-control, strategy, and session data into a single ordered story, then gives five defined
specialists room to interpret it. They make calls, challenge assumptions, revise positions, and
show the evidence behind every grounded claim.

The result feels closer to a great post-race debrief unfolding in real time than a conventional
dashboard.

## What makes Apex Arena different

- **Five analytical perspectives** — strategy, telemetry, racecraft, history, and moderation.
- **Opinionated but accountable debate** — agents take positions while separating facts from
  inference and uncertainty.
- **Evidence on demand** — every supported message can expose its trigger, source metrics,
  confidence, and data-quality notes.
- **Live and replay rooms** — follow an active session or revisit an archived weekend through the
  same conversation model.
- **Session-aware 2026 calendar** — qualifying, sprint qualifying, sprint, and race rooms are
  grouped into complete Grand Prix weekends.
- **Accurate circuit artwork** — 2026 layouts are sourced from Jules Roy's
  [f1-circuits-svg](https://github.com/julesr0y/f1-circuits-svg) archive.
- **Circuit intelligence** — every 2026 venue carries records, historical context, and memorable
  facts from the official Formula 1 circuit guides.
- **Live track conditions** — OpenF1 session weather brings air and track temperature, rainfall,
  humidity, pressure, wind speed, and wind direction into the room.
- **Transparent data quality** — unavailable or partial provider data is labelled; Apex Arena does
  not invent telemetry.
- **Dark and light themes** — a vibrant, responsive interface designed for desktop and mobile.

## The 2026 season, session by session

![Apex Arena Race Rooms catalog](docs/images/apex-arena-race-rooms.jpg)

The Race Rooms catalog turns the season into an editorial archive. A live-session countdown leads
into the current weekend, completed events open into evidence-linked replays, and future sessions
remain visible without pretending provider data already exists.

Visitors can filter by:

- Grand Prix, circuit, or country
- live, completed, or upcoming weekends
- qualifying, sprint qualifying, sprint, or race
- standard and sprint-weekend formats

## Inside a Race Room

![Apex Arena circuit records and OpenF1 weather](docs/images/apex-arena-room-intelligence.jpg)

Each room brings together four connected surfaces:

1. **Session conversation** — a scrollable, reply-aware debate between the agents.
2. **Conversation map** — key moments arranged across the session timeline.
3. **Track dossier** — circuit length, first Grand Prix, race lap record, and venue facts.
4. **OpenF1 conditions** — the latest session weather sample, with a clear provider status.

The circuit and weather panels are deliberately resilient. Historical and live sessions show the
latest OpenF1 sample when it is available. Future sessions retain the panel and explain when the
provider has not published data. A weather-provider interruption never takes the race room offline.

## Meet the room

| Agent | Lens | What they bring to the debate |
| --- | --- | --- |
| **Mira Vale** | Race strategy | Pit windows, tyre life, traffic, undercuts, and strategic trade-offs |
| **Theo Voss** | Telemetry | Lap deltas, sector trends, consistency, and measured pace claims |
| **Lena Cross** | Racecraft | Overtakes, defensive driving, incidents, and track-position battles |
| **Arjun Reyes** | Championship history | Season form, circuit precedent, records, and historical context |
| **Nova** | Room host | Moderation, evidence quality, uncertainty, and room verdicts |

Agents have explicit specialties, speaking styles, supported topics, confidence rules, and evidence
standards. Replies are first-class messages: agreement, disagreement, correction, questions, and
summaries remain visible as relationships rather than a flat comment feed.

## Circuit intelligence and weather

Apex Arena V1 ships a verified dossier for every circuit in its 22-event 2026 catalog. Circuit
profiles include:

- circuit length
- first Formula 1 Grand Prix
- current race lap record and holder
- two venue-specific facts
- a link to the official Formula 1 circuit guide

Weather is fetched through the OpenF1 `/v1/weather` endpoint using the room's session key. The
backend selects the latest sample and safely normalizes:

| Measurement | Display |
| --- | --- |
| Air temperature | °C |
| Track temperature | °C |
| Rainfall | detected / none |
| Humidity | percent |
| Atmospheric pressure | mbar |
| Wind speed | m/s |
| Wind direction | compass point and degrees |

Provider failures, empty responses, malformed samples, and sessions without a provider key all have
tested fallback states.

## Data architecture

```mermaid
flowchart LR
    A[OpenF1 REST + live feed] --> D[Ingestion and normalization]
    B[Jolpica season calendar] --> C[Session catalog]
    C --> D
    D --> E[(PostgreSQL event archive)]
    D --> F[(Redis event bus)]
    E --> G[Race state + replay engine]
    F --> G
    G --> H[Discussion engine]
    H --> I[Evidence-linked Race Room API]
    I --> J[Next.js fan experience]
```

### Backend

- FastAPI and Pydantic contracts
- asynchronous SQLAlchemy with PostgreSQL
- Redis-backed event streaming
- OpenF1 REST and MQTT provider clients
- Jolpica season-calendar synchronization
- deterministic normalization, ordering, and deduplication
- race-state snapshots and replay coordination
- evidence-linked multi-agent discussion engine

### Frontend

- Next.js App Router
- React and TypeScript
- accessible, theme-aware component system
- live Server-Sent Events room updates
- grouped session catalog and countdown experience
- replay controls, message filters, evidence drawer, and conversation map
- official 2026 circuit artwork with responsive rendering

## Data integrity principles

Apex Arena is designed around a simple rule: **the interface must never sound more certain than the
data**.

- Raw provider events are preserved before normalization.
- Normalized events have stable ordering and deduplication keys.
- Messages carry confidence and evidence-availability states.
- Partial telemetry narrows agent conclusions.
- Results-only rooms do not manufacture lap-by-lap analysis.
- Development fixtures are visibly labelled and never presented as real championship data.
- Provider outages degrade individual panels rather than the entire experience.

## Public API surface

The V1 API is organized around stable public resources:

| Area | Responsibility |
| --- | --- |
| Health | application and dependency readiness |
| Calendar | authoritative 2026 season summary |
| Event weekends | grouped Grand Prix and session catalog |
| Race rooms | room metadata, circuit dossier, weather, and playback state |
| Messages | filtered conversation and pagination |
| Evidence | source metrics, trigger events, and data-quality flags |
| Streaming | live room messages and session events over SSE |
| Replay | start, pause, resume, speed, lap, phase, and sequence control |

Interactive API documentation is exposed by the running backend through FastAPI's OpenAPI surface.

## V1 release pipeline

Version `1.0.0` introduces a release gate that keeps unverified images out of GitHub Container
Registry.

Every pull request, `main` update, and version tag must pass:

- backend formatting and lint checks
- the complete backend test suite
- frontend linting and TypeScript checks
- the complete frontend component test suite
- a production Next.js build
- production backend and frontend Docker builds
- critical-vulnerability scans for both images

Only verified builds can publish images. Version tags publish multi-platform release images:

- `ghcr.io/csingh26/apex-arena-backend`
- `ghcr.io/csingh26/apex-arena-frontend`

Published release images receive semantic-version, major/minor, major, `latest`, and commit-SHA
tags. The same verified publish matrix also publishes the Railway-ready multi-platform backend
image after the full `main` workflow succeeds:

- `ghcr.io/csingh26/apex-arena-backend:main`
- `ghcr.io/csingh26/apex-arena-backend:<full-commit-sha>`

Railway should follow `:main` for simple continuous deployment and keep the SHA tags available
for exact rollbacks.

## Deployment

Apex Arena runs in production at **https://chaitanyasingh.org/apex-arena**.

The public domain belongs to the portfolio project, which rewrites `/apex-arena/*` to the
Apex Arena frontend on Vercel. The frontend proxies API calls server-side to the FastAPI
backend on Railway, which reads from Neon PostgreSQL and Upstash Redis. No infrastructure
hostname is ever exposed to the browser.

`main` is the canonical deployment source branch. The backend supports three process roles:

- **`api`** — serves HTTP and SSE only.
- **`ingestor`** — owns OpenF1 worker duties behind a singleton advisory lease.
- **`combined`** — serves the API and runs narrowly scoped worker duties in one process.

For the next deployment pass, Apex Arena is prepared around one `combined` backend service from
`main`, with `OPENF1_INGESTION_MODE=rest`, live MQTT off, and recent-session reconciliation on.
Full-season historical backfill remains manual-only; automatic recovery is limited to recently
completed competitive sessions after OpenF1 publishes real data.

### Deployment documentation

- [Low-cost deployment audit](docs/low-cost-deployment-audit.md)
- [Low-cost production architecture](docs/low-cost-production-architecture.md)
- [Railway deployment runbook (backend)](docs/railway-deployment-runbook.md)
- [Neon PostgreSQL setup](docs/neon-setup.md)
- [Upstash Redis setup](docs/upstash-setup.md)
- [Apex Arena frontend on Vercel](docs/apex-arena-vercel-deployment.md)
- [Portfolio ↔ Apex Arena integration](docs/portfolio-vercel-integration.md)
- [Deployment secrets and environment variables](docs/deployment-secrets.md)
- [Deployment cost controls](docs/deployment-cost-controls.md)
- [Deployment rollback runbook](docs/deployment-rollback-runbook.md)

### Local development URL

The mount point is controlled by a single variable, `NEXT_PUBLIC_APP_BASE_PATH`, from which
`next.config.ts` derives the Next.js `basePath`. This changes where the development server
serves the application:

| `NEXT_PUBLIC_APP_BASE_PATH` | Local URL | Notes |
| --- | --- | --- |
| unset (or empty) | `http://localhost:3000` | The app is served at the origin root. Convenient for day-to-day local work |
| `/apex-arena` | `http://localhost:3000/apex-arena` | Mirrors production. `http://localhost:3000` itself returns 404 — this is correct, not a broken build |

Production **always** uses the prefixed form, because Apex Arena is mounted beneath the
portfolio domain rather than served from its own root. If you are testing anything that
depends on URL construction — canonical tags, Open Graph metadata, share links, the API
prefix, or the static asset path — run locally with `NEXT_PUBLIC_APP_BASE_PATH=/apex-arena`
so the local URLs match production. Note that the browser-facing API prefix follows the
base path: it defaults to `<base path>/api`, and can be overridden with
`NEXT_PUBLIC_API_BASE_PATH`.

## Project documentation

- [Day 3: Race Rooms and evidence architecture](docs/day-3-race-rooms.md)
- [Live race operations and failure states](docs/live-race-operations.md)
- [Agent conversation experience](docs/arena-chat-experience.md)

## Attribution

- Timing, telemetry, session, and weather data are provided by
  [OpenF1](https://openf1.org/).
- Season calendar metadata is sourced through [Jolpica F1](https://jolpi.ca/).
- Circuit artwork is adapted from
  [julesr0y/f1-circuits-svg](https://github.com/julesr0y/f1-circuits-svg), licensed
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Circuit records and venue facts link to the corresponding official
  [Formula 1](https://www.formula1.com/) circuit guides.

## License and disclaimer

Apex Arena source code is licensed under
[GNU Affero General Public License v3.0 only](LICENSE). See [NOTICE](NOTICE) and
[COPYRIGHT](COPYRIGHT) for attribution details.

Apex Arena is an independent, unofficial fan project. It is not affiliated with, endorsed by, or
sponsored by Formula 1, the FIA, Formula One Management, any team, circuit, broadcaster, or data
provider. Formula 1, F1, Grand Prix names, team names, circuit names, and related marks belong to
their respective owners.
