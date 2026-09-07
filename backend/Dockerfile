# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# Prevents .pyc write-back and forces stdout/stderr to be unbuffered — the
# latter matters specifically so `docker compose logs` shows output as it
# happens, not batched whenever Python's internal buffer flushes.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy only the dependency manifest first, so Docker's layer cache can skip
# reinstalling every package on every rebuild if app code changes but
# pyproject.toml doesn't.
COPY pyproject.toml ./

# Editable install with no source tree yet would fail, so install without -e
# here — we're building an image, not developing against live source, so
# there's nothing to make "editable" in the first place.
RUN pip install --no-cache-dir .

# Now bring in the actual application code.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# Run as a non-root user rather than the default root — a real security
# posture even for local/dev images, and a habit worth having before this
# ever approaches a production build.
RUN useradd --create-home --uid 1000 appuser

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]

# docker-compose.yml overrides this with --reload for local dev; this is the
# sane default if the image is ever run standalone (e.g. `docker run`).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]