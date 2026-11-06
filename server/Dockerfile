# syntax=docker/dockerfile:1
#
# netdispatch central server (hub)
#   target "runtime" (default, last stage) - production image
#   target "test"                          - runtime + pytest:  docker build --target test server/

# ---------------------------------------------------------------- assets
FROM --platform=$BUILDPLATFORM debian:bookworm-slim AS assets
ARG BUILDARCH
ARG TAILWIND_VERSION=3.4.17
ARG ALPINEJS_VERSION=3.14.8
ARG ALPINEJS_SHA256=b600e363d99d95444db54acbfb2deffec9ae792aa99a09229bcda078e5b55643
ARG WIMBOOT_VERSION=2.9.0
ARG WIMBOOT_SHA256=5f067ccdc4d084d5bf77b6c853bd0f8402dfc2b4cd1b103d358993ae97fae8e3
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
RUN case "$BUILDARCH" in arm64) tw=arm64 ;; *) tw=x64 ;; esac \
 && curl -fsSL -o /usr/local/bin/tailwindcss \
      "https://github.com/tailwindlabs/tailwindcss/releases/download/v${TAILWIND_VERSION}/tailwindcss-linux-${tw}" \
 && chmod +x /usr/local/bin/tailwindcss
RUN mkdir -p /out \
 && curl -fsSL -o /out/alpine.min.js "https://cdn.jsdelivr.net/npm/alpinejs@${ALPINEJS_VERSION}/dist/cdn.min.js" \
 && echo "${ALPINEJS_SHA256}  /out/alpine.min.js" | sha256sum -c - \
 && curl -fsSL -o /out/wimboot "https://github.com/ipxe/wimboot/releases/download/v${WIMBOOT_VERSION}/wimboot" \
 && echo "${WIMBOOT_SHA256}  /out/wimboot" | sha256sum -c -
WORKDIR /src
COPY tailwind.config.js ./
COPY tailwind ./tailwind
COPY app/templates ./app/templates
RUN tailwindcss -c tailwind.config.js -i tailwind/input.css -o /out/app.css --minify

# ---------------------------------------------------------------- base
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data
RUN apt-get update \
 && apt-get install -y --no-install-recommends 7zip syslinux-common \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /opt/netdispatch \
 && cp /usr/lib/syslinux/memdisk /opt/netdispatch/memdisk \
 && (command -v 7zz || command -v 7z) \
 && groupadd --system --gid 1000 netdispatch \
 && useradd --system --uid 1000 --gid 1000 --home-dir /data --shell /usr/sbin/nologin netdispatch
WORKDIR /srv
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY app ./app
COPY --from=assets /out/app.css /out/alpine.min.js ./app/static/
COPY --from=assets /out/wimboot /opt/netdispatch/wimboot
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

# ---------------------------------------------------------------- test
FROM base AS test
COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY tests ./tests
CMD ["python", "-m", "pytest", "-q", "tests"]

# ---------------------------------------------------------------- runtime
FROM base AS runtime
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
