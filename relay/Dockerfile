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

# ------------------------------------------- signed iPXE for UEFI Secure Boot
# Used by SECURE_BOOT=true. The iPXE project's shim is signed by Microsoft
# (UEFI CA 2011 and 2023) and loads the iPXE-signed ipxe.efi / snponly.efi
# that sits next to it: <name>-shimx64.efi loads <name>.efi. A signed binary
# can't carry our embedded script, so embed.ipxe is shipped beside it as
# autoexec.ipxe, which iPXE fetches from its own TFTP directory at start-up.
# These are prebuilt x86_64 files, so the stage is the same on every platform.
FROM --platform=$BUILDPLATFORM alpine:3.22 AS ipxe-signed
ARG IPXE_SIGNED_VERSION=2.0.0
ARG IPXE_SIGNED_SHA256=01a526d4cc791fc30362259c609d6c506cc64a7bdff51b9a5eb788354e17eee1
ARG IPXE_SHIM_VERSION=16.1
ARG IPXE_SHIM_SHA256=5eecca2780bd49c900565e124516a1bd666ec5e012825f34991b6ba1ef2fa6cf
RUN set -eu; \
    apk add --no-cache curl; \
    mkdir -p /out/secureboot /tmp/sb; cd /tmp/sb; \
    curl -fsSL -o ipxeboot.tar.gz \
      "https://github.com/ipxe/ipxe/releases/download/v${IPXE_SIGNED_VERSION}/ipxeboot.tar.gz"; \
    echo "${IPXE_SIGNED_SHA256}  ipxeboot.tar.gz" | sha256sum -c -; \
    tar -xzf ipxeboot.tar.gz; \
    cp ipxeboot/x86_64-sb/ipxe.efi ipxeboot/x86_64-sb/snponly.efi /out/secureboot/; \
    curl -fsSL -o /out/secureboot/ipxe-shimx64.efi \
      "https://github.com/ipxe/shim/releases/download/ipxe-${IPXE_SHIM_VERSION}/ipxe-shimx64.efi"; \
    echo "${IPXE_SHIM_SHA256}  /out/secureboot/ipxe-shimx64.efi" | sha256sum -c -; \
    cp /out/secureboot/ipxe-shimx64.efi /out/secureboot/snponly-shimx64.efi; \
    rm -rf /tmp/sb; \
    ls -l /out/secureboot
COPY ipxe/embed.ipxe /out/secureboot/autoexec.ipxe

# ----------------------------------------------------------------- relay
FROM alpine:3.22
RUN apk add --no-cache dnsmasq curl iproute2 tini ca-certificates \
      jq lighttpd samba-server samba-common-tools tzdata
COPY certs/ /usr/local/share/ca-certificates/netdispatch/
RUN find /usr/local/share/ca-certificates/netdispatch -type f ! -name '*.crt' -delete \
 && update-ca-certificates 2>/dev/null || true
COPY --from=ipxe /out/ /tftp/
COPY --from=ipxe-signed /out/ /tftp/
COPY entrypoint.sh heartbeat.sh cache-sync.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/entrypoint.sh /usr/local/bin/heartbeat.sh /usr/local/bin/cache-sync.sh /tftp/secureboot \
 && find /tftp -type f -exec chmod 0644 {} + \
 && mkdir -p /run/netdispatch
ENV RELAY_VERSION=0.1.0
ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/entrypoint.sh"]
