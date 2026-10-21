FROM python:3.12-slim-trixie AS builder
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

FROM python:3.12-slim-trixie AS runtime
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

FROM builder AS website-python-build
COPY requirements-web.lock ./
RUN pip download --find-links=/wheel-cache --require-hashes -r requirements-web.lock -d /wheels

FROM node:22-bookworm-slim AS website-build
ARG NPM_CONFIG_OFFLINE=false
WORKDIR /build/web
COPY .npm-cache /npm-cache
COPY web/package.json web/package-lock.json ./
RUN npm ci --cache=/npm-cache
COPY web ./
RUN npm run build

FROM runtime AS website
USER root
COPY requirements-web.lock ./
COPY --from=website-python-build /wheels /web-wheels
RUN pip install --no-index --find-links=/web-wheels --require-hashes -r requirements-web.lock && \
    rm -rf /web-wheels
COPY --from=website-build /build/web/dist /app/web/dist
ENV WEB_STATIC_DIRECTORY=/app/web/dist WEB_HOST=0.0.0.0
USER radar
CMD ["revival-radar-web", "--host", "0.0.0.0", "--port", "8000"]

# Preserve the existing default scanner build and command.
FROM runtime AS scanner
