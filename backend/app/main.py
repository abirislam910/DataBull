"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.api import auth, devices, readings
from app.core.errors import register_exception_handlers

app = FastAPI(title="Sensor Telemetry Platform")

# Installed before the routers so every error — ours, Pydantic's validation
# failures, and framework 404s alike — leaves the API in the shape SPEC.md
# documents: {"detail", "code", "field"?}.
register_exception_handlers(app)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(readings.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
