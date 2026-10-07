# DataBull

**An industrial time-series telemetry platform built with FastAPI and TimescaleDB, with an AI operator's assistant powered by Claude.**

DataBull ingests sensor readings from industrial devices,  temperature, pressure, and flow,  and serves them back as raw series, bucketed rollups, or threshold breaches. Readings live in a TimescaleDB hypertable partitioned on time, so aggregate queries stay fast as history grows.

On top of the REST API sits an operator's assistant: a Claude tool-calling agent that answers natural-language questions about a device fleet ("has pump-3 drifted since Tuesday?") by calling the same telemetry layer the API exposes, and streams its reasoning, tool calls, and answer to the browser over Server-Sent Events.

Every device, reading, and conversation is scoped to the authenticated user. Cross-user access returns `404`, never `403`,  the API does not confirm that another tenant's resources exist.

---

## Table of Contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Authentication & Security Decisions](#authentication--security-decisions)
4. [Tech Stack](#tech-stack)
5. [Project Structure](#project-structure)
6. [Getting Started](#getting-started)
7. [Environment Variables](#environment-variables)
8. [Testing & CI](#testing--ci)
9. [Deployment](#deployment)
10. [API Reference](#api-reference)
11. [Roadmap](#roadmap)

---

## Features

- **Time-Series Ingestion**,  Single-reading and bulk endpoints (up to 10,000 rows per request) writing into a TimescaleDB hypertable partitioned on `time`.
- **Windowed Aggregation**,  `time_bucket` rollups at 1-hour, 1-day, or 1-week granularity with `avg`, `min`, `max`, or `p95` (via `percentile_cont`). Requests that would exceed 1,000 buckets are rejected at validation rather than served slowly.
- **Threshold Alerting**,  Per-device `min_threshold` / `max_threshold`. `GET /readings/alerts` returns every breach since a given timestamp, across the whole fleet or one device.
- **Device Management**,  Full CRUD over devices, each with a type (`temperature` / `pressure` / `flow`), a unit, and optional thresholds.
- **JWT Authentication**,  Stateless HS256 bearer tokens with argon2id password hashing. Login is timing-safe and does not reveal whether an email is registered.
- **AI Operator's Assistant**,  A Claude tool-calling agent over four typed tools, streamed to the client as SSE, with per-turn tool-call budgets, a wall-clock timeout, and token/cost accounting on every turn.
- **Generated API Types**,  The frontend's request and response types are generated from the backend's OpenAPI schema, so a contract change breaks the build instead of production.

---

## Architecture

### High-Level Overview

```mermaid
flowchart TB
    subgraph Client ["Frontend (React + TypeScript)"]
        SPA[React SPA]
        AC[AuthContext<br/>token in memory only]
        TQ[TanStack Query<br/>server-state cache]
    end

    subgraph Server ["Backend (FastAPI, async throughout)"]
        RT[Routers<br/>auth · devices · readings · chat]
        SV[Services<br/>all DB access lives here]
        AG[Agent module<br/>runner · tools · prompt]
    end

    subgraph Data ["PostgreSQL 16 + TimescaleDB"]
        US[(users)]
        DV[(devices)]
        RD[(readings<br/>hypertable on time)]
    end

    AN[Anthropic Messages API]

    SPA --> RT
    AC --- SPA
    TQ --- SPA
    RT --> SV
    RT --> AG
    AG --> SV
    AG --> AN
    SV --> US
    SV --> DV
    SV --> RD
```

The agent reaches the database only through the same `/services/` functions the routers use, behind a set of Protocols. It never imports FastAPI, and the data plane never imports the agent,  the two are separable by design.

### Request Flow,  Ingest & Aggregation

```mermaid
sequenceDiagram
    participant C as Client / seed script
    participant API as FastAPI router
    participant SVC as services/reading.py
    participant TS as TimescaleDB

    C->>API: POST /devices/{id}/readings/bulk
    Note over API: Bearer token → device ownership check
    API->>API: Validate payload (≤ 10,000 rows)
    API->>SVC: bulk_create(...)
    SVC->>TS: INSERT INTO readings
    TS-->>SVC: rows written
    SVC-->>API: {inserted: n}
    API-->>C: 201 Created

    C->>API: GET /readings/aggregate?window=1h&fn=p95
    API->>API: Reject windows exceeding 1,000 buckets
    API->>SVC: aggregate(...)
    SVC->>TS: SELECT time_bucket('1 hour', time),<br/>percentile_cont(0.95) ... GROUP BY 1
    TS-->>SVC: buckets
    SVC-->>API: [AggregateBucket]
    API-->>C: 200 OK
```

### Request Flow,  AI Assistant (SSE)

```mermaid
sequenceDiagram
    participant U as Operator
    participant API as POST /chat/stream
    participant R as run_agent
    participant LLM as Anthropic Messages API
    participant T as Tools → services → DB

    U->>API: {messages: [{role, content}, ...]}
    API->>R: AgentServices (repositories scoped to caller)

    loop until the model stops calling tools
        R->>LLM: transcript + 4 tool schemas<br/>(system prompt cached)
        LLM-->>R: text deltas
        R-->>U: data: {"type":"text","delta":"..."}
        LLM-->>R: tool_use blocks
        R-->>U: data: {"type":"tool_use","name":"..."}
        R->>T: execute_tool (ownership re-verified)
        T-->>R: payload + ≤200-char summary
        R-->>U: data: {"type":"tool_result","truncated":false}
        R->>LLM: all tool_results in ONE user message
    end

    R-->>U: data: {"type":"done","usage":{...}}
```

The whole turn is bounded by a single timeout, not one per call,  a model that keeps asking for one more tool cannot hold the connection open indefinitely. `run_agent` never raises for an LLM or tool failure: those become `error` events, and the stream always terminates with `done`.

### Authentication Flow

```mermaid
sequenceDiagram
    participant U as User
    participant FE as React SPA
    participant API as FastAPI
    participant DB as PostgreSQL

    U->>FE: Submits login form
    FE->>API: POST /auth/login {email, password}
    API->>DB: SELECT user WHERE email = ...
    DB-->>API: row, or none
    Note over API: An unknown email still runs a dummy argon2<br/>verify, so response timing does not leak<br/>whether the account exists
    API->>API: argon2id verify → sign HS256 JWT (24h)
    API-->>FE: {access_token, token_type: "bearer"}
    FE->>FE: Hold token in React context (memory only)

    FE->>API: Authorization: Bearer <token>
    API->>DB: get_current_user,  account looked up every request
    API-->>FE: 200, or 401 with a machine-readable code
```

---

## Authentication & Security Decisions

JWT bearer tokens, HS256, 24-hour expiry. Passwords are hashed with **argon2id** (via `pwdlib`) and must be at least 8 characters.

The API refuses to start without `SECRET_KEY`. That is deliberate: the value signs every access token, so a shipped placeholder would let anyone mint a token for any account. Use a distinct key per environment.

**Token in memory only.** The frontend keeps the token in React context, never in `localStorage` or `document.cookie`. Anything readable from JavaScript is exfiltratable by an XSS payload; a token held only in a closure is not. The cost is that a tab reload loses it and the user logs in again. A regression test asserts the token never reaches either store or the cookie jar.

**No refresh tokens in v1.** A 24-hour access token is the whole session. When it expires, the user logs in again.

**Stateless tokens cannot be revoked.** The server stores no record of issued tokens,  it re-verifies the signature on each request instead. A token therefore stays valid until it expires, even if the user logs out elsewhere. Deleting a user *does* lock them out immediately, because `get_current_user` looks the account up on every request. Revoking one outstanding token would require a denylist, deliberately out of scope for v1. Rotating `SECRET_KEY` invalidates every outstanding token at once.

**Login does not reveal whether an email is registered.** Wrong password and unknown account return identical 401s, and the unknown-account path still runs an argon2 comparison so response timing does not leak the difference either.

**Tenant isolation returns 404, not 403.** Requesting another user's device is indistinguishable from requesting one that does not exist.

**The client cannot inject a system turn.** `ChatMessageIn.role` accepts only `user` or `assistant`. A client-supplied `system` message would be a prompt-injection channel straight into the operator instructions which gets rejected at validation.

---

## Tech Stack

| Layer | Technology |
|:------|:-----------|
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| ORM | SQLAlchemy 2.0 (typed `Mapped[...]`), async throughout |
| Database | PostgreSQL 16 + TimescaleDB (hypertable on `readings`) |
| Driver | `psycopg` 3 (one DSN drives both sync and async engines) |
| Migrations | Alembic (hand-written initial migration) |
| Auth | PyJWT (HS256), `pwdlib[argon2]` (argon2id) |
| AI | Anthropic SDK 1.x, `claude-sonnet-5`, native tool calling |
| Streaming | Server-Sent Events over `StreamingResponse` |
| Frontend | Vite, React 18, TypeScript (strict), TanStack Query, React Router 6 |
| UI | Tailwind CSS, shadcn/ui, `lucide-react`, Recharts |
| Backend tests | pytest, pytest-asyncio, httpx, testcontainers |
| Frontend tests | Vitest, Testing Library, jsdom |
| Lint / format | ruff (backend), ESLint + Prettier (frontend) |
| Type checking | `mypy --strict`, `tsc` |
| Local infra | Docker, Docker Compose |
| CI | GitHub Actions |

---

## Project Structure

```
DataBull/
├── backend/
│   ├── app/
│   │   ├── api/                  # FastAPI routers, one file per resource
│   │   │   ├── auth.py           # signup, login, me, account deletion
│   │   │   ├── devices.py        # device CRUD
│   │   │   ├── readings.py       # ingest, query, aggregate, alerts, delete
│   │   │   └── chat.py           # POST /chat/stream,  SSE adapter only
│   │   ├── agent/                # AI assistant,  no FastAPI imports
│   │   │   ├── runner.py         # run_agent,  the single public entry point
│   │   │   ├── tools.py          # tool handlers; execute_tool never raises
│   │   │   ├── tool_schemas.py   # JSON schemas sent to the model
│   │   │   ├── services.py       # AgentServices + repository Protocols
│   │   │   ├── llm_client.py     # Anthropic adapter + cost estimation
│   │   │   ├── prompt.py         # versioned system prompts
│   │   │   └── events.py         # public SSE event contract
│   │   ├── core/
│   │   │   ├── config.py         # pydantic-settings; env-driven
│   │   │   ├── security.py       # hashing + JWT encode/decode
│   │   │   ├── deps.py           # DbSession / CurrentUser annotated aliases
│   │   │   └── errors.py         # APIError → {detail, code, field?}
│   │   ├── db/
│   │   │   ├── base.py           # DeclarativeBase + constraint naming
│   │   │   └── session.py        # async engine + session factory
│   │   ├── models/               # SQLAlchemy models (user, device, reading)
│   │   ├── schemas/              # Pydantic models, kept separate from ORM
│   │   ├── services/             # business logic,  all DB access
│   │   └── main.py               # app factory, router registration, /health
│   ├── alembic/
│   │   └── versions/
│   │       └── 0001_initial_schema.py   # includes create_hypertable(...)
│   ├── scripts/
│   │   └── demo_seed.py          # seeds a demo user, devices, and readings
│   ├── tests/                    # pytest,  real Postgres via testcontainers
│   │   ├── conftest.py           # db_session, client, authed_client fixtures
│   │   ├── agent_fakes.py        # in-memory repos + scripted LLM client
│   │   └── test_*.py
│   ├── Dockerfile                # python:3.12-slim, non-root user
│   ├── entrypoint.sh             # runs `alembic upgrade head`, then the CMD
│   └── pyproject.toml
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── DashboardPage.tsx      # fleet overview + chart
│   │   │   ├── DevicesPage.tsx        # device list, create, delete
│   │   │   ├── DeviceDetailPage.tsx   # one device: readings + aggregates
│   │   │   ├── LoginPage.tsx
│   │   │   ├── SignupPage.tsx
│   │   │   └── AuthForm.tsx           # shared credential form
│   │   ├── components/
│   │   │   ├── AppShell.tsx           # nav + layout for authed routes
│   │   │   ├── ChatDrawer.tsx         # assistant panel, streamed over SSE
│   │   │   ├── ProtectedRoute.tsx     # redirects unauthenticated users
│   │   │   ├── ReadingsChart.tsx      # Recharts time-series view
│   │   │   ├── ConfirmDialog.tsx      # destructive-action confirmation
│   │   │   ├── states/DataStates.tsx  # loading / empty / error states
│   │   │   └── ui/                    # shadcn primitives
│   │   ├── lib/
│   │   │   ├── api.ts            # fetch wrapper + SSE frame parser
│   │   │   ├── auth.tsx          # AuthProvider,  token in memory
│   │   │   ├── useChat.ts        # chat transcript + streaming turn state
│   │   │   ├── queries.ts        # TanStack Query hooks + query keys
│   │   │   ├── api-types.ts      # GENERATED,  do not hand-edit
│   │   │   └── types.ts          # shared hand-written types
│   │   └── test/                 # vitest setup + render helpers
│   ├── tailwind.config.ts        # design tokens,  never inline hex codes
│   ├── vite.config.ts            # dev server, /api proxy, vitest config
│   └── package.json
│
├── docs/
│   ├── SPEC.md                   # the contract,  read before any change
│   └── SIMULATOR.md              # placeholder; simulator not yet built
│
├── evals/                        # agent eval suite — 30 cases, replayed from cassettes
│   ├── cases.yaml                # the cases: question, tool sequence, rubric
│   ├── runner.py                 # pytest module — calls run_agent directly
│   ├── grading.py                # case schema, loader, both graders
│   ├── fixtures.py               # deterministic fleet + in-memory repositories
│   ├── cassette.py               # record/replay of Anthropic turns
│   └── test_harness.py           # tests for the harness itself
│   ├── cases.yaml
│   └── runner.py
│
├── .github/workflows/
│   └── ci.yml                    # backend + frontend jobs
│
├── docker-compose.yml            # TimescaleDB + API
└── CLAUDE.md                     # conventions for AI-assisted development
```

---

## Getting Started

### Prerequisites

- Docker and Docker Compose
- Python 3.12+ and Node.js 22+ (only for running tests or the frontend outside Docker)
- An Anthropic API key,  **optional**. Without one the API runs normally and only `POST /chat/stream` returns `503 assistant_unavailable`.

### Quick Start (Docker)

```bash
git clone https://github.com/abirislam910/DataBull.git
cd DataBull

cp backend/.env.example backend/.env

# Generate a signing key and paste it into backend/.env as SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"

docker compose up
```

This starts TimescaleDB and the API. Migrations run automatically on container start via `entrypoint.sh`, so there is no separate upgrade step. The API is then available on `http://localhost:8000`, with interactive docs at `http://localhost:8000/docs`.

Optionally seed a demo account with devices and a few days of readings:

```bash
python backend/scripts/demo_seed.py
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Backend Outside Docker

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

alembic upgrade head
uvicorn app.main:app --reload
```

### Regenerating API Types

Whenever the API surface changes, refresh the generated frontend types:

```bash
curl http://localhost:8000/openapi.json -o frontend/openapi.json
cd frontend && npm run gen:api
```

---

## Environment Variables

### Backend (`backend/.env`)

| Variable | Required | Default | Description |
|:---------|:---------|:--------|:------------|
| `SECRET_KEY` | **Yes** |,  | Signs and verifies every JWT. The app will not start without it. |
| `DATABASE_URL` | No | `postgresql+psycopg://postgres:postgres@localhost:5432/telemetry` | Postgres DSN. One value drives both the async app engine and Alembic's sync engine. |
| `SQL_ECHO` | No | `false` | Log every SQL statement. Noisy; useful when debugging. |
| `ANTHROPIC_API_KEY` | No | unset | Only `POST /chat/stream` needs it. Unset means that one route 503s and nothing else changes. |
| `AGENT_MODEL` | No | `claude-sonnet-5` | Model ID. Note that current model IDs carry no date suffix. |
| `AGENT_EFFORT` | No | `low` | Reasoning effort. Current models reject sampling parameters such as `temperature`. |
| `AGENT_MAX_TOKENS` | No | `1024` | Output cap per turn. |
| `AGENT_MAX_TOOL_CALLS` | No | `10` | Tool-call ceiling for one turn, checked before execution. |
| `AGENT_TOOL_RESULT_MAX_BYTES` | No | `2048` | Tool payloads are truncated to this, keeping the head. |
| `AGENT_LLM_TIMEOUT_SECONDS` | No | `30.0` | Per-request timeout to the model. |
| `AGENT_TURN_TIMEOUT_SECONDS` | No | `60.0` | Wall-clock bound on an entire agent turn. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `1440` | Token lifetime (24 hours). |
| `MIN_PASSWORD_LENGTH` | No | `8` | Minimum password length. |
| `MAX_BULK_READINGS` | No | `10000` | Cap on rows per bulk ingest request. |
| `MAX_AGGREGATE_BUCKETS` | No | `1000` | Aggregate requests wider than this are rejected. |

### Frontend

No environment variables are required for local development; the dev server proxy handles API routing.

---

## Testing & CI

```bash
# Backend,  spins up real Postgres + TimescaleDB in Docker
cd backend && pytest
cd backend && pytest -k test_agent      # one module

# Frontend
cd frontend && npm test

# Agent evals,  replays recorded model turns; no API key, no network, no cost
pytest evals/
```

**222 tests** in total: 153 backend, 44 frontend, 25 eval-harness.

Backend tests run against a PostgreSQL + TimescaleDB container via testcontainers. Each test runs inside a savepoint that is rolled back afterwards, so the suite is order-independent without rebuilding the schema per test.

The agent is tested without spending money. `LLMClient` is a Protocol, so a scripted fake replays model turns and `run_agent` executes unmodified,  the real loop, the real tool dispatch, no network. The 25 agent tests run in about 0.1s with no database at all. Per `CLAUDE.md`, no test may make a live Anthropic call without a recorded fixture.

**Evals are separate from tests.** `backend/tests/test_agent.py` proves the tool loop works; `evals/` asks whether the *model* picks the right tools and grounds its answers — a different question with a different failure mode. The 30 cases replay recorded Anthropic turns from `evals/cassettes/`, so CI runs them for free and deterministically. A case with no cassette yet is skipped and counted in the printed metrics, so an all-skipped run cannot be mistaken for a pass. Recording is opt-in, needs a key, and spends money: see [evals/README.md](evals/README.md).

GitHub Actions runs two jobs on every push and pull request:

| Job | Gates |
|:----|:------|
| `backend` | `pip-audit` · `ruff check` · `ruff format --check` · `mypy --strict app tests` · `mypy --strict evals` · `ruff` on `evals` · `pytest` · `pytest evals/` (replay) |
| `frontend` | `npm audit` (high+, production deps gate the build) · `eslint` · `prettier --check` · `tsc --noEmit` · `vitest` · `vite build` |

---

## Deployment

There is **no hosted deployment yet.** The project runs locally through Docker Compose.

What is in place for one:

- **`backend/Dockerfile`** builds on `python:3.12-slim`, installs dependencies in a separate layer from application code so the cache survives code changes, and runs as a non-root `appuser`.
- **`backend/entrypoint.sh`** runs `alembic upgrade head` before handing off to the process command, so a container start always reconciles the schema.
- **`docker-compose.yml`** provisions `timescale/timescaledb:latest-pg16` with a health check, and the API waits on it rather than racing it.

---

## API Reference

All times are UTC and ISO 8601. Errors share one shape: `{"detail": "...", "code": "machine_readable_string", "field"?: "field_name"}`. Filtering is always by query parameter, never by path.

### Auth

| Method | Endpoint | Auth | Description |
|:-------|:---------|:-----|:------------|
| POST | `/auth/signup` | No | Create an account; returns a bearer token |
| POST | `/auth/login` | No | Exchange credentials for a bearer token |
| GET | `/auth/me` | Yes | The authenticated account |
| POST | `/auth/me/delete` | Yes | Delete the account and all its data; requires the password in the body |

### Devices

| Method | Endpoint | Auth | Description |
|:-------|:---------|:-----|:------------|
| POST | `/devices` | Yes | Create a device (`name`, `type`, `unit`, optional thresholds) |
| GET | `/devices` | Yes | List the caller's devices |
| GET | `/devices/{device_id}` | Yes | Fetch one device |
| PATCH | `/devices/{device_id}` | Yes | Update name, unit, or thresholds |
| DELETE | `/devices/{device_id}` | Yes | Delete a device and its readings |

### Readings

| Method | Endpoint | Auth | Description |
|:-------|:---------|:-----|:------------|
| POST | `/devices/{device_id}/readings` | Yes | Ingest one reading |
| POST | `/devices/{device_id}/readings/bulk` | Yes | Ingest up to 10,000 readings in one request |
| GET | `/readings` | Yes | Raw readings,  `device_id`, `start`, `end`, `limit` |
| GET | `/readings/aggregate` | Yes | Rollups,  `device_id`, `window` (`1h`/`1d`/`1w`), `fn` (`avg`/`min`/`max`/`p95`), `start`, `end` |
| GET | `/readings/alerts` | Yes | Threshold breaches,  `since`, optional `device_id`, `limit` |
| DELETE | `/readings` | Yes | Delete within a window,  `device_id`, `start`, `end`, `dry_run` |

Time windows are half-open: `start` is inclusive, `end` exclusive. Naive timestamps are interpreted as UTC.

### Assistant

| Method | Endpoint | Auth | Description |
|:-------|:---------|:-----|:------------|
| POST | `/chat/stream` | Yes | Stream an assistant turn as SSE. Body: `{messages: [{role, content}]}`, 1–50 turns, roles `user` or `assistant` |

Each SSE frame is `data: <json>`. Event types:

| `type` | Payload |
|:-------|:--------|
| `text` | `delta`,  a chunk of the assistant's prose |
| `tool_use` | `name`, `input`,  a tool the model decided to call |
| `tool_result` | `name`, `summary` (≤200 chars), `truncated` |
| `error` | `code` (`tool_failed` / `llm_failed` / `rate_limited` / `invalid_input`), `message` |
| `done` | `usage` (`input_tokens`, `output_tokens`, `cost_usd`, `latency_ms`), `prompt_version`, `tool_calls` |

The stream always ends with `done`, including after an `error`.

The assistant has four tools: `list_devices`, `query_readings`, `aggregate_window`, and `get_recent_alerts`. Each is scoped to the calling user, and ownership is re-verified inside the repository layer rather than trusted from the model's arguments.

### Health

| Method | Endpoint | Auth | Description |
|:-------|:---------|:-----|:------------|
| GET | `/health` | No | Liveness probe,  `{"status": "ok"}` |

---

## Roadmap

Built and tested; not yet started where noted.

- [x] Async data layer, TimescaleDB hypertable, hand-written initial migration
- [x] JWT auth with argon2id, timing-safe login, tenant isolation
- [x] Device CRUD and the five readings endpoints
- [x] React dashboard,  fleet overview, device detail, charts
- [x] AI operator's assistant and `POST /chat/stream`
- [x] **Frontend chat drawer**,  a persistent assistant panel on the dashboard and device detail pages
- [x] **Agent eval suite**,  30 labeled cases with a cassette-replay runner (cassettes not yet recorded — see `evals/README.md`)
- [ ] **Sensor simulator**,  APScheduler-driven synthetic telemetry with reproducible seeding (`docs/SIMULATOR.md` is currently a placeholder)
- [ ] Hosted demo deployment