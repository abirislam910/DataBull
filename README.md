# DataBull

**An industrial time-series telemetry platform built with FastAPI and TimescaleDB, with an AI operator's assistant powered by Claude.**

DataBull ingests sensor readings from industrial devices (temperature, pressure, and flow) and serves them back as raw readings, bucketed statistical aggregations, or configured threshold breaches. Readings live in a TimescaleDB hypertable partitioned on time, so aggregate queries stay fast as history grows.

On top of the REST API sits an operator's assistant: a Claude tool-calling agent that answers natural-language questions about a user's device fleet ("has Pump-3 reported any alarms since Tuesday?") by calling the same service layer the API exposes. It streams its tool calls and answer to the client over Server-Sent Events. The same tools are also published as an MCP server, so any MCP client can query a fleet directly.

The web app is a React + TypeScript single-page app built with TanStack Query and Recharts. A dashboard summarizes the fleet with device and alert counts, an activity chart, and recent alerts, and each device has a page with a time-series chart, threshold lines, and a 24-hour alert history. A side drawer streams the assistant's answers as they are written, showing each tool call as it runs and the cost of every answer.

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

- **Time-Series Ingestion:** single-reading and bulk endpoints (up to 10,000 rows per request) writing into a TimescaleDB hypertable partitioned on `time`. Timestamps are normalized to UTC and must fall between 1970 and five minutes from now, which tolerates ordinary clock skew on a device.
- **Windowed Aggregation:** `time_bucket` rollups at 1-hour, 1-day, or 1-week granularity with `avg`, `min`, `max`, or `p95` (via `percentile_cont`). Requests that would exceed 1,000 buckets are rejected at validation rather than served slowly.
- **Threshold Alerting:** per-device `min_threshold` and `max_threshold`. `GET /readings/alerts` returns every breach since a given timestamp, across the whole fleet or for one device.
- **Device Management:** full CRUD over devices, each with a type (`temperature`, `pressure`, or `flow`), a unit, and optional thresholds.
- **JWT Authentication:** stateless HS256 bearer tokens with argon2id password hashing. Login is timing-safe and does not reveal whether an email is registered.
- **AI Operator's Assistant:** a Claude tool-calling agent over four typed tools, streamed to the client as SSE, with a per-turn tool-call budget and timeout, and token/cost accounting on every turn.
- **Operator Dashboard:** a fleet overview with device and alert counts, an activity chart, and recent alerts; device pages with a time-series chart, threshold overlays, and a 24-hour alert history; and a persistent assistant drawer that renders streamed answers as they arrive.
- **MCP Server:** the same four tools published over the Model Context Protocol, so Claude Desktop, Claude Code, or any other MCP client can query a fleet directly. Read-only, and a client of the API rather than of the database. See [docs/MCP.md](docs/MCP.md).
- **Generated API Types:** the frontend's request and response types are generated from the backend's OpenAPI schema, so a contract change breaks the build instead of production.

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

    EXT[MCP client<br/>Claude Desktop · Claude Code]
    MCP[databull-mcp<br/>stdio server]

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
    EXT -->|JSON-RPC over stdio| MCP
    MCP -->|HTTP + bearer token| RT
    RT --> SV
    RT --> AG
    AG --> SV
    AG --> AN
    SV --> US
    SV --> DV
    SV --> RD
```

The agent reaches the database only through the same `/services/` functions the routers use, behind a set of Protocols. It never imports FastAPI, and the data plane never imports the agent, so the two are separable by design.

The MCP server runs as its own process and reaches the data over HTTP. It supplies HTTP-backed implementations of the agent's repository Protocols, which lets it reuse the agent's tool handlers unchanged while inheriting the API's authentication and ownership checks.

### Request Flow: Ingest and Aggregation

```mermaid
sequenceDiagram
    participant C as Client / seed script
    participant API as FastAPI router
    participant SVC as services/reading.py
    participant TS as TimescaleDB

    C->>API: POST /devices/{id}/readings/bulk
    Note over API: Bearer token → device ownership check
    API->>API: Validate payload (≤ 10,000 rows, UTC timestamps)
    API->>SVC: create_readings_bulk(...)
    SVC->>TS: INSERT INTO readings (one statement)
    TS-->>SVC: rows written
    SVC-->>API: count
    API-->>C: 201 Created {count}

    C->>API: GET /readings/aggregate?window=1h&fn=p95
    API->>API: Reject windows exceeding 1,000 buckets
    API->>SVC: aggregate_readings(...)
    SVC->>TS: SELECT time_bucket('1 hour', time),<br/>percentile_cont(0.95) ... GROUP BY 1
    TS-->>SVC: buckets
    SVC-->>API: [AggregateBucket]
    API-->>C: 200 OK
```

### Request Flow: AI Assistant (SSE)

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

The whole turn is bounded by a single timeout rather than one per call, so a model that keeps asking for one more tool cannot hold the connection open indefinitely. `run_agent` never raises for an LLM or tool failure: those become `error` events, and the stream always ends with a `done` event.

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
    API->>DB: get_current_user (account looked up every request)
    API-->>FE: 200, or 401 with a machine-readable code
```

---

## Authentication & Security Decisions

JWT bearer tokens, HS256, 24-hour expiry. Passwords are hashed with **argon2id** (via `pwdlib`) and must be at least 8 characters.

The API refuses to start without `SECRET_KEY`. That is deliberate: the value signs every access token, so a shipped placeholder would let anyone mint a token for any account. Use a distinct key per environment.

**Token in memory only.** The frontend keeps the token in React context, never in `localStorage` or `document.cookie`. Anything readable from JavaScript can be exfiltrated by an XSS payload; a token held only in a closure cannot. The cost is that a tab reload loses it and the user signs in again. A test asserts the token never reaches either store or the cookie jar.

**No refresh tokens in v1.** A 24-hour access token is the whole session. When it expires, the user signs in again.

**Stateless tokens cannot be revoked.** The server stores no record of issued tokens; it re-verifies the signature on each request instead. A token therefore stays valid until it expires, even if the user signs out elsewhere. Deleting a user *does* lock them out immediately, because `get_current_user` looks the account up on every request. Revoking one outstanding token would require a denylist, which is deliberately out of scope for v1. Rotating `SECRET_KEY` invalidates every outstanding token at once.

**Login does not reveal whether an email is registered.** A wrong password and an unknown account return identical 401s, and the unknown-account path still runs an argon2 comparison so response timing does not leak the difference either.

**Tenant isolation returns 404, not 403.** Ownership is a predicate in the SQL `WHERE` clause, so another user's device never enters the result set. Requesting it is indistinguishable from requesting one that does not exist.

**Unique constraints arbitrate concurrent creates.** Signup, device creation, device renames, and bulk ingest insert first and translate a unique-constraint violation into a `409`, rather than checking for a conflict and then inserting. Two simultaneous requests cannot both pass a check that only the database can decide atomically.

**The client cannot inject a system turn.** `ChatMessageIn.role` accepts only `user` or `assistant`. A client-supplied `system` message would be a prompt-injection channel straight into the operator instructions, so it is rejected at validation.

**Model output is never rendered as HTML.** The assistant drawer renders a small Markdown subset (bold, bullet lists, numbered lists) as React elements. Device names are user-supplied and reach the model's answers through tool results, so rendering its output as HTML would open a stored XSS path.

---

## Tech Stack

| Layer | Technology |
|:------|:-----------|
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| ORM | SQLAlchemy 2.0, async throughout |
| Database | PostgreSQL 16 + TimescaleDB (hypertable on `readings`) |
| Driver | `psycopg` 3 (one DSN drives both the sync and async engines) |
| Migrations | Alembic (hand-written initial migration) |
| Auth | PyJWT (HS256), `pwdlib[argon2]` (argon2id) |
| AI | Anthropic SDK 1.x, `claude-sonnet-5`, native tool calling |
| Tool interop | MCP Python SDK 2.x, exposing the same tools to any MCP client |
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
│   │   │   └── chat.py           # POST /chat/stream (SSE adapter only)
│   │   ├── agent/                # AI assistant (no FastAPI imports)
│   │   │   ├── runner.py         # run_agent, the single public entry point
│   │   │   ├── tools.py          # tool handlers; execute_tool never raises
│   │   │   ├── tool_schemas.py   # JSON schemas sent to the model
│   │   │   ├── services.py       # AgentServices + repository Protocols
│   │   │   ├── llm_client.py     # Anthropic adapter + cost estimation
│   │   │   ├── prompt.py         # versioned system prompts
│   │   │   └── events.py         # public SSE event contract
│   │   ├── mcp/                  # MCP server: the agent's tools, for any client
│   │   │   ├── server.py         # MCPServer + tool registrations; databull-mcp
│   │   │   ├── repositories.py   # Http{Device,Reading,Alert}Repository
│   │   │   ├── client.py         # authenticated HTTP client for this API
│   │   │   └── settings.py       # environment-only configuration
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
│   │   ├── services/             # business logic; all DB access
│   │   └── main.py               # app factory, router registration, /health
│   ├── alembic/
│   │   └── versions/
│   │       └── 0001_initial_schema.py   # includes create_hypertable(...)
│   ├── scripts/
│   │   └── demo_seed.py          # seeds a demo account, devices, and readings
│   ├── tests/                    # pytest, real Postgres via testcontainers
│   │   ├── conftest.py           # db_session, client, authed_client fixtures
│   │   ├── agent_fakes.py        # in-memory repos + scripted LLM client
│   │   ├── test_mcp_*.py         # MCP repositories, tool surface, stdio handshake
│   │   └── test_*.py
│   ├── Dockerfile                # python:3.12-slim, non-root user
│   ├── entrypoint.sh             # runs `alembic upgrade head`, then the CMD
│   └── pyproject.toml            # dependencies; `dev` and `mcp` extras
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── DashboardPage.tsx      # fleet overview, activity chart, recent alerts
│   │   │   ├── DevicesPage.tsx        # device list, create, delete
│   │   │   ├── DeviceDetailPage.tsx   # one device: chart, thresholds, alert history
│   │   │   ├── LoginPage.tsx
│   │   │   ├── SignupPage.tsx
│   │   │   └── AuthForm.tsx           # shared credential form
│   │   ├── components/
│   │   │   ├── AppShell.tsx           # nav + layout for signed-in routes
│   │   │   ├── ChatDrawer.tsx         # assistant panel, streamed over SSE
│   │   │   ├── AssistantText.tsx      # safe rendering of the assistant's Markdown
│   │   │   ├── ProtectedRoute.tsx     # redirects signed-out users
│   │   │   ├── ReadingsChart.tsx      # Recharts time-series view
│   │   │   ├── ConfirmDialog.tsx      # destructive-action confirmation
│   │   │   ├── states/DataStates.tsx  # loading / empty / error states
│   │   │   └── ui/                    # shadcn primitives
│   │   ├── lib/
│   │   │   ├── api.ts            # fetch wrapper + SSE frame parser
│   │   │   ├── auth.tsx          # AuthProvider; token in memory
│   │   │   ├── useChat.ts        # chat transcript + streaming turn state
│   │   │   ├── queries.ts        # TanStack Query hooks + query keys
│   │   │   ├── api-types.ts      # generated; do not edit by hand
│   │   │   └── types.ts          # shared types and the SSE event contract
│   │   └── test/                 # Vitest setup + render helpers
│   ├── tailwind.config.ts        # design tokens; never inline hex codes
│   ├── vite.config.ts            # dev server, /api proxy, Vitest config
│   └── package.json
│
├── evals/                        # agent eval suite: 30 cases replayed from cassettes
│   ├── cases.yaml                # the cases: question, tool sequence, rubric
│   ├── runner.py                 # pytest module; calls run_agent directly
│   ├── grading.py                # case schema, loader, both graders
│   ├── fixtures.py               # deterministic fleet + in-memory repositories
│   ├── cassette.py               # record/replay of Anthropic turns
│   ├── conftest.py               # import paths, run mode, metrics report
│   ├── test_harness.py           # tests for the harness itself
│   └── cassettes/                # recorded model turns, one file per case
│
├── docs/
│   ├── SPEC.md                   # the contract; read before any change
│   ├── MCP.md                    # MCP server setup and client configuration
│   └── SIMULATOR.md              # placeholder; simulator not yet built
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
- Python 3.12+ and Node.js 22+ (for the frontend, the tests, and running the API outside Docker)
- An Anthropic API key, **optional**. Without one, the API still serves devices and readings; only the assistant is unavailable.

### Quick Start (Docker)

Docker Compose reads `SECRET_KEY` and `ANTHROPIC_API_KEY` from your shell or from a `.env` file at the repository root (gitignored).

```bash
git clone https://github.com/abirislam910/DataBull.git
cd DataBull

echo "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" > .env
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env   # optional: enables the assistant

docker compose up
```

This starts TimescaleDB and the API. Migrations run automatically on container start via `entrypoint.sh`, so there is no separate upgrade step. The API is then available at `http://localhost:8000`, with interactive docs at `http://localhost:8000/docs`.

To seed a demo account, run this once the API is up:

```bash
python3 backend/scripts/demo_seed.py
```

It creates `operator@example.com` (password `a-good-password`) with three devices (Pump-3, Furnace-1, and Line-Press) and the last three hours of readings at one per minute, including a few spikes that cross the configured thresholds.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The dev server runs on `http://localhost:5173` and proxies `/api/*` to the API on port 8000. Sign in with the demo account to see the seeded data.

### Backend Outside Docker

Start just the database with Compose, then run the API directly with Python 3.12+ (a virtual environment is recommended):

```bash
docker compose up -d db

cd backend
pip install -e ".[dev]"
cp .env.example .env               # then set SECRET_KEY
alembic upgrade head
uvicorn app.main:app --reload
```

### MCP Server

```bash
cd backend
pip install -e ".[mcp]"            # registers the databull-mcp command

DATABULL_EMAIL=operator@example.com DATABULL_PASSWORD=a-good-password databull-mcp --check
```

`--check` signs in, lists the fleet, and exits. Client configuration for Claude Desktop and Claude Code is in [docs/MCP.md](docs/MCP.md).

### Regenerating API Types

Whenever the API surface changes, refresh the generated frontend types:

```bash
curl http://localhost:8000/openapi.json -o frontend/openapi.json
cd frontend && npm run gen:api
```

---

## Environment Variables

### Backend

When the API runs outside Docker, it reads `backend/.env` (start from `backend/.env.example`). Under Docker Compose, the container receives `DATABASE_URL`, `SECRET_KEY`, and `ANTHROPIC_API_KEY` from `docker-compose.yml`; every other setting uses its default unless added there.

| Variable | Required | Default | Description |
|:---------|:---------|:--------|:------------|
| `SECRET_KEY` | **Yes** | none | Signs and verifies every JWT. The app will not start without it. |
| `DATABASE_URL` | No | `postgresql+psycopg://postgres:postgres@localhost:5432/telemetry` | Postgres DSN. One value drives both the async app engine and Alembic's sync engine. |
| `SQL_ECHO` | No | `false` | Log every SQL statement. Noisy; useful when debugging. |
| `ANTHROPIC_API_KEY` | No | unset | Needed only by `POST /chat/stream`. Without it, that route returns `503 assistant_unavailable` and nothing else changes. |
| `AGENT_MODEL` | No | `claude-sonnet-5` | Claude model used by the assistant. |
| `AGENT_EFFORT` | No | `low` | Reasoning effort: `low`, `medium`, `high`, `xhigh`, or `max`. |
| `AGENT_MAX_TOKENS` | No | `1024` | Output cap per model turn. |
| `AGENT_MAX_TOOL_CALLS` | No | `10` | Tool-call ceiling for one turn, checked before execution. |
| `AGENT_TOOL_RESULT_MAX_BYTES` | No | `2048` | Tool payloads are truncated to this size, keeping the first rows (for readings, the newest). |
| `AGENT_LLM_TIMEOUT_SECONDS` | No | `30.0` | Per-request timeout to the model. |
| `AGENT_TURN_TIMEOUT_SECONDS` | No | `60.0` | Wall-clock bound on an entire assistant turn. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `1440` | Token lifetime (24 hours). |
| `MIN_PASSWORD_LENGTH` | No | `8` | Minimum password length. |
| `MAX_BULK_READINGS` | No | `10000` | Cap on rows per bulk ingest request. |
| `DEFAULT_READING_LIMIT` | No | `1000` | Rows returned by `GET /readings` when no `limit` is given. |
| `MAX_READING_LIMIT` | No | `10000` | Largest `limit` `GET /readings` accepts. |
| `MAX_AGGREGATE_BUCKETS` | No | `1000` | Aggregate requests wider than this are rejected. |

`.env` files are gitignored. Never commit real secrets; document new variables in `backend/.env.example` instead.

### MCP Server

The MCP server reads its configuration from the environment only, usually supplied by the MCP client's config: `DATABULL_API_URL`, and either `DATABULL_EMAIL` with `DATABULL_PASSWORD` or a `DATABULL_TOKEN`. The full table is in [docs/MCP.md](docs/MCP.md#configuration).

### Frontend

No environment variables are required for local development; the dev server's proxy handles API routing.

---

## Testing & CI

```bash
# Backend: starts a real Postgres + TimescaleDB container (needs a running Docker daemon)
cd backend && pytest
cd backend && pytest -k test_agent      # one module

# Frontend
cd frontend && npm test

# Agent evals: replays recorded model turns; no API key, no network, no cost
pytest evals/

# MCP server: repositories against the real app, plus a real stdio handshake
cd backend && pytest tests/test_mcp_repositories.py tests/test_mcp_server.py tests/test_mcp_stdio.py
```

**259 tests** in total: 180 backend, 53 frontend, and 26 for the eval harness, plus the 30 eval cases.

**Backend.** Tests run against a PostgreSQL + TimescaleDB container via testcontainers. Each test runs inside a savepoint that is rolled back afterwards, so the suite is order-independent without rebuilding the schema per test.

**Agent.** `LLMClient` is a Protocol, so a scripted fake replays model turns and `run_agent` executes unmodified: the real loop and the real tool dispatch, with no network. The 26 agent tests run in a fraction of a second with no database at all. Per `CLAUDE.md`, no test may make a live Anthropic call without a recorded fixture.

**MCP server.** Its repositories send every request through the actual FastAPI application over `httpx.ASGITransport`, backed by the same testcontainers Postgres, so a tenant-isolation assertion checks the API's real behavior rather than a stub's. A separate test spawns the server as a subprocess and completes a real `initialize` and `list_tools` handshake over stdio; it needs no API and no credentials.

**Frontend.** Vitest and Testing Library cover the SSE frame parser (including frames split across chunk boundaries), the assistant drawer's streaming, error, and retry behavior, keyboard and focus behavior in the delete confirmation dialog, and the guarantee that model output is never rendered as HTML.

**Evals are separate from tests.** `backend/tests/test_agent.py` proves the tool loop works; `evals/` asks whether the *model* picks the right tools and grounds its answers. The 30 cases replay recorded Anthropic turns from `evals/cassettes/`, so CI runs them for free and deterministically; all 30 currently pass. Recording is opt-in, needs a key, and costs money. See [evals/README.md](evals/README.md).

GitHub Actions runs two jobs on every push and pull request:

| Job | Gates |
|:----|:------|
| `backend` | install with the `dev` and `mcp` extras · `pip-audit` · `ruff check` · `ruff format --check` · `mypy --strict app tests` · `mypy --strict` and `ruff` on `evals` · `pytest` · `pytest evals/` (replay) |
| `frontend` | `npm ci` · `npm audit` on production dependencies (high and above) · `eslint` · `prettier --check` · `tsc --noEmit` · `vitest` · `vite build` |

---

## Deployment

There is **no hosted deployment yet.** The project runs locally through Docker Compose.

What is in place for one:

- **`backend/Dockerfile`** builds on `python:3.12-slim`, installs dependencies in a separate layer from application code so the cache survives code changes, and runs as a non-root `appuser`.
- **`backend/entrypoint.sh`** runs `alembic upgrade head` before handing off to the process command, so a container start always reconciles the schema.
- **`docker-compose.yml`** provisions `timescale/timescaledb:latest-pg16` with a health check, and the API waits for it to be healthy before starting.

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
| POST | `/devices/{device_id}/readings` | Yes | Ingest one reading; `time` defaults to now |
| POST | `/devices/{device_id}/readings/bulk` | Yes | Ingest up to 10,000 readings in one request; every row needs a `time` |
| GET | `/readings` | Yes | Raw readings, newest first: `device_id`, `start`, `end`, `limit` |
| GET | `/readings/aggregate` | Yes | Rollups: `device_id`, `window` (`1h`, `1d`, `1w`), `fn` (`avg`, `min`, `max`, `p95`), `start`, `end` |
| GET | `/readings/alerts` | Yes | Threshold breaches: `since`, optional `device_id`, `limit` |
| DELETE | `/readings` | Yes | Delete readings in a window: `device_id`, `start`, `end`; `dry_run=true` returns the count without deleting |

Time windows are half-open: `start` is inclusive and `end` exclusive. Naive timestamps are interpreted as UTC. A reading timestamped before 1970 or more than five minutes in the future is rejected. A reading that duplicates an existing `(device, time)` pair returns `409 duplicate_reading`, and a bulk request containing one is rejected as a whole.

### Assistant

| Method | Endpoint | Auth | Description |
|:-------|:---------|:-----|:------------|
| POST | `/chat/stream` | Yes | Stream an assistant turn as SSE. Body: `{messages: [{role, content}]}`, 1 to 50 turns, roles `user` or `assistant` |

Each SSE frame is `data: <json>`. Event types:

| `type` | Payload |
|:-------|:--------|
| `text` | `delta`: a chunk of the assistant's prose |
| `tool_use` | `name`, `input`: a tool the model decided to call |
| `tool_result` | `name`, `summary` (≤200 chars), `truncated` |
| `error` | `code` (`tool_failed`, `llm_failed`, `rate_limited`, or `invalid_input`), `message` |
| `done` | `usage` (`input_tokens`, `output_tokens`, `cost_usd`, `latency_ms`), `prompt_version`, `tool_calls` |

The stream always ends with `done`, including after an `error`.

The assistant has four tools: `list_devices`, `query_readings`, `aggregate_window`, and `get_recent_alerts`. Each is scoped to the calling user, and ownership is re-verified inside the repository layer rather than trusted from the model's arguments.

### Health

| Method | Endpoint | Auth | Description |
|:-------|:---------|:-----|:------------|
| GET | `/health` | No | Liveness probe: `{"status": "ok"}` |

### MCP Tools

These are not HTTP endpoints. The same four tools the assistant uses are also published over the Model Context Protocol, reached by launching `databull-mcp` rather than by calling a URL: `list_devices`, `query_readings`, `aggregate_window`, and `get_recent_alerts`. All are read-only. Setup and client configuration are in [docs/MCP.md](docs/MCP.md).

---

## Roadmap

Built and tested, except where unchecked.

- [x] Async data layer, TimescaleDB hypertable, hand-written initial migration
- [x] JWT auth with argon2id, timing-safe login, tenant isolation
- [x] Device CRUD and the readings endpoints
- [x] React dashboard: fleet overview, device detail, charts
- [x] AI operator's assistant and `POST /chat/stream`
- [x] Assistant chat drawer on the dashboard and device pages
- [x] Agent eval suite: 30 labeled cases with a cassette-replay runner
- [x] MCP server over stdio for any MCP client
- [ ] Security review of the MCP server's `streamable-http` transport (the flag exists; use stdio until it is reviewed)
- [ ] Sensor simulator: APScheduler-driven synthetic telemetry with reproducible seeding
- [ ] Hosted demo deployment
