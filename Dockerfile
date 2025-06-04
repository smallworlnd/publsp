FROM python:3.12-slim AS base
ARG  DEV=0
ENV  POETRY_VERSION=2.1.1 \
  PYTHONUNBUFFERED=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  PIP_NO_CACHE_DIR=off \
  PIP_DISABLE_PIP_VERSION_CHECK=on \
  PIP_DEFAULT_TIMEOUT=100 \
  POETRY_NO_INTERACTION=1 \
  POETRY_VIRTUALENVS_IN_PROJECT=1 \
  POETRY_VIRTUALENVS_CREATE=1 \
  POETRY_CACHE_DIR=/tmp/poetry_cache \
  VIRTUAL_ENV=/app/.venv \
  PATH="/app/.venv/bin:$PATH"


FROM base AS builder
RUN apt update && \
  apt install build-essential pkg-config -y && \
  pip install "poetry==$POETRY_VERSION"
WORKDIR /app
COPY poetry.lock pyproject.toml ./
RUN --mount=type=cache,target=$POETRY_CACHE_DIR \
  [ "$DEV" -eq 1 ] && poetry install --with dev --no-root || poetry install --without dev --no-root


FROM base AS runtime
ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"
COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}
WORKDIR /app
COPY . .
ENTRYPOINT ["dumb-init", "--", "python", "-m", "publsp.main"]
