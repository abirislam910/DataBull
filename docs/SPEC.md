# Sensor Telemetry Platform — Specification
 
*Starter version. Copy into your new repo as `/docs/SPEC.md` and edit before writing code. This file is the source of truth for the data model, module boundaries, endpoints, and architectural decisions. Resist changing the API surface or module interfaces mid-build — update this doc first, then write the change.*
 
---
 
## Goal
 
A Python/FastAPI service that ingests, stores, and serves time-series sensor data, with an AI operator's assistant that answers natural-language questions about the data via tool-calling. Built as a portfolio project demonstrating production-quality Python backend engineering, time-series database modeling, JWT authentication, and eval-driven agent development.
 
## Non-goals (explicit)
 
These were considered and deliberately excluded for v1. New ideas go to `/docs/IDEAS.md`, not into the spec.
 
- Real-time streaming via WebSocket (polling at 5–10s is sufficient)
- Sophisticated anomaly detection beyond simple threshold alerts
- Multi-tenancy or role-based access control beyond per-user isolation
- Mobile UI
- Real PLC integration (use a simulator)
- Email/SMS alert delivery
- Splitting the agent into its own service (see Architecture § "Extraction path" for the deliberate deferral)
## Glossary
 
- **Device** — a logical sensor registered by a user (e.g. "Pump-3", "Furnace-1")
- **Reading** — a single `(device, timestamp, value)` tuple
- **Aggregate** — a rollup of readings across a window using `avg | min | max | p95`
- **Alert** — a reading exceeding a configured min/max threshold for its device
- **Tool** — a Python callable the agent can invoke; each tool has a Pydantic-typed signature and a JSON schema published to Claude
- **Agent module** — the self-contained `/backend/app/agent/` package; the only place LLM calls happen
---
 
## Architecture
 
### Shape
 
**Modular monolith.** One deployable FastAPI service. Two clearly separated internal modules: the *data plane* (auth, devices, readings) and the *agent plane* (chat, tools, evals). The modules share the database and the Python process; they do not share code beyond a small, explicit `services` layer that both consume.
 
This was chosen deliberately over a two-service split. The tradeoff analysis and the criteria for reversing the decision live below in § "Extraction path." Every architectural choice in this document assumes this shape and should be revisited if the shape changes.
 
### Module map
 
```
/backend/app
  /api            # HTTP surface — thin adapters, no business logic
    auth.py       # data plane
    devices.py    # data plane
    readings.py   # data plane
    chat.py       # agent plane — adapter only; imports run_agent
  /core           # shared: config, security, exceptions, logging
  /db             # session factory, declarative base
  /models         # SQLAlchemy models (data plane owns these)
  /schemas        # Pydantic request/response models
  /services       # data-plane business logic + repositories
    device_repo.py
    reading_repo.py
    alert_repo.py
    aggregation.py
  /agent          # AGENT MODULE — self-contained
    runner.py     # public entry point: run_agent(...)
    tools.py      # tool implementations
    tool_schemas.py  # JSON schemas published to Claude
    prompt.py     # system prompt as a versioned constant
    events.py     # AgentEvent types (SSE payload shapes)
    services.py   # AgentServices dependency container
    llm_client.py # Anthropic client wrapper
```
 
### Runtime model
 
**Async all the way.** One process, one asyncio event loop, one `AsyncEngine`, one `AsyncSession` factory. Every endpoint is `async def`. Every service and repository method is `async def`. The agent module is async by construction. Alembic runs migrations against a separate *sync* engine — this is standard and correct; migrations are admin-time work, not request-path work.
 
Module boundaries are code boundaries, not runtime boundaries. Both modules share this one process, this one event loop, and this one connection pool. Do not attempt a mixed sync-API + async-agent design inside the monolith — it forces `run_in_threadpool` bridging inside the agent module and either doubles the engine count or blocks the event loop. If a genuine sync/async split is ever needed, that's the extraction-path trigger, not a code-organization tweak.
 
### Boundary rules
 
Rules that keep the modules honest. If any of these gets violated, that's the signal to fix it *before* it compounds — not after.
 
1. **The data plane never imports from `/agent/`.** Enforced by a lint rule (importlinter or a custom ruff check).
2. **The agent module never imports from `/api/`.** It knows nothing about HTTP, FastAPI, or SSE.
3. **The agent module talks to the database through `AgentServices` only.** No direct SQLAlchemy sessions inside `/agent/`, no direct model imports beyond DTOs. This is the interface that would become a network boundary if the module is later extracted.
4. **The `/api/chat.py` router is an adapter, not a place for logic.** It receives the HTTP request, constructs `AgentServices`, calls `run_agent`, and translates the returned event stream into SSE frames. That's it. No prompt work, no tool logic, no LLM calls in the router.
5. **Prompts and tool definitions are versioned.** `prompt.py` exports `SYSTEM_PROMPT_V1` (etc.) as immutable constants. Evals record which prompt version they targeted.
6. **No sync DB sessions in application code.** Only `AsyncSession`. The only permitted sync engine lives in `alembic/env.py` for migrations.
### Public interface of the agent module
 
The single public function of the agent module. Nothing else in the codebase should import from `/agent/` except this function, the event types, and the services container.
 
```python
# /backend/app/agent/runner.py
 
async def run_agent(
    *,
    user_id: UUID,
    messages: list[ChatMessage],
    services: AgentServices,
    prompt_version: str = "v1",
) -> AsyncIterator[AgentEvent]:
    """
    Run one agent turn (which may involve multiple tool calls internally).
    Yields AgentEvent objects until the model stops calling tools and emits
    a final response. Terminates with a Done event carrying usage stats.
 
    Never raises for LLM or tool errors — those become error AgentEvents.
    Only raises for programmer errors (bad arguments, missing services).
    """
```
 
### Event shapes (public contract)
 
```python
# /backend/app/agent/events.py
 
class TextDelta(BaseModel):
    type: Literal["text"] = "text"
    delta: str
 
class ToolUse(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    name: str
    input: dict[str, Any]
 
class ToolResult(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    name: str
    summary: str            # ≤200 chars, human-readable
    truncated: bool = False # true if full result was summarized
 
class Error(BaseModel):
    type: Literal["error"] = "error"
    code: Literal["tool_failed", "llm_failed", "rate_limited", "invalid_input"]
    message: str
 
class Done(BaseModel):
    type: Literal["done"] = "done"
    usage: Usage            # input_tokens, output_tokens, cost_usd, latency_ms
    prompt_version: str
    tool_calls: int
 
AgentEvent = TextDelta | ToolUse | ToolResult | Error | Done
```
 
Client (frontend and evals) code depends only on these shapes. Internal changes to how the agent works don't affect callers.
 
### AgentServices — the extraction seam
 
```python
# /backend/app/agent/services.py
 
@dataclass
class AgentServices:
    """
    Dependency container passed into run_agent. Everything the agent needs
    to reach the outside world goes through this object. It is the natural
    seam along which the agent module would be split into its own service:
    replace these repository implementations with HTTP clients and the
    agent code needs zero changes.
    """
    devices: DeviceRepository
    readings: ReadingRepository
    alerts: AlertRepository
    llm: LLMClient
    now: Callable[[], datetime]   # injected clock; makes tests deterministic
```
 
Two concrete implementations of the repositories exist in the codebase:
 
- `Sqlalchemy{Device,Reading,Alert}Repository` — used in production (in-process, direct DB access)
- `Fake{...}Repository` — used in agent unit tests; in-memory implementation
An HTTP-based implementation would be added if and when the module is extracted.
 
### Tools — internal shape
 
```python
# /backend/app/agent/tools.py
 
@dataclass
class ToolContext:
    user_id: UUID
    services: AgentServices
 
async def list_devices(ctx: ToolContext) -> list[DeviceOut]:
    return await ctx.services.devices.list_for_user(ctx.user_id)
 
async def query_readings(
    ctx: ToolContext,
    *,
    device_id: UUID,
    start: datetime,
    end: datetime,
    limit: int = 1000,
) -> list[ReadingOut]: ...
 
async def aggregate_window(
    ctx: ToolContext,
    *,
    device_id: UUID,
    window: Literal["1h", "1d", "1w"],
    fn: Literal["avg", "min", "max", "p95"],
    start: datetime,
    end: datetime,
) -> list[AggregateBucket]: ...
 
async def get_recent_alerts(
    ctx: ToolContext,
    *,
    since: datetime,
    device_id: UUID | None = None,
) -> list[AlertOut]: ...
 
TOOLS: dict[str, ToolFn] = {
    "list_devices": list_devices,
    "query_readings": query_readings,
    "aggregate_window": aggregate_window,
    "get_recent_alerts": get_recent_alerts,
}
```
 
Tool inputs are validated by Pydantic. Tool outputs are summarized to ≤2KB before being returned to Claude (see `truncated` flag on ToolResult).
 
### Adapter — how the router uses the module
 
```python
# /backend/app/api/chat.py
 
@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    current_user: CurrentUser,
    services: Annotated[AgentServices, Depends(build_agent_services)],
):
    async def sse():
        async for event in run_agent(
            user_id=current_user.id,
            messages=body.messages,
            services=services,
        ):
            yield f"data: {event.model_dump_json()}\n\n"
    return StreamingResponse(sse(), media_type="text/event-stream")
```
 
That's the whole router. Any logic beyond translation belongs in `/agent/`, not here.
 
---
 
## Data model
 
### `users`
 
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| email | str | unique, indexed |
| password_hash | str | argon2id via passlib |
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
 
### Auth (public) — data plane
 
- `POST /auth/signup` — `{email, password}` → `{access_token, token_type: "bearer"}`
- `POST /auth/login` — `{email, password}` → `{access_token, token_type: "bearer"}`
- `GET /auth/me` — bearer token → `{id, email, created_at}`
### Devices (protected) — data plane
 
- `POST /devices` — `{name, type, unit, min_threshold?, max_threshold?}` → Device
- `GET /devices` → `list[Device]`
- `GET /devices/{id}` → Device
- `DELETE /devices/{id}` → 204
### Readings (protected) — data plane
 
- `POST /devices/{id}/readings` — `{value, time?}` → Reading
- `POST /devices/{id}/readings/bulk` — `[{value, time}, ...]` → `{count}`
- `GET /readings?device_id=&start=&end=&limit=` → `list[Reading]`
- `GET /readings/aggregate?device_id=&window=1h|1d|1w&fn=avg|min|max|p95&start=&end=` → `list[{bucket, value}]`
- `GET /readings/alerts?device_id?&since=` → `list[Alert]`
### Chat (protected) — agent plane
 
- `POST /chat/stream` — `{messages: [{role, content}, ...]}` → SSE stream of AgentEvent JSON (see Architecture § "Event shapes").
---
 
## Authentication
 
JWT signed with HS256 using `SECRET_KEY` from env. 24-hour expiry. No refresh tokens in v1 — re-login on expiry; document the tradeoff in README.
 
Password hashing: **argon2id** via `passlib[argon2]`. Parameters: `memory_cost=65536` (64 MiB), `time_cost=3`, `parallelism=4` — matches OWASP baseline recommendations. `CryptContext` is configured with `deprecated="auto"` so any future scheme addition triggers transparent rehashing on next login.
 
Password requirements: minimum 8 characters. No complexity rules (modern NIST guidance prefers length over composition).
 
Frontend stores the token in memory only (React context). Document this choice in README — protects against XSS exfiltration at the cost of requiring re-login on tab reload.
 
**Auth crossing the module boundary.** The `/api/chat.py` adapter authenticates the request using the standard `CurrentUser` dependency, then passes `user_id` explicitly into `run_agent`. The agent module never touches the JWT. This mirrors the pattern an eventual split would use — the agent service would receive a resolved `user_id` from an authenticated upstream call, not the raw token.
 
---
 
## Sensor simulator
 
APScheduler `IntervalTrigger` runs every 5 seconds. For each registered device, append one reading using:
 
- Baseline value per device type: T 25°C, P 100 kPa, F 50 L/min, V 2 mm/s
- Sinusoidal variation: `baseline + amplitude * sin(2π * t / period)`, where period = 10 minutes
- Gaussian noise: ±5% of baseline (`numpy.random.normal(0, 0.05 * baseline)`)
- 1% chance per tick of a "spike" event: `value × 2.5`, which generates an alert if thresholds are configured
The simulator lives at `/backend/app/simulator/` and is treated as a third module. It runs as a startup task inside the FastAPI process for local dev. In production it can be enabled or disabled by env flag. It uses `/services/` (never `/api/`) to write readings, and never imports from `/agent/`.
 
Document in `/docs/SIMULATOR.md` how to seed reproducibly with a fixed numpy seed for tests.
 
---
 
## Agent behavior
 
### System prompt (v1)
 
Owned by `/agent/prompt.py` as an immutable constant. Structural elements:
 
- **Identity**: "You are an industrial operator's assistant. You help users understand sensor data from devices they monitor."
- **Grounding**: "Answer only from tool results. If you have no data, say so. Never fabricate readings or device names."
- **Citation**: "Cite device names and time ranges in every quantitative answer."
- **Caveats**: "Decline to speculate about root causes or future events beyond what the data supports."
- **Brevity**: "Default to ≤3 sentences unless the user asks for more detail."
### Tool policy
 
- Maximum 10 tool calls per turn (safety cap against runaway loops)
- Tool results larger than 2KB are summarized before being fed back to the model; `truncated=True` is set on the corresponding event
- Tool errors become `ToolResult` events with an error summary rather than exceptions — the model gets a chance to recover
### LLM configuration
 
- Model: `claude-sonnet-4-6` for prod, `claude-haiku-4-5-20251001` for dev/CI
- Temperature: 0.2 (deterministic-ish for reproducibility)
- Max tokens: 1024 per turn
- Timeouts: 60s total per turn; individual LLM call timeout 30s
---
 
## Frontend
 
### Scope
 
Four pages. Adding a page requires updating this section first.
 
1. `/login` and `/signup` — auth forms
2. `/dashboard` — overview: device count, active-alert count, "activity" chart summarizing readings across all devices in the last hour
3. `/devices` (list) and `/devices/:id` (detail with time-series chart, threshold indicators, alert history)
4. Chat panel — a persistent right-side drawer available on `/dashboard` and `/devices/:id` (not a separate route)
Explicitly out of scope for v1: user settings, notification preferences, light-mode toggle, mobile-optimized layouts, entrance/scroll animations, custom-designed components, dashboard customization, multi-device comparison UI, in-app help.
 
### Language and framework
 
- **TypeScript strict mode is mandatory.** `strict: true`, `noUncheckedIndexedAccess: true`, `noImplicitOverride: true` in `tsconfig.json`. No `any`. No `as unknown as X`. Use `unknown` and narrow.
- React 18 with functional components + hooks only. No class components.
- All server state through TanStack Query. `useEffect` for data fetching is not permitted.
- API types are generated from the backend OpenAPI spec via `openapi-typescript` — do not hand-maintain response type definitions.
### Design system
 
The project has a deliberate, opinionated visual identity — a "control room" aesthetic that reads confident and modern without asking you to make design decisions on the fly. Concrete choices below; treat them as the contract.
 
**Theme.** Single dark theme, warm near-black background. No light-mode toggle. Dark matches the industrial ops-center feel and halves the styling surface area.
 
**Typography.**
 
- Sans: **Inter** (400, 500, 600, 700). Load via `@fontsource-variable/inter`.
- Monospace: **JetBrains Mono**. Load via `@fontsource/jetbrains-mono`. Use for all numeric values, device IDs, timestamps, and inline code.
- Sizes: 12px table cells, 14px UI chrome, 16px body, 20px card titles, 28px page titles. All defined as Tailwind text scale utilities.
**Color tokens.** Defined once in `tailwind.config.ts`. Never inline hex codes in components — always reference the token.
 
```ts
colors: {
  bg:        '#0B0D0F',   // warm near-black — root background
  surface:   '#14171A',   // elevated cards, panels
  'surface-hover': '#1B1F23',
  border:    '#252A2F',
  text: {
    DEFAULT:   '#F3F4F6',  // primary
    secondary: '#9CA3AF',
    muted:     '#6B7280',
  },
  accent:       '#F97316', // vibrant orange — primary brand + interactive
  'accent-hover': '#EA580C',
  ok:      '#10B981',      // emerald — operating normally
  warn:    '#F59E0B',      // amber — approaching threshold
  alert:   '#EF4444',      // red — threshold exceeded
}
```
 
Rationale for the palette: warm near-black + a single bright warm accent reads as "industrial data" without falling into either the corporate-blue trap (feels generic) or the cyberpunk-neon trap (feels performative). The orange accent is close enough to a heat/warning signal to feel domain-appropriate but bright enough to feel modern.
 
**Iconography.** `lucide-react` exclusively. Stroke width 2 (default). Sizes: 16px inline with text, 20px in buttons, 24px standalone.
 
**Spacing.** Tailwind default scale, used consistently. Card padding 24px (`p-6`). Section gaps 32px (`gap-8`). Form field vertical spacing 16px (`space-y-4`). Never use arbitrary Tailwind values (`p-[23px]`).
 
**Radius.** `rounded-md` (6px) on cards, inputs, buttons. `rounded-full` on status pills and dots. `rounded-sm` on nested chips. No square corners anywhere except charts.
 
**Motion.** 150ms ease-out on hover, focus, and state transitions. No entrance animations. No scroll-triggered animations. Loading indicators use `Loader2` from lucide with `animate-spin`.
 
### Components
 
- All primitives from **shadcn/ui** — Button, Input, Card, Dialog, Tooltip, Select, Tabs, Sheet, Skeleton, DropdownMenu, Toast. Configure the shadcn theme once against the tokens above.
- Never fork the shadcn source. Compose complex UI from primitives + Tailwind.
- Prefer table + type hierarchy over cards for lists of five or more items.
### Charts (Recharts)
 
Every chart in the app follows these conventions. Do not choose chart colors ad-hoc.
 
- Primary series: stroke `accent` (`#F97316`), stroke-width 2
- Secondary series (comparisons): stroke `text-secondary` (`#9CA3AF`), stroke-width 1.5
- Max-threshold line: `alert` dashed (4 4)
- Min-threshold line: `warn` dashed (4 4)
- Grid: `border` at 20% opacity, horizontal lines only
- Axis tick labels: `text-muted`, JetBrains Mono
- Tooltip: `surface` background, top border in `accent`, rounded-md
- Interactive cursor line: `accent` at 30% opacity
- Heights: 240px in dashboard cards, 400px on device detail page
### Required states
 
Every list, table, and chart implements three states — never left as defaults.
 
- **Loading**: shadcn `Skeleton` rows/rectangles sized to the final content. Never spinners for list content (spinners only for button-scoped actions).
- **Empty**: centered lucide icon (24px, `text-muted`), a one-sentence explanation, and a primary CTA button when an action makes sense.
- **Error**: `alert`-colored inline message with a Retry button. Never use `window.alert` or unstyled browser dialogs.
### Rules for adding UI
 
- Start from a shadcn primitive; if none fits, ask before hand-building.
- Only use tokens from the color / typography / spacing scales above.
- If you need a color, font, or spacing not in the tokens, add it to the tokens *first*, then reference it.
- No inline hex codes, no `text-[14.5px]`, no `bg-[#123456]`.
- New pages require an update to `Frontend § Scope`.
---
 
## Eval plan
 
30 labeled cases in `/evals/cases.yaml`. Distribution:
 
- 12 simple recall — "show me X" / "what was Y at time Z"
- 8 aggregation — "average vibration on Pump-3 today"
- 5 multi-step — "compare Furnace-1 and Furnace-2 over the last hour"
- 3 alert-aware — "any issues with my devices in the last 24h?"
- 2 should-decline — "predict next week's failures" → expect a graceful decline
Each case specifies: the user question, expected tool-call sequence (names + argument patterns, not exact values), and a rubric for the final answer (keywords that must appear).
 
**Evals live outside the API.** The eval runner imports `run_agent` directly with a `Fake*Repository`-backed `AgentServices` and asserts on the emitted event stream. It does not go through HTTP or SSE. This is only possible because the agent module has a clean public interface — the module boundary was designed with eval ergonomics in mind.
 
CI uses recorded Anthropic responses (vcrpy-style) for determinism. A separate weekly job hits the live API to track drift.
 
Tracked metrics: tool-call precision (correct sequence), rubric pass rate, p50/p95 latency, mean cost per query, prompt version.
 
---
 
## Key decisions
 
| Decision | Choice | Reason |
|---|---|---|
| Overall shape | Modular monolith | Two-service split rejected for v1 as premature; module boundary designed for later extraction — see "Extraction path" |
| Runtime | Async throughout | asyncio all the way — AsyncEngine + AsyncSession + `async def` endpoints. SSE and the Anthropic streaming SDK both require async; unifying the runtime avoids `run_in_threadpool` bridging inside `/agent/`. Alembic uses its own sync engine (standard) |
| DB driver | psycopg3 async | Actively maintained; asyncpg is faster but psycopg3 is the safer default and works with every SQLAlchemy async example in the docs |
| Password hashing | argon2id (memory=64MiB, time=3, parallelism=4) | Memory-hard, OWASP first-choice recommendation; no 72-byte truncation footgun; `deprecated="auto"` in CryptContext enables future scheme migration |
| Database | Postgres + TimescaleDB | Hypertables speed aggregation queries 10–100×; real industry tool |
| Auth | JWT | Stateless; one project goal is to implement auth at protocol level; documented limitation (no revocation) is fine for portfolio |
| Streaming | SSE not WebSocket | One-way streaming is sufficient; simpler to deploy and debug |
| Test DB | testcontainers, not SQLite | TimescaleDB hypertables aren't in SQLite; real Postgres tests catch more bugs |
| Component library | shadcn/ui | Code I own, no theming runtime; faster than MUI for portfolio polish |
| Tool result handling | Summarize server-side to ≤2KB | Token cost predictability; keeps response shape stable |
| Agent test strategy | Direct `run_agent` calls with fake repos | Faster tests, better isolation, no HTTP layer to mock |
| Simulator placement | Third module inside the same process | v1 simplicity; can move to its own worker later |
 
---
 
## Extraction path
 
The agent module is designed so it can be extracted into its own service without invasive changes. This section records what such a split would involve, and — more importantly — the criteria that would justify doing it.
 
### Triggers that would justify the split
 
- Chat traffic dominates cost or latency SLOs, and the data API's scaling profile is being distorted by it
- Prompt/tool iteration cadence becomes high enough that redeploying the whole API for each tweak is disruptive
- An agent bug causes a data-plane incident (this is the strongest possible trigger — blast-radius argument becomes concrete)
- Team ownership divides such that different people own the agent versus the API (not applicable to a solo project, but this is the standard reason companies actually split)
Absent one of these, the split is not justified.
 
### What changes if we do split
 
1. `AgentServices` grows a new implementation: `HttpDeviceRepository`, `HttpReadingRepository`, `HttpAlertRepository` — each wraps calls to the data API over HTTP with retries and timeouts.
2. A new service (call it `agent-svc`) is stood up. It contains `/agent/` and the `/chat/*` routers. It receives requests, calls `run_agent` with `HttpAgentServices`, streams responses back.
3. Auth: the data API accepts either a user JWT (from browser) or a service token + user_id header (from agent-svc). The agent-svc receives the user JWT from the browser and either passes it through or exchanges it for a service token.
4. Observability: correlation IDs propagated in headers, distributed tracing via OpenTelemetry.
5. Local dev: docker-compose gains a fourth service.
### What does not change
 
Neither the tool implementations nor the `run_agent` function nor the event shapes change during the split. That is the entire point of the module boundary.
 
### What must not happen before the split
 
Do not fake-split — do not put HTTP calls inside `/agent/` while the module still lives in the same process. That gets the operational cost of a split without the benefit. The rule is: `/agent/` uses `AgentServices` and only `AgentServices`, always.