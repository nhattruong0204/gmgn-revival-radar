FROM python:3.12-slim-bookworm AS builder
ARG PIP_NO_INDEX
ENV PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements.lock requirements-build.lock ./
# Optional verified wheel cache for environments whose Docker daemon has no DNS.
COPY .wheelhouse /wheel-cache
RUN pip download --find-links=/wheel-cache --require-hashes -r requirements.lock -d /wheels && \
    pip install --find-links=/wheel-cache --require-hashes -r requirements-build.lock
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip wheel --no-deps --no-build-isolation . -w /wheels

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock ./
COPY assets /app/assets
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels --require-hashes -r requirements.lock && \
    pip install --no-index --no-deps /wheels/gmgn_revival_radar-*.whl && \
    rm -rf /wheels && useradd --uid 10001 --create-home radar && \
    mkdir -p /app/data && chown radar:radar /app/data
USER radar
CMD ["revival-radar", "run"]
