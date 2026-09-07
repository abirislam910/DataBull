# Frontend

Vite + React 18 + TypeScript (strict), Tailwind, shadcn/ui primitives, TanStack
Query, Recharts. Single dark theme — see `/docs/SPEC.md` § Frontend for the
design contract.

```bash
npm install
npm run dev           # http://localhost:5173, proxies /api/* to :8000
npm test              # vitest
npm run typecheck     # tsc --noEmit
npm run lint          # eslint
npm run format        # prettier --write
npm run format:check  # prettier --check
npm run build         # tsc -b && vite build
```

The dev server needs the API on `:8000`. See the repo README for bringing up the
backend and database.

## CI

The `frontend` job in `.github/workflows/ci.yml` runs, in order: `npm ci`,
`npm audit --audit-level=high`, lint, format check, typecheck, tests, build. It
runs in parallel with the backend job, so a frontend change does not wait on
testcontainers pulling Postgres.

To reproduce a CI failure locally, run the same sequence — `npm ci` rather than
`npm install`, since it installs strictly from the lockfile.

Prettier ignores `src/lib/api-types.ts` and `openapi.json` (see
`.prettierignore`): both are generated, and formatting them would make the
formatter and the generator undo each other on every regeneration.

## API types are generated, never hand-written

`src/lib/api-types.ts` is produced from the backend's OpenAPI schema. Editing it
by hand would let the frontend drift from the contract silently. Regenerate in
two steps whenever the API changes:

```bash
# 1. dump the schema from the FastAPI app (no server needed)
cd backend && SECRET_KEY=x PYTHONPATH=. python -c \
  "import json; from app.main import app; print(json.dumps(app.openapi(), indent=2))" \
  > ../frontend/openapi.json

# 2. regenerate the TypeScript types
cd frontend && npm run gen:api
```

`src/lib/types.ts` re-exports those generated shapes under short names, so a
backend change surfaces as a type error at every call site rather than a runtime
surprise.

## Auth

The access token lives in React state only — never `localStorage`,
`sessionStorage`, or `document.cookie`. A tab reload loses the session and the
user signs in again; that cost is deliberate. See the repo README §
"Documented tradeoffs" and the test in `src/lib/auth.test.tsx` that asserts the
token never reaches any persistent store.

## Not yet built

The chat drawer (SPEC § Frontend § Scope, item 4) is not implemented: it depends
on `POST /chat/stream`, which does not exist yet. It ships with the agent module.
