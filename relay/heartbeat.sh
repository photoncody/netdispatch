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
  body=$(printf '{"relay_id":"%s","name":"%s","ip_address":"%s","subnet":"%s","interface":"%s","version":"%s","dnsmasq_ok":%s}' \
    "$(json_escape "$RELAY_ID")" "$(json_escape "$RELAY_NAME")" "$(json_escape "${ip_now:-$RELAY_IP}")" \
    "$(json_escape "$SUBNET_DISPLAY")" "$(json_escape "$RELAY_INTERFACE")" \
    "$(json_escape "${RELAY_VERSION:-dev}")" "$dns_ok")

  # shellcheck disable=SC2086
  code=$(curl -sS -o /tmp/heartbeat.out -w '%{http_code}' -m 10 $CURL_OPTS \
    -H "Content-Type: application/json" -H "X-Relay-Key: ${RELAY_API_KEY}" \
    -X POST --data "$body" "$URL" 2>/tmp/heartbeat.err) || code="000"

  if [ "$code" = "200" ]; then
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
