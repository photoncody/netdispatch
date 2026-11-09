#!/bin/sh
# Reports relay status to the hub every HEARTBEAT_INTERVAL seconds.
# Expects the variables exported by entrypoint.sh.
set -u

json_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

URL="${CENTRAL_SERVER_URL}/api/relays/heartbeat"
CURL_OPTS="${HEARTBEAT_CURL_OPTS:-}"
failures=0

while true; do
  ip_now="$(ip -4 -o addr show dev "$RELAY_INTERFACE" 2>/dev/null | awk '{print $4; exit}')"
  ip_now="${ip_now%/*}"
  if pidof dnsmasq >/dev/null 2>&1; then dns_ok=true; else dns_ok=false; fi
  cache="null"
  if [ "${RELAY_CACHE:-false}" = "true" ]; then
    # Written by cache-sync.sh: per-image status, progress and etag.
    items="$(cat /run/netdispatch/cache-status.json 2>/dev/null || echo '[]')"
    if pidof lighttpd >/dev/null 2>&1; then http_ok=true; else http_ok=false; fi
    if pidof smbd >/dev/null 2>&1; then smb_ok=true; else smb_ok=false; fi
    disk="$(df -Pk "$CACHE_DIR" 2>/dev/null | awk 'NR==2 {print $4 * 1024, $2 * 1024}')"
    cache=$(jq -cn --arg url "$CACHE_URL" --argjson http "$http_ok" --argjson smb "$smb_ok" \
      --arg free "${disk% *}" --arg total "${disk#* }" --argjson items "$items" \
      '{url:$url, http_ok:$http, smb_ok:$smb, items:$items,
        disk_free:($free | tonumber? // null), disk_total:($total | tonumber? // null)}' 2>/dev/null) \
      || cache="null"
  fi
  body=$(printf '{"relay_id":"%s","name":"%s","ip_address":"%s","subnet":"%s","interface":"%s","version":"%s","dnsmasq_ok":%s,"cache":%s}' \
    "$(json_escape "$RELAY_ID")" "$(json_escape "$RELAY_NAME")" "$(json_escape "${ip_now:-$RELAY_IP}")" \
    "$(json_escape "$SUBNET_DISPLAY")" "$(json_escape "$RELAY_INTERFACE")" \
    "$(json_escape "${RELAY_VERSION:-dev}")" "$dns_ok" "$cache")

  # shellcheck disable=SC2086
  code=$(curl -sS -o /tmp/heartbeat.out -w '%{http_code}' -m 10 $CURL_OPTS \
    -H "Content-Type: application/json" -H "X-Relay-Key: ${RELAY_API_KEY}" \
    -X POST --data "$body" "$URL" 2>/tmp/heartbeat.err) || code="000"

  if [ "$code" = "200" ]; then
    if [ "${RELAY_CACHE:-false}" = "true" ]; then
      # Wakes cache-sync.sh when the hub's manifest (assignments, images) changed.
      jq -r '.cache_manifest_etag // empty' /tmp/heartbeat.out > /run/netdispatch/cache-manifest-wanted 2>/dev/null
    fi
    [ "$failures" -gt 0 ] && echo "[netdispatch-relay] heartbeat restored after $failures failure(s)"
    [ "$failures" -eq 0 ] && [ ! -f /run/netdispatch/registered ] && {
      echo "[netdispatch-relay] registered with hub ${CENTRAL_SERVER_URL}"
      touch /run/netdispatch/registered
    }
    failures=0
  else
    failures=$((failures + 1))
    case "$code" in
      401) reason="invalid RELAY_API_KEY" ;;
      403) reason="relay is disabled in the hub" ;;
      000) reason="$(tr '\n' ' ' </tmp/heartbeat.err)" ;;
      *) reason="HTTP $code $(head -c 200 /tmp/heartbeat.out 2>/dev/null)" ;;
    esac
    echo "[netdispatch-relay] heartbeat failed ($failures): $reason"
  fi
  sleep "$HEARTBEAT_INTERVAL"
done
