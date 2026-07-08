# syntax=docker/dockerfile:1.7
#
# Image for the Nautilus Trader lab. The main.py entrypoint accepts two
# required args (-c system config, -s strategy config). They are passed
# via CMD so docker-compose can override them per service.
#
# Build:    docker build -t nautilus-lab:latest .
# Run:      docker run --rm nautilus-lab:latest -c /app/config/system/foo.yaml -s /app/config/strategies/bar.yaml

FROM python:3.14-slim

# uv from the official image (smaller than pip-installing it).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local

WORKDIR /app

# --- Layer 1: dependency manifests (cached unless they change) ---------------
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# --- Layer 2: source ---------------------------------------------------------
COPY src ./src
COPY main.py ./
COPY config ./config
RUN mkdir -p /app/logs

# Install the project itself now that src/ is present.
RUN uv sync --frozen --no-dev

# --- Runtime defaults --------------------------------------------------------
# These are overridden by docker-compose `command:` per service. Change them
# here to bake different defaults into the image at build time.
ENTRYPOINT ["uv", "run", "--no-sync", "python", "main.py"]
CMD ["-c", "/app/config/system/binance-paper-trading.yaml", \
     "-s", "/app/config/strategies/orderbook-detector.yaml"]
