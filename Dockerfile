# syntax=docker/dockerfile:1.7
FROM golang:1.26.5-bookworm AS flight-goat-build
ARG FLIGHT_GOAT_COMMIT=854c0465aaa9c275485338c2be7ef0bcaddc4e89
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    GOBIN=/out go install \
    "github.com/mvanhorn/printing-press-library/library/travel/flight-goat/cmd/flight-goat-pp-cli@${FLIGHT_GOAT_COMMIT}"

FROM python:3.12-slim-bookworm AS runtime
ARG APP_VERSION=0.2.0
ARG APP_UID=1000
ARG APP_GID=1000
LABEL org.opencontainers.image.title="AutoFly" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    AUTOFLY_CONFIG=/etc/autofly/config.yaml \
    AUTOFLY_DATABASE_PATH=/var/lib/autofly/autofly.db \
    AUTOFLY_LOCK_PATH=/var/lib/autofly/autofly.lock
RUN groupadd --gid "${APP_GID}" autofly \
    && useradd --uid "${APP_UID}" --gid autofly --home /var/lib/autofly --no-log-init autofly \
    && mkdir -p /etc/autofly /var/lib/autofly \
    && chown autofly:autofly /var/lib/autofly
COPY --from=flight-goat-build /out/flight-goat-pp-cli /usr/local/bin/flight-goat-pp-cli
WORKDIR /opt/autofly
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir ".[web]"
USER autofly
VOLUME ["/var/lib/autofly"]
ENTRYPOINT ["autofly"]
CMD ["doctor"]
