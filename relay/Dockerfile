# syntax=docker/dockerfile:1
#
# netdispatch PXE relay: dnsmasq (ProxyDHCP + TFTP) + heartbeat agent.
# Run with network_mode: host and cap_add: [NET_ADMIN].

# ----------------------------------------------------------- iPXE builder
# Client bootloaders are always x86 binaries, so build them on the build
# platform (cross-compiling when the build host is not x86_64, e.g. a Pi).
FROM --platform=$BUILDPLATFORM debian:bookworm-slim AS ipxe
ARG IPXE_REF=v2.0.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git make perl liblzma-dev \
      gcc binutils libc6-dev \
 && if [ "$(uname -m)" != "x86_64" ]; then \
      apt-get install -y --no-install-recommends gcc-x86-64-linux-gnu binutils-x86-64-linux-gnu libc6-dev-amd64-cross; \
    fi \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${IPXE_REF}" https://github.com/ipxe/ipxe.git /ipxe
COPY ipxe/general.h /ipxe/src/config/local/general.h
COPY ipxe/embed.ipxe /ipxe/embed.ipxe
# Optional: drop PEM CA certificates into relay/certs/ to make iPXE trust a
# private CA (e.g. for an internal HTTPS reverse proxy). Without any, iPXE
# trusts the public CAs via its built-in cross-signing root.
COPY certs/ /certs/
WORKDIR /ipxe/src
RUN set -eu; \
    cross=""; [ "$(uname -m)" = "x86_64" ] || cross="CROSS=x86_64-linux-gnu-"; \
    trust="$(find /certs -maxdepth 1 -type f \( -name '*.pem' -o -name '*.crt' \) | sort | paste -sd, -)"; \
    if [ -n "$trust" ]; then echo "iPXE: trusting $trust"; trust="TRUST=$trust"; fi; \
    make -j"$(nproc)" NO_WERROR=1 $cross EMBED=/ipxe/embed.ipxe $trust \
      bin/undionly.kpxe bin-x86_64-efi/ipxe.efi bin-x86_64-efi/snponly.efi; \
    mkdir -p /out; \
    cp bin/undionly.kpxe bin-x86_64-efi/ipxe.efi bin-x86_64-efi/snponly.efi /out/; \
    ls -l /out

# ----------------------------------------------------------------- relay
FROM alpine:3.22
RUN apk add --no-cache dnsmasq curl iproute2 tini ca-certificates
COPY certs/ /usr/local/share/ca-certificates/netdispatch/
RUN find /usr/local/share/ca-certificates/netdispatch -type f ! -name '*.crt' -delete \
 && update-ca-certificates 2>/dev/null || true
COPY --from=ipxe /out/ /tftp/
COPY entrypoint.sh heartbeat.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/entrypoint.sh /usr/local/bin/heartbeat.sh \
 && chmod 0644 /tftp/* \
 && mkdir -p /run/netdispatch
ENV RELAY_VERSION=0.1.0
ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/entrypoint.sh"]
