# syntax=docker/dockerfile:1

# uv-managed image pinned to the project's Python (3.13).
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# Which optional providers to install (openai + litellm by default).
# Override at build time, e.g. --build-arg EXTRAS="--extra all --extra bertopic".
ARG EXTRAS="--extra all"

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# 1) Install dependencies in a cached layer (no project source yet).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project ${EXTRAS}

# 2) Copy the project and install it (the conference-analyzer entry point).
COPY . /app
COPY --link .env* /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen ${EXTRAS}

ENV PATH="/app/.venv/bin:${PATH}"

# Run as a non-root user; keep the on-disk cache on a persistable path.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /home/app/.cache/conflens \
    && chown -R app:app /app /home/app
USER app
ENV HOME=/home/app

EXPOSE 8080

# ENTRYPOINT + CMD: default launches the server; `docker run <image> --clear-cache`
# (or any other flags) is appended to `conference-analyzer`.
ENTRYPOINT ["conference-analyzer"]
CMD ["--host", "0.0.0.0", "--port", "8080"]
