# Multi-stage build: wheels are compiled in a fat builder image, then only the
# installed packages are copied into a slim runtime. Keeps the final image
# small and free of compilers (a smaller attack surface).
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Build deps for asyncpg / cryptography / argon2 wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# Copy built packages into the interpreter's real prefix. Installing to a
# custom prefix and only extending PATH would leave site-packages off
# sys.path: console scripts resolve, but their imports fail.
COPY --from=builder /install /usr/local

WORKDIR /app

# Run as a non-root user: if the process is ever compromised it has no
# privileges to escalate with inside the container.
RUN groupadd --system app && useradd --system --gid app --home /app app

COPY --chown=app:app . .

USER app

EXPOSE 8000

# Container-level liveness. Compose adds its own healthcheck; this makes the
# image self-describing when run outside compose too.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
