# Multi-stage build: uv installs deps into a venv in the build stage; the runtime stage
# copies only that venv, so no uv, no build tools, no source tree in the final image.

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first: this layer is cached until uv.lock changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.12-slim
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
# Numeric UID so Kubernetes' runAsNonRoot can verify it without an /etc/passwd entry.
USER 10001
ENTRYPOINT ["kairos"]
