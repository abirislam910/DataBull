"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.api import auth, chat, devices, readings
from app.core.config import get_settings
from app.core.cors import configure_cors
from app.core.errors import register_exception_handlers

app = FastAPI(title="Sensor Telemetry Platform")

# Installed before the routers so every error — ours, Pydantic's validation
# failures, and framework 404s alike — leaves the API in the shape SPEC.md
# documents: {"detail", "code", "field"?}.
register_exception_handlers(app)

# Only when CORS_ALLOW_ORIGINS is set: a frontend on another domain. The
# middleware wraps the exception handlers, so error responses such as a 401 also
# carry CORS headers, which lets the browser read the error code and sign out.
configure_cors(app, get_settings().cors_origins)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(readings.router)
app.include_router(chat.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
