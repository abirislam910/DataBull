"""FastAPI application entrypoint.

Placeholder scaffold — routers, lifespan, and middleware to be added per /docs/SPEC.md.
"""

from fastapi import FastAPI

app = FastAPI(title="Sensor Telemetry Platform")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
