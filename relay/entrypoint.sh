#!/bin/sh
# netdispatch relay entrypoint
#
# 1. validate configuration and auto-detect interface / subnet
# 2. generate /tftp/netdispatch.ipxe (chains to the hub's /boot endpoint)
# 3. generate /etc/dnsmasq.d/netdispatch.conf (ProxyDHCP + TFTP, no DNS)
# 4. start the heartbeat loop in the background and run dnsmasq in the foreground
set -eu

log() { echo "[netdispatch-relay] $*"; }
die() { log "ERROR: $*"; exit 1; }

: "${CENTRAL_SERVER_URL:?CENTRAL_SERVER_URL is required (e.g. https://pxe.example.com)}"
: "${RELAY_ID:?RELAY_ID is required (e.g. site-office-vlan20)}"
: "${RELAY_API_KEY:?RELAY_API_KEY is required (create the relay in the netdispatch UI)}"
RELAY_NAME="${RELAY_NAME:-$RELAY_ID}"
RELAY_INTERFACE="${RELAY_INTERFACE:-}"
SUBNET_RANGE="${SUBNET_RANGE:-}"
UEFI_BOOTFILE="${UEFI_BOOTFILE:-ipxe.efi}"
BIOS_BOOTFILE="${BIOS_BOOTFILE:-undionly.kpxe}"
RELAY_LOG_DHCP="${RELAY_LOG_DHCP:-true}"
HEARTBEAT_INTERVAL="${HEARTBEAT_INTERVAL:-30}"
# chainload: clients that already run some other iPXE (NIC ROMs, VM firmware)
#            are also given netdispatch's iPXE build (HTTPS + trusted CAs).
# direct:    such clients skip the binary and fetch netdispatch.ipxe directly.
IPXE_CLIENTS="${IPXE_CLIENTS:-chainload}"
# true: UEFI x86_64 clients get the iPXE project's Microsoft-signed shim and
#       signed iPXE (secureboot/ in the TFTP root) instead of our own build,
#       so they boot with UEFI Secure Boot enabled.
SECURE_BOOT="${SECURE_BOOT:-false}"
# true: keep local copies of the ISOs the hub assigns to this relay and serve
#       them on this LAN (HTTP for iPXE, SMB for Windows Setup). See cache-sync.sh.
RELAY_CACHE="${RELAY_CACHE:-false}"
CACHE_DIR="${CACHE_DIR:-/cache}"
CACHE_HTTP_PORT="${CACHE_HTTP_PORT:-8080}"
CACHE_BIND="${CACHE_BIND:-}"            # default: the relay's IP on RELAY_INTERFACE
CACHE_URL="${CACHE_URL:-}"              # default: http://<relay ip>:CACHE_HTTP_PORT
CACHE_SMB="${CACHE_SMB:-true}"
CACHE_RATE_LIMIT="${CACHE_RATE_LIMIT:-}"
CACHE_SYNC_WINDOW="${CACHE_SYNC_WINDOW:-}"
CENTRAL_SERVER_URL="${CENTRAL_SERVER_URL%/}"
CACHE_DIR="${CACHE_DIR%/}"

case "$IPXE_CLIENTS" in
  chainload|direct) ;;
  *) die "IPXE_CLIENTS must be 'chainload' or 'direct' (got '$IPXE_CLIENTS')" ;;
esac
case "$SECURE_BOOT" in
  true|false) ;;
  *) die "SECURE_BOOT must be 'true' or 'false' (got '$SECURE_BOOT')" ;;
esac
if [ "$SECURE_BOOT" = "true" ]; then
  # The shim loads the signed iPXE named after it: ipxe-shimx64.efi -> ipxe.efi.
  case "$UEFI_BOOTFILE" in
    ipxe.efi|snponly.efi) UEFI_BOOTFILE="secureboot/${UEFI_BOOTFILE%.efi}-shimx64.efi" ;;
    secureboot/*) ;;
    *) die "with SECURE_BOOT=true, UEFI_BOOTFILE must be ipxe.efi or snponly.efi (got '$UEFI_BOOTFILE')" ;;
  esac
  case "$CENTRAL_SERVER_URL" in
    https://*)
      if ls /usr/local/share/ca-certificates/netdispatch/*.crt >/dev/null 2>&1; then
        log "WARNING: SECURE_BOOT=true uses the iPXE project's signed iPXE, which does not trust"
        log "         the CA certificates in relay/certs. UEFI clients can only reach an https hub"
        log "         whose certificate chains to a public CA; otherwise use an http:// hub URL."
      fi
      ;;
  esac
fi

case "$RELAY_CACHE" in
  true|false) ;;
  *) die "RELAY_CACHE must be 'true' or 'false' (got '$RELAY_CACHE')" ;;
esac
if [ "$RELAY_CACHE" = "true" ]; then
  case "$CACHE_SMB" in
    true|false) ;;
    *) die "CACHE_SMB must be 'true' or 'false' (got '$CACHE_SMB')" ;;
  esac
  # lighttpd lower-cases request paths (force-lowercase-filenames) and the
  # relay stores everything in lower case, so CACHE_DIR must be lower case too.
  echo "$CACHE_DIR" | grep -Eq '^/[a-z0-9._/-]+$' \
    || die "CACHE_DIR must be an absolute path of lowercase letters, digits, '.', '_', '-' (got '$CACHE_DIR')"
  echo "$CACHE_HTTP_PORT" | grep -Eq '^[0-9]{1,5}$' && [ "$CACHE_HTTP_PORT" -ge 1 ] && [ "$CACHE_HTTP_PORT" -le 65535 ] \
    || die "CACHE_HTTP_PORT must be a TCP port number (got '$CACHE_HTTP_PORT')"
  [ -z "$CACHE_RATE_LIMIT" ] || echo "$CACHE_RATE_LIMIT" | grep -Eq '^[0-9]+[KkMmGg]?$' \
    || die "CACHE_RATE_LIMIT must look like 500K, 20M or 1G (got '$CACHE_RATE_LIMIT')"
  [ -z "$CACHE_SYNC_WINDOW" ] || echo "$CACHE_SYNC_WINDOW" | grep -Eq '^([01][0-9]|2[0-3]):[0-5][0-9]-([01][0-9]|2[0-3]):[0-5][0-9]$' \
    || die "CACHE_SYNC_WINDOW must be HH:MM-HH:MM, e.g. 22:00-06:00 (got '$CACHE_SYNC_WINDOW')"
  [ -z "$CACHE_URL" ] || echo "$CACHE_URL" | grep -Eq '^http://[A-Za-z0-9.-]+(:[0-9]{1,5})?/?$' \
    || die "CACHE_URL must be http://host[:port] with no path (got '$CACHE_URL'); clients fetch over plain HTTP"
  CACHE_URL="${CACHE_URL%/}"
  [ -z "$CACHE_BIND" ] || echo "$CACHE_BIND" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' \
    || die "CACHE_BIND must be an IPv4 address of this host, or 0.0.0.0 (got '$CACHE_BIND')"
  mkdir -p "$CACHE_DIR" || die "cannot create CACHE_DIR $CACHE_DIR"
  mountpoint -q "$CACHE_DIR" 2>/dev/null \
    || log "WARNING: $CACHE_DIR is not a volume - the cache is lost when the container is recreated"
fi

echo "$RELAY_ID" | grep -Eq '^[a-z0-9][a-z0-9._-]{0,62}$' \
  || die "RELAY_ID must be lowercase letters, digits, '.', '_' or '-' (got '$RELAY_ID')"
case "$CENTRAL_SERVER_URL" in
  http://*|https://*) ;;
  *) die "CENTRAL_SERVER_URL must start with http:// or https://" ;;
esac
for f in "$UEFI_BOOTFILE" "$BIOS_BOOTFILE"; do
  [ -f "/tftp/$f" ] || die "boot file /tftp/$f does not exist (available: $(cd /tftp && echo *))"
done

# --- interface & subnet ----------------------------------------------------
if [ -z "$RELAY_INTERFACE" ]; then
  RELAY_INTERFACE="$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
  [ -n "$RELAY_INTERFACE" ] || die "could not detect the default-route interface; set RELAY_INTERFACE"
  log "auto-detected interface: $RELAY_INTERFACE"
fi
ip link show "$RELAY_INTERFACE" >/dev/null 2>&1 \
  || die "interface '$RELAY_INTERFACE' not found - is the container running with network_mode: host?"

CIDR="$(ip -4 -o addr show dev "$RELAY_INTERFACE" | awk '{print $4; exit}')"
[ -n "$CIDR" ] || die "interface $RELAY_INTERFACE has no IPv4 address"
RELAY_IP="${CIDR%/*}"
PREFIX="${CIDR#*/}"
NETWORK="$(ipcalc -n "$CIDR" | cut -d= -f2)"
NETMASK="$(ipcalc -m "$CIDR" | cut -d= -f2)"

if [ -z "$SUBNET_RANGE" ]; then
  SUBNET_RANGE="${NETWORK},proxy,${NETMASK}"
  log "auto-detected subnet: ${NETWORK}/${PREFIX}"
else
  case "$SUBNET_RANGE" in
    *,proxy*) ;;
    *) SUBNET_RANGE="${SUBNET_RANGE},proxy" ;;
  esac
fi
SUBNET_DISPLAY="$(echo "$SUBNET_RANGE" | cut -d, -f1)/${PREFIX}"

CACHE_BIND="${CACHE_BIND:-$RELAY_IP}"
CACHE_URL="${CACHE_URL:-http://${RELAY_IP}:${CACHE_HTTP_PORT}}"

export RELAY_INTERFACE RELAY_IP SUBNET_DISPLAY RELAY_NAME HEARTBEAT_INTERVAL CENTRAL_SERVER_URL
export RELAY_CACHE CACHE_DIR CACHE_HTTP_PORT CACHE_BIND CACHE_URL CACHE_SMB CACHE_RATE_LIMIT CACHE_SYNC_WINDOW

# --- boot script served over TFTP ---------------------------------------------
cat > /tftp/netdispatch.ipxe <<EOF
#!ipxe
# Generated by the netdispatch relay '${RELAY_ID}' at startup.
# The hub's scripts only return here when it says "boot locally" (they set
# nd-local); we then exit with a failure status so UEFI/BIOS firmware moves
# on to the next boot entry. Any other return means the hub was unreachable.
set nd-hub ${CENTRAL_SERVER_URL}
set nd-relay ${RELAY_ID}
:hub
echo netdispatch :: contacting \${nd-hub} via relay \${nd-relay}
chain --autofree \${nd-hub}/boot?mac=\${netX/mac:hexhyp}&ip=\${netX/ip}&relay=\${nd-relay}&arch=\${buildarch}&platform=\${platform} || goto hub_done
:hub_done
isset \${nd-local} && exit 1 || goto hub_failed
:hub_failed
echo netdispatch :: hub unreachable or returned an error - retrying in 10 seconds (Ctrl-C for a shell)
sleep 10 || shell
goto hub
EOF
chmod 0644 /tftp/netdispatch.ipxe

# --- dnsmasq ----------------------------------------------------------------------
mkdir -p /etc/dnsmasq.d
CONF=/etc/dnsmasq.d/netdispatch.conf
{
  echo "# Generated by the netdispatch relay entrypoint - do not edit."
  echo "port=0"                        # no DNS server
  echo "interface=${RELAY_INTERFACE}"
  echo "bind-interfaces"
  echo "log-facility=-"
  if [ "$RELAY_LOG_DHCP" = "true" ]; then echo "log-dhcp"; fi
  echo "# ProxyDHCP: the existing DHCP server keeps handing out leases"
  echo "dhcp-range=${SUBNET_RANGE}"
  echo "enable-tftp"
  echo "tftp-root=/tftp"
  echo
  echo "# PXE boot menu entries; the client architecture (DHCP option 93) selects one."
  echo "pxe-prompt=\"netdispatch\",0"
  # Which clients that already run iPXE (DHCP user class "iPXE" / option 175)
  # are sent straight to netdispatch.ipxe instead of being given a binary:
  #   IPXE_CLIENTS=direct  all of them.
  #   SECURE_BOOT=true     UEFI ones too. The signed iPXE normally runs
  #                        secureboot/autoexec.ipxe and never looks at the DHCP
  #                        filename, but if that download fails it falls back
  #                        to DHCP; handing it the shim again would nest forever.
  #   otherwise            none: netdispatch's own iPXE build ignores the DHCP
  #                        filename (embedded script), so chainloading can't loop.
  bios_direct=false; uefi_direct=false
  [ "$IPXE_CLIENTS" = "direct" ] && { bios_direct=true; uefi_direct=true; }
  [ "$SECURE_BOOT" = "true" ] && uefi_direct=true
  if [ "$bios_direct" = "true" ] || [ "$uefi_direct" = "true" ]; then
    echo "dhcp-userclass=set:ipxe,iPXE"
    echo "dhcp-match=set:ipxe,175"
  fi
  if [ "$bios_direct" = "true" ]; then
    echo "pxe-service=tag:!ipxe,x86PC,\"netdispatch (BIOS)\",${BIOS_BOOTFILE}"
    echo "pxe-service=tag:ipxe,x86PC,\"netdispatch\",netdispatch.ipxe"
  else
    echo "pxe-service=x86PC,\"netdispatch (BIOS)\",${BIOS_BOOTFILE}"
  fi
  for csa in X86-64_EFI BC_EFI; do
    if [ "$uefi_direct" = "true" ]; then
      echo "pxe-service=tag:!ipxe,${csa},\"netdispatch (UEFI)\",${UEFI_BOOTFILE}"
      echo "pxe-service=tag:ipxe,${csa},\"netdispatch\",netdispatch.ipxe"
    else
      echo "pxe-service=${csa},\"netdispatch (UEFI)\",${UEFI_BOOTFILE}"
    fi
  done
} > "$CONF"

log "relay '${RELAY_ID}' (${RELAY_NAME})"
log "  interface ${RELAY_INTERFACE}  ip ${RELAY_IP}  proxy-dhcp ${SUBNET_RANGE}"
log "  hub ${CENTRAL_SERVER_URL}  uefi=${UEFI_BOOTFILE} bios=${BIOS_BOOTFILE} ipxe-clients=${IPXE_CLIENTS} secure-boot=${SECURE_BOOT}"
dnsmasq --test --conf-file="$CONF" >/dev/null 2>&1 || { dnsmasq --test --conf-file="$CONF"; die "invalid dnsmasq configuration"; }

# --- relay cache (RELAY_CACHE=true) -------------------------------------------
if [ "$RELAY_CACHE" = "true" ]; then
  mkdir -p /etc/lighttpd "$CACHE_DIR/tree" "$CACHE_DIR/raw"
  cat > /etc/lighttpd/netdispatch.conf <<EOF
# Generated by the netdispatch relay entrypoint - do not edit.
# Serves the relay cache read-only to PXE clients (Range requests for sanboot).
server.modules = ("mod_access", "mod_accesslog")
server.document-root = "${CACHE_DIR}"
server.bind = "${CACHE_BIND}"
server.port = ${CACHE_HTTP_PORT}
server.username = "lighttpd"
server.groupname = "lighttpd"
server.tag = "netdispatch-relay"
# Files are stored in lower case; this makes lookups case-insensitive, like
# the hub's own /files/iso/*/tree route.
server.force-lowercase-filenames = "enable"
server.follow-symlink = "disable"
url.access-deny = (".part")
mimetype.assign = ("" => "application/octet-stream")
# lighttpd drops to the lighttpd user before opening this, so it can't reopen
# the container's stderr: a FIFO read by a root loop (below) forwards it.
accesslog.filename = "/run/netdispatch/http-access.fifo"
accesslog.format = "[netdispatch-cache] http %h \"%r\" %>s %b"
EOF
  # The access-log reader must exist before anything opens the FIFO for
  # writing (lighttpd -tt included), or the open blocks.
  fifo=/run/netdispatch/http-access.fifo
  rm -f "$fifo" && mkfifo -m 0620 "$fifo" && chgrp lighttpd "$fifo"
  ( while :; do cat "$fifo" || sleep 1; done ) &
  lighttpd -tt -f /etc/lighttpd/netdispatch.conf >/dev/null 2>&1 \
    || { lighttpd -tt -f /etc/lighttpd/netdispatch.conf; die "invalid lighttpd configuration"; }
  log "  cache ${CACHE_DIR} at ${CACHE_URL} (smb=${CACHE_SMB})"
  /usr/local/bin/cache-sync.sh &
fi

/usr/local/bin/heartbeat.sh &

exec dnsmasq --keep-in-foreground --conf-file="$CONF"
