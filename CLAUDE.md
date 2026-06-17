# CLAUDE.md

*Starter version. Copy into your new repo as `/CLAUDE.md` at the root. This file orients Claude Code at session start — every prompt is grounded in it. Edit it as the project evolves; outdated conventions in this file produce worse code than no file at all.*

---

This file orients Claude Code at the start of every session. Read it before making any change. If conventions here conflict with what you find in the repo, follow the repo and ask before changing patterns.

## Project

Sensor Telemetry Platform — a FastAPI backend with PostgreSQL+TimescaleDB plus a React+TypeScript frontend, including an AI operator's assistant powered by Claude with tool calling. The full specification is at `/docs/SPEC.md`. **Always read SPEC.md before implementing a new endpoint, model, or tool.**

## Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0 (typed `Mapped[...]` syntax), Alembic, Pydantic v2, python-jose, passlib[bcrypt], APScheduler, anthropic SDK
- **Database**: PostgreSQL 16 with the TimescaleDB extension
- **Frontend**: Vite, React 18, TypeScript (strict), Tailwind, shadcn/ui, TanStack Query, Recharts
- **Tests**: pytest, pytest-asyncio, httpx, testcontainers-python
- **CI**: GitHub Actions
- **Local infra**: Docker Compose

## Repo structure

```
/backend
  /app
    /api          # FastAPI routers, one file per resource
    /core         # config, security, dependencies
    /db           # session factory, declarative base
    /models       # SQLAlchemy models
    /schemas      # Pydantic models (kept separate from ORM)
    /services     # business logic — all DB writes live here
    /agent        # AI agent: tool definitions, system prompt, runner
    main.py
  /alembic
  /tests
  pyproject.toml
/frontend
  /src
    /pages
    /components
    /lib          # api client, auth context, query setup
  package.json
/evals
  cases.yaml
  runner.py
/docs
  SPEC.md
  SIMULATOR.md
docker-compose.yml
.github/workflows/
```

## Commands

```bash
# Backend
docker compose up                       # start API + DB locally
cd backend && pytest                    # run tests (uses testcontainers)
cd backend && pytest -k <name>          # single test
cd backend && ruff check . --fix        # lint + autofix
cd backend && mypy app                  # type-check (strict)
cd backend && alembic revision --autogenerate -m "..."
cd backend && alembic upgrade head

# Frontend
cd frontend && pnpm dev                 # dev server on 5173
cd frontend && pnpm test                # vitest
cd frontend && pnpm lint                # eslint
cd frontend && pnpm typecheck           # tsc --noEmit

# Evals
pytest evals/                           # run agent eval suite
```

## Conventions

### Python

- SQLAlchemy 2.0 typed syntax everywhere: `class User(Base): id: Mapped[UUID] = mapped_column(primary_key=True)`. Never legacy `Column(...)` at class body.
- Pydantic v2: `model_config = ConfigDict(...)`, not the inner `class Config:` style.
- FastAPI dependencies via `Annotated[Type, Depends(...)]`, not bare `Depends`.
- Type hints on every function signature. `mypy --strict` must pass.
- No `from typing import Optional`. Use `X | None`.
- `ruff` for both lint and format. No `black`, no `isort` separately.
- Module names snake_case, class names PascalCase, function and variable names snake_case.
- Constants UPPER_SNAKE.

### TypeScript

- `strict: true` in tsconfig. No `any` — use `unknown` and narrow.
- Functional components with hooks only. No class components.
- Component-local types/interfaces colocated with the component; shared types in `/src/lib/types.ts`.
- TanStack Query for all server state. **Never** use `useEffect` for data fetching.
- Tailwind classes inline. Use shadcn primitives via composition; don't fork them.

### API design

- Plural resource names (`/devices`, `/readings`).
- Query parameters for filtering, never path parameters.
- All times in UTC and ISO 8601 in request and response bodies.
- Error shape: `{"detail": "...", "code": "machine_readable_string", "field"?: "field_name"}`.

### Database

- Migrations are reviewed before commit. **Never run `alembic --autogenerate` without inspecting the diff** — autogenerate misses constraints, hypertable definitions, and enum changes.
- The `readings` table is a TimescaleDB hypertable. Its migration must explicitly call `create_hypertable('readings', 'time')` via `op.execute(...)`.
- No raw SQL in routers. Push it to `/services/`.
- Consider an index for every column used in a WHERE clause.

### Tests

- Real Postgres via `testcontainers-python`. **Never** SQLite. **Never** mock the database.
- Unit tests for pure functions; integration tests for routers (full HTTP request → response).
- Standard fixtures: `db_session`, `client`, `authed_client`, factory-built `user`, `device`, `reading`.
- Test behavior, not implementation. Don't assert that one method called another method.
- Every new feature ships with its tests in the same PR.

### Auth

- JWT in `Authorization: Bearer <token>` header. Never cookies for this project.
- Frontend stores the token in a React context, in memory only. Document this in README — chosen to protect against XSS exfiltration at the cost of requiring re-login on tab reload.
- Protected routes use `current_user: Annotated[User, Depends(get_current_user)]` injected as the last parameter.

### Git

- Branch per task. Naming: `feat/...`, `fix/...`, `chore/...`, `docs/...`.
- Commit messages lead with a verb in imperative mood: "Add JWT middleware", not "auth stuff" or "added auth".
- All commits and PRs reviewed by engineer first

## Working with Claude Code on this repo

- Always read `/docs/SPEC.md` before implementing or changing an endpoint, model, or tool.
- One logical change per session and per PR. If a task feels bigger than that, propose a breakdown first and wait for confirmation.
- Write or update tests in the same change set as the code. Run them and show output before claiming done.
- For schema changes, generate the Alembic migration but never run `alembic upgrade head` without showing the SQL first.
- For new endpoints, also update `/docs/SPEC.md` if the API surface changes — the spec is the contract.
- When stuck, propose two options with explicit tradeoffs rather than guessing or asking an open-ended question.

## Things to never do

- Never commit changes before review from engineer, only stage them for review
- Never disable `mypy --strict` or add a global `# type: ignore` to make CI green. Fix the types.
- Never use `Any`, `# type: ignore`, or `as unknown as ...` without a comment explaining why.
- Never store the JWT in `localStorage` or `document.cookie`.
- Never write tests after a feature has been merged.
- Never use `console.log` or `print` in committed code. Use the configured logger.
- Never bypass branch protection. CI must be green before merge.
- Never make Anthropic API calls in tests without a recorded fixture — costs and determinism both matter.
