"""Cross-origin access for a frontend served from another domain.

The browser blocks a page on one origin (for example a Vercel deployment) from
reading responses from another (the API) unless the API opts in with CORS
headers. Locally this never comes up: the Vite dev server proxies `/api`, so the
browser only ever talks to one origin.

The allowlist is explicit, never `*`. Requests carry the JWT in an
`Authorization` header rather than a cookie (CLAUDE.md: never cookies), so
credentials mode stays off and no cookie is ever sent cross-origin.
"""

from collections.abc import Sequence

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def configure_cors(app: FastAPI, origins: Sequence[str]) -> None:
    """Allow `origins` to call the API from the browser. No-op when empty."""
    if not origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
        # Browsers cache a successful preflight for this many seconds, so a page
        # does not send an OPTIONS request before every authenticated call.
        max_age=600,
    )
