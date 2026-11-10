# syntax=docker/dockerfile:1
#
# netdispatch central server (hub)
#   target "runtime" (default, last stage) - production image
#   target "test"                          - runtime + pytest:  docker build --target test server/
#   target "unattendgen-test"              - builds and runs the unattend generator's .NET tests

# ---------------------------------------------------------------- assets
FROM --platform=$BUILDPLATFORM debian:bookworm-slim AS assets
ARG BUILDARCH
ARG TAILWIND_VERSION=3.4.17
# From the release's sha256sums.txt; update both together with TAILWIND_VERSION.
ARG TAILWIND_SHA256_X64=7d24f7fa191d2193b78cd5f5a42a6093e14409521908529f42d80b11fde1f1d4
ARG TAILWIND_SHA256_ARM64=69b1378b8133192d7d2feb12a116fa12d035594f58db3eff215879e4ad8cf39b
ARG ALPINEJS_VERSION=3.14.8
ARG ALPINEJS_SHA256=b600e363d99d95444db54acbfb2deffec9ae792aa99a09229bcda078e5b55643
ARG WIMBOOT_VERSION=2.9.0
ARG WIMBOOT_SHA256=5f067ccdc4d084d5bf77b6c853bd0f8402dfc2b4cd1b103d358993ae97fae8e3
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
RUN case "$BUILDARCH" in \
      amd64) tw=x64;   sum="$TAILWIND_SHA256_X64" ;; \
      arm64) tw=arm64; sum="$TAILWIND_SHA256_ARM64" ;; \
      *) echo "unsupported build architecture: $BUILDARCH" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /usr/local/bin/tailwindcss \
      "https://github.com/tailwindlabs/tailwindcss/releases/download/v${TAILWIND_VERSION}/tailwindcss-linux-${tw}" \
 && echo "${sum}  /usr/local/bin/tailwindcss" | sha256sum -c - \
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

# ---------------------------------------------------------------- unattendgen
# The unattend generator CLI (unattendgen/): the upstream library at the commit pinned in
# unattendgen/upstream.lock, published as one self-contained file for the target architecture.
# The SDK image is pinned by digest; to move it, pull the tag and copy the digest from
#   docker image inspect --format '{{index .RepoDigests 0}}' mcr.microsoft.com/dotnet/sdk:10.0
# Keep the SDK version in step with the packages.lock.json files (ILLink.Tasks tracks it).
# mcr.microsoft.com/dotnet/sdk:10.0 (SDK 10.0.402 when pinned)
FROM --platform=$BUILDPLATFORM mcr.microsoft.com/dotnet/sdk:10.0@sha256:747cbade5bfd918a175ac32f8f1d931edec2c7d0d5397a3de72eaef1ac1f66d2 AS unattendgen-base
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1
WORKDIR /src
COPY unattendgen/upstream.lock unattendgen/fetch-upstream.sh ./
RUN sh fetch-upstream.sh
COPY unattendgen/global.json unattendgen/Unattendgen.slnx ./
COPY unattendgen/src/Directory.Build.props ./src/
COPY unattendgen/src/Core/Netdispatch.UnattendGen.Core.csproj unattendgen/src/Core/packages.lock.json ./src/Core/
COPY unattendgen/src/Cli/Netdispatch.UnattendGen.Cli.csproj unattendgen/src/Cli/packages.lock.json ./src/Cli/

FROM unattendgen-base AS unattendgen
# Restores for both RuntimeIdentifiers the project lists (as the lock files record), then
# publishes for the target architecture only.
RUN dotnet restore src/Cli/Netdispatch.UnattendGen.Cli.csproj --locked-mode -p:SelfContained=true
COPY unattendgen/src ./src
ARG TARGETARCH
# Not trimmed: upstream builds types by name through Newtonsoft.Json. InvariantGlobalization is
# set in Directory.Build.props because the python:3.12-slim runtime has no ICU.
RUN case "$TARGETARCH" in \
      amd64) rid=linux-x64 ;; \
      arm64) rid=linux-arm64 ;; \
      *) echo "unsupported target architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && dotnet publish src/Cli/Netdispatch.UnattendGen.Cli.csproj --no-restore -c Release -r "$rid" \
      --self-contained -p:PublishSingleFile=true -p:DebugType=none -o /out

FROM unattendgen-base AS unattendgen-test
COPY unattendgen/tests/Directory.Build.props ./tests/
COPY unattendgen/tests/Tests/Netdispatch.UnattendGen.Tests.csproj unattendgen/tests/Tests/packages.lock.json ./tests/Tests/
RUN dotnet restore Unattendgen.slnx --locked-mode
COPY unattendgen/src ./src
COPY unattendgen/tests ./tests
RUN dotnet test --solution Unattendgen.slnx --no-restore -c Release

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
COPY --from=unattendgen /out/netdispatch-unattendgen /opt/netdispatch/netdispatch-unattendgen
COPY unattendgen/THIRD-PARTY-NOTICES.md /opt/netdispatch/licenses/unattendgen-THIRD-PARTY-NOTICES.md
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

# ---------------------------------------------------------------- test
FROM base AS test
# Tests that need the real unattend generator fail instead of skipping when it is missing.
ENV NETDISPATCH_REQUIRE_UNATTENDGEN=1
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
