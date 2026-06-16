# Sensor Telemetry Platform — Specification

*This file is the source of truth for the data model, endpoints, and architectural decisions.*

---

## Goal

A Python/FastAPI service that ingests, stores, and serves time-series sensor data, with an AI operator's assistant that answers natural-language questions about the data via tool-calling. Built as a portfolio project to gain experience with Python backend development, time-series database modeling, JWT authentication, and eval-driven agent development.

## Non-goals (explicit)

These were considered and deliberately excluded for v1.

- Real-time streaming via WebSocket (polling at 5–10s is sufficient)
- Sophisticated anomaly detection beyond simple threshold alerts
- Multi-tenancy or role-based access control beyond per-user isolation
- Mobile UI
- Real PLC integration (use a simulator)
- Email/SMS alert delivery

## Glossary

- **Device** — a logical sensor registered by a user (e.g. "Pump-3", "Furnace-1")
- **Reading** — a single `(device, timestamp, value)` tuple
- **Aggregate** — a rollup of readings across a window using `avg | min | max | p95`
- **Alert** — a reading exceeding a configured min/max threshold for its device

---

## Data model

### `users`

| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| email | str | unique, indexed |
| password_hash | str | bcrypt via passlib |
| created_at | timestamptz | default now() |

### `devices`

| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| user_id | UUID | FK → users, indexed |
| name | str | unique per user |
| type | enum | `temperature` \| `pressure` \| `flow` \| `vibration` |
| unit | str | °C, kPa, L/min, mm/s |
| min_threshold | float \| null | for alert generation |
| max_threshold | float \| null | for alert generation |
| created_at | timestamptz | default now() |

### `readings` (TimescaleDB hypertable)

| Column | Type | Notes |
|---|---|---|
| time | timestamptz | hypertable partition key; indexed |
| device_id | UUID | FK → devices, indexed |
| value | float | |

Composite primary key: `(time, device_id)`. Hypertable `chunk_time_interval = 1 day`. Create with `SELECT create_hypertable('readings', 'time')` in the Alembic migration.

---

## API endpoints

All responses JSON. All times UTC, ISO 8601. Errors follow `{"detail": "...", "code": "..."}` shape.

### Auth (public)

- `POST /auth/signup` — `{email, password}` → `{access_token, token_type: "bearer"}`
- `POST /auth/login` — `{email, password}` → `{access_token, token_type: "bearer"}`
- `GET /auth/me` — bearer token → `{id, email, created_at}`

### Devices (protected)

- `POST /devices` — `{name, type, unit, min_threshold?, max_threshold?}` → Device
- `GET /devices` → `list[Device]`
- `GET /devices/{id}` → Device
- `DELETE /devices/{id}` → 204

### Readings (protected)

- `POST /devices/{id}/readings` — `{value, time?}` → Reading
- `POST /devices/{id}/readings/bulk` — `[{value, time}, ...]` → `{count}`
- `GET /readings?device_id=&start=&end=&limit=` → `list[Reading]`
- `GET /readings/aggregate?device_id=&window=1h|1d|1w&fn=avg|min|max|p95&start=&end=` → `list[{bucket, value}]`
- `GET /readings/alerts?device_id?&since=` → `list[Alert]`

### Chat (protected)

- `POST /chat/stream` — `{messages: [{role, content}, ...]}` → SSE stream of events:
  - `{"type": "text", "delta": "..."}`
  - `{"type": "tool_use", "name": "...", "input": {...}}`
  - `{"type": "tool_result", "name": "...", "summary": "..."}`
  - `{"type": "done", "usage": {input_tokens, output_tokens, cost_usd, latency_ms}}`

---

## Authentication

JWT signed with HS256 using `SECRET_KEY` from env. 24-hour expiry. No refresh tokens in v1 — re-login on expiry; document the tradeoff in README.

Password requirements: minimum 8 characters. No complexity rules (modern NIST guidance prefers length over composition).

Frontend stores the token in memory only (React context), which protects against XSS exfiltration at the cost of requiring re-login on tab reload.

---

## Sensor simulator

APScheduler `IntervalTrigger` runs every 5 seconds. For each registered device, append one reading using:

- Baseline value per device type: T 25°C, P 100 kPa, F 50 L/min, V 2 mm/s
- Sinusoidal variation: `baseline + amplitude * sin(2π * t / period)`, where period = 10 minutes
- Gaussian noise: ±5% of baseline (`numpy.random.normal(0, 0.05 * baseline)`)
- 1% chance per tick of a "spike" event: `value × 2.5`, which generates an alert if thresholds are configured

Document in `/docs/SIMULATOR.md` how to seed reproducibly with a fixed numpy seed for tests.

---

## AI operator's assistant

### Tools exposed to Claude

1. `list_devices()` → list of devices the current user owns
2. `query_readings(device_id: str, start: ISO8601, end: ISO8601, limit: int = 1000)` → raw readings in window
3. `aggregate_window(device_id: str, window: "1h"|"1d"|"1w", fn: "avg"|"min"|"max"|"p95", start: ISO8601, end: ISO8601)` → bucketed aggregates
4. `get_recent_alerts(since: ISO8601, device_id: str | None = None)` → alerts in window

All tool inputs validated with Pydantic before execution. Tool results truncated/summarized server-side if they'd exceed ~2KB to keep token costs predictable.

### System prompt outline

- **Identity**: "You are an industrial operator's assistant. You help users understand sensor data from devices they monitor."
- **Grounding**: "Answer only from tool results. If you have no data, say so. Never fabricate readings or device names."
- **Citation**: "Cite device names and time ranges in every quantitative answer."
- **Caveats**: "Decline to speculate about root causes or future events beyond what the data supports."
- **Brevity**: "Default to ≤3 sentences unless the user asks for more detail."

### Streaming protocol

Server runs the tool-calling loop. Each iteration:
1. Stream model output to the client as `text` events
2. When the model issues a `tool_use`, emit a `tool_use` event, execute the tool, emit a `tool_result` event with a one-line summary (not the full payload)
3. Loop until the model stops calling tools
4. Emit a `done` event with token usage and cost

---

## Eval plan

30 labeled cases in `/evals/cases.yaml`. Distribution:

- 12 simple recall — "show me X" / "what was Y at time Z"
- 8 aggregation — "average vibration on Pump-3 today"
- 5 multi-step — "compare Furnace-1 and Furnace-2 over the last hour"
- 3 alert-aware — "any issues with my devices in the last 24h?"
- 2 should-decline — "predict next week's failures" → expect a graceful decline

Each case specifies: the user question, expected tool-call sequence (names + argument patterns, not exact values), and a rubric for the final answer (keywords that must appear).

Eval runner is a pytest module under `/evals/`. CI uses recorded Anthropic responses (vcrpy-style) for determinism. A separate weekly job hits the live API to track drift.

Tracked metrics: tool-call precision (correct sequence), rubric pass rate, p50/p95 latency, mean cost per query.

---

## Key decisions

| Decision | Choice | Reason |
|---|---|---|
| Database | Postgres + TimescaleDB | Hypertables speed aggregation queries 10–100×; real industry tool |
| Auth | JWT | Stateless; one project goal is to implement auth at protocol level |
| Streaming | SSE not WebSocket | One-way streaming is sufficient; simpler to deploy and debug |
| Test DB | testcontainers, not SQLite | TimescaleDB hypertables aren't in SQLite; real Postgres tests catch more bugs |
| Component library | shadcn/ui | Code I own, no theming runtime; faster than MUI for portfolio polish |
| Tool result handling | Summarize server-side | Token cost predictability; keeps response shape stable |
