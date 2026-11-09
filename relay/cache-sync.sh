#!/bin/sh
# netdispatch relay cache (RELAY_CACHE=true): keeps local copies of the ISOs
# the hub assigned to this relay and serves them on the LAN.
#
#   * fetches the manifest from the hub (X-Relay-Key) and downloads missing or
#     changed files into $CACHE_DIR/{tree,raw}/<iso id>/ (resumable, rate
#     limited, only inside CACHE_SYNC_WINDOW), deleting what's no longer listed
#   * runs lighttpd (HTTP for iPXE / initramfs) and smbd (Windows Setup) and
#     restarts them if they die
#   * writes per-image progress to $STATE/cache-status.json for heartbeat.sh
#
# The hub only points clients here once an image is reported "ready" with the
# manifest etag it currently expects; until then they boot from the hub.
# Expects the variables exported by entrypoint.sh.
set -u

STATE=/run/netdispatch
WORK="$STATE/cache"
ITEMS="$WORK/items"
MANIFEST_URL="${CENTRAL_SERVER_URL}/api/relays/cache/manifest?relay_id=${RELAY_ID}"
CURL_OPTS="${HEARTBEAT_CURL_OPTS:-}"
IDLE_SECONDS="${CACHE_IDLE_SECONDS:-300}"
LIGHTTPD_CONF=/etc/lighttpd/netdispatch.conf
# Not smb.conf: the samba-server package ships a default one, and smbd must
# not start until the hub has sent the share settings.
SMB_CONF=/etc/samba/netdispatch.conf
TAB="$(printf '\t')"

log() { echo "[netdispatch-cache] $*"; }

mkdir -p "$ITEMS" "$CACHE_DIR/tree" "$CACHE_DIR/raw"
umask 022

# ------------------------------------------------------------------ services
ensure_http() {
  pidof lighttpd >/dev/null 2>&1 && return 0
  log "starting lighttpd on ${CACHE_BIND}:${CACHE_HTTP_PORT}"
  lighttpd -D -f "$LIGHTTPD_CONF" &
}

# $1 share, $2 user, $3 password
apply_smb() {
  [ "$CACHE_SMB" = "true" ] || return 0
  want="$(printf '%s\n%s\n%s' "$1" "$2" "$3" | sha256sum | cut -d' ' -f1)"
  have="$(cat "$WORK/smb.sum" 2>/dev/null || true)"
  if [ "$want" != "$have" ] || [ ! -f "$SMB_CONF" ]; then
    id "$2" >/dev/null 2>&1 || adduser -D -H -s /sbin/nologin "$2"
    write_smb_conf "$1" "$2"
    printf '%s\n%s\n' "$3" "$3" | smbpasswd -c "$SMB_CONF" -s -a "$2" >/dev/null || { log "smbpasswd failed"; return 1; }
    if pidof smbd >/dev/null 2>&1; then
      log "SMB share settings changed - restarting smbd"
      killall smbd 2>/dev/null
      sleep 1
    fi
    echo "$want" > "$WORK/smb.sum"
  fi
  ensure_smb
}

ensure_smb() {
  [ "$CACHE_SMB" = "true" ] && [ -f "$SMB_CONF" ] || return 0
  pidof smbd >/dev/null 2>&1 && return 0
  log "starting smbd on ${SMB_LISTEN}"
  smbd --configfile="$SMB_CONF" --foreground --debug-stdout --no-process-group &
}

# Same address as lighttpd: CACHE_BIND (default the relay's IP), or every
# address on RELAY_INTERFACE for 0.0.0.0.
if [ "$CACHE_BIND" = "0.0.0.0" ]; then SMB_LISTEN="$RELAY_INTERFACE"; else SMB_LISTEN="$CACHE_BIND"; fi

# Same hardening as the hub's samba sidecar (samba/entrypoint.sh). Files are
# stored in lower case, so Samba lower-cases incoming names instead of
# scanning directories case-insensitively.
write_smb_conf() {
  mkdir -p /var/lib/samba/private /var/log/samba /run/samba
  cat > "$SMB_CONF" <<EOF
[global]
   server role = standalone server
   workgroup = WORKGROUP
   netbios name = NDRELAY
   server string = netdispatch relay cache (read-only)
   interfaces = ${SMB_LISTEN}
   bind interfaces only = yes
   server min protocol = SMB2_10
   ntlm auth = ntlmv2-only
   map to guest = never
   restrict anonymous = 2
   disable netbios = yes
   smb ports = 445
   server signing = auto
   load printers = no
   printing = bsd
   printcap name = /dev/null
   disable spoolss = yes
   log level = 1
   unix extensions = no
   follow symlinks = no
   wide links = no

[$1]
   path = ${CACHE_DIR}/tree
   comment = netdispatch relay cache (read-only)
   read only = yes
   browseable = yes
   guest ok = no
   valid users = $2
   force user = $2
   acl allow execute always = yes
   case sensitive = yes
   default case = lower
   preserve case = no
   short preserve case = no
   veto files = /*.part/
EOF
  testparm -s "$SMB_CONF" >/dev/null 2>&1 || { testparm -s "$SMB_CONF"; log "invalid smb.conf"; }
}

# ------------------------------------------------------------------ helpers
in_window() {
  [ -n "$CACHE_SYNC_WINDOW" ] || return 0
  now=$(( 1$(date +%H%M) - 10000 ))
  from=$(( 1$(echo "$CACHE_SYNC_WINDOW" | cut -d- -f1 | tr -d :) - 10000 ))
  to=$(( 1$(echo "$CACHE_SYNC_WINDOW" | cut -d- -f2 | tr -d :) - 10000 ))
  if [ "$from" -le "$to" ]; then
    [ "$now" -ge "$from" ] && [ "$now" -lt "$to" ]
  else
    [ "$now" -ge "$from" ] || [ "$now" -lt "$to" ]
  fi
}

free_bytes() { df -Pk "$CACHE_DIR" | awk 'NR==2 {print $4 * 1024}'; }
fsize() { stat -c %s "$1" 2>/dev/null || echo 0; }

# $1 id, $2 status, $3 etag, $4 done, $5 total, $6 message
set_item() {
  jq -cn --argjson id "$1" --arg st "$2" --arg etag "$3" --argjson bdone "$4" --argjson total "$5" --arg msg "$6" \
    '{iso_id:$id, status:$st, etag:(if $etag == "" then null else $etag end),
      bytes_done:$bdone, bytes_total:$total, message:(if $msg == "" then null else $msg end)}' \
    > "$ITEMS/$1.json.tmp" && mv "$ITEMS/$1.json.tmp" "$ITEMS/$1.json"
  publish_status
}

publish_status() {
  if ls "$ITEMS"/*.json >/dev/null 2>&1; then
    jq -cs . "$ITEMS"/*.json > "$STATE/cache-status.json.tmp"
  else
    echo '[]' > "$STATE/cache-status.json.tmp"
  fi
  mv "$STATE/cache-status.json.tmp" "$STATE/cache-status.json"
}

# Download $2 (hub path) to $1 (absolute final path), $3 size, $4 mtime.
# Progress for image $5 is reported as $6 + bytes so far, of $7.
PROGRESS_MIN_BYTES=$((64 * 1048576))

# Resumable download of f_src into f_part (set by fetch).
download_cmd() {
  # shellcheck disable=SC2086
  curl -fsS --retry 3 --retry-delay 5 -C - ${CACHE_RATE_LIMIT:+--limit-rate "$CACHE_RATE_LIMIT"} \
    $CURL_OPTS -o "$f_part" "${CENTRAL_SERVER_URL}${f_src}"
}

fetch() {
  f_final="$1"; f_src="$2"; f_size="$3"; f_mtime="$4"; f_id="$5"; f_base="$6"; f_total="$7"
  f_part="${f_final}.nd-${f_size}-${f_mtime}.part"
  mkdir -p "$(dirname "$f_final")"
  [ "$(fsize "$f_part")" -gt "$f_size" ] && rm -f "$f_part"
  if [ "$f_size" -eq 0 ]; then
    : > "$f_part"
  elif [ "$(fsize "$f_part")" -lt "$f_size" ]; then
    if [ "$f_size" -lt "$PROGRESS_MIN_BYTES" ]; then
      download_cmd 2>"$WORK/curl.err" || { log "download of $f_src failed: $(tr '\n' ' ' < "$WORK/curl.err")"; return 1; }
    else
      # Big file (install.wim, ...): report progress while it downloads.
      download_cmd 2>"$WORK/curl.err" &
      f_pid=$!
      while kill -0 "$f_pid" 2>/dev/null; do
        sleep 5
        set_item "$f_id" syncing "" "$(( f_base + $(fsize "$f_part") ))" "$f_total" "downloading $(basename "$f_final")"
      done
      wait "$f_pid" || { log "download of $f_src failed: $(tr '\n' ' ' < "$WORK/curl.err")"; return 1; }
    fi
  fi
  [ "$(fsize "$f_part")" -eq "$f_size" ] || { log "size mismatch for $f_src"; rm -f "$f_part"; return 1; }
  touch -d "@$f_mtime" "$f_part" && mv -f "$f_part" "$f_final"
}

# ------------------------------------------------------------------ sync
# One image: $1 = its manifest object (JSON), $2 = "check" (verify what's on
# disk and report it, no downloads) or "fetch".
sync_iso() {
  obj="$1"; mode="$2"
  id="$(echo "$obj" | jq -r .id)"
  case "$id" in ''|*[!0-9]*) log "ignoring image with invalid id"; return 0 ;; esac
  if [ "$(echo "$obj" | jq -r '.keep // false')" = "true" ]; then
    set_item "$id" pending "" 0 0 "image is being updated on the hub"
    return 0
  fi
  err="$(echo "$obj" | jq -r '.error // empty' | tr -d '[:cntrl:]' | cut -c1-200)"
  if [ -n "$err" ]; then
    # The hub can't let relays cache this image (e.g. paths that differ only in
    # letter case): drop any copy so it doesn't take up space.
    log "image $id can't be cached: $err"
    rm -rf -- "${CACHE_DIR:?}/tree/$id" "${CACHE_DIR:?}/raw/$id"
    set_item "$id" error "" 0 0 "$err"
    return 0
  fi
  etag="$(echo "$obj" | jq -r .etag)"
  total="$(echo "$obj" | jq -r .total_bytes)"
  list="$WORK/files-$id"
  # dest <TAB> size <TAB> mtime <TAB> src, for safe destinations only: inside
  # this image's directory, no . or .. components, no control characters or
  # backslashes (tabs/newlines would also break this list), hub-relative src.
  # (Oniguruma has no \uXXXX escapes, hence [:cntrl:].)
  echo "$obj" | jq -r --arg id "$id" '
    .files[]
    | select((.dest | startswith("tree/" + $id + "/") or startswith("raw/" + $id + "/"))
             and (.dest | test("(^|/)\\.\\.?(/|$)") | not)
             and ([.dest, .src] | map(test("[[:cntrl:]\\\\]")) | any | not)
             and (.src | startswith("/files/iso/")))
    | [.dest, (.size|tostring), (.mtime|tostring), .src] | join("\t")' > "$list"
  listed="$(echo "$obj" | jq '.files | length')"
  kept="$(wc -l < "$list")"
  # Two entries for one path would both count towards the total while one
  # overwrites the other. The hub never sends that; don't trust it blindly.
  if [ "$(cut -f1 "$list" | sort | uniq -d | wc -l)" -ne 0 ]; then
    log "image $id: manifest lists the same path more than once"
    set_item "$id" error "" 0 "$total" "manifest lists the same path more than once - not caching this image"
    return 0
  fi
  if [ "$kept" -ne "$listed" ] || [ "$listed" -eq 0 ]; then
    # Never report an incomplete copy as ready: the hub would send clients here.
    log "image $id: $((listed - kept)) of $listed manifest entries rejected as unsafe"
    set_item "$id" error "" 0 "$total" "$((listed - kept)) of $listed files have unsafe names - not caching this image"
    return 0
  fi

  # What's missing, and how much it is.
  done_bytes=0; need_bytes=0
  : > "$list.todo"
  while IFS="$TAB" read -r dest size mtime src; do
    f="$CACHE_DIR/$dest"
    if [ -f "$f" ] && [ "$(stat -c '%s %Y' "$f")" = "$size $mtime" ]; then
      done_bytes=$((done_bytes + size))
    else
      printf '%s\t%s\t%s\t%s\n' "$dest" "$size" "$mtime" "$src" >> "$list.todo"
      part="$f.nd-${size}-${mtime}.part"
      need_bytes=$((need_bytes + size - $(fsize "$part")))
    fi
  done < "$list"

  # Remove files (and stale partial downloads) the manifest no longer lists.
  {
    cut -f1 "$list"
    awk -F"$TAB" '{print $1 ".nd-" $2 "-" $3 ".part"}' "$list.todo"
  } | sort > "$list.keep"
  for top in tree raw; do
    [ -d "$CACHE_DIR/$top/$id" ] || continue
    (cd "$CACHE_DIR" && find "$top/$id" -type f) | sort | comm -23 - "$list.keep" | while IFS= read -r extra; do
      rm -f -- "${CACHE_DIR:?}/$extra"
    done
    find "$CACHE_DIR/$top/$id" -mindepth 1 -depth -type d -empty -delete 2>/dev/null
  done

  if [ ! -s "$list.todo" ]; then
    set_item "$id" ready "$etag" "$done_bytes" "$total" ""
    return 0
  fi
  if [ "$mode" = "check" ]; then
    set_item "$id" pending "" "$done_bytes" "$total" "queued for download"
    return 0
  fi
  if ! in_window; then
    set_item "$id" pending "" "$done_bytes" "$total" "waiting for the sync window $CACHE_SYNC_WINDOW"
    return 0
  fi
  free="$(free_bytes)"
  if [ "$need_bytes" -gt "$free" ]; then
    set_item "$id" error "" "$done_bytes" "$total" \
      "not enough disk space: needs $((need_bytes / 1048576)) MiB more, $((free / 1048576)) MiB free"
    return 0
  fi

  log "image $id: downloading $(wc -l < "$list.todo") file(s), $((need_bytes / 1048576)) MiB"
  set_item "$id" syncing "" "$done_bytes" "$total" ""
  failed=0; n=0
  while IFS="$TAB" read -r dest size mtime src; do
    if fetch "$CACHE_DIR/$dest" "$src" "$size" "$mtime" "$id" "$done_bytes" "$total"; then
      done_bytes=$((done_bytes + size))
      n=$((n + 1))
      # Thousands of small files in a Windows tree: don't rewrite the status for each.
      if [ $((n % 25)) -eq 0 ] || [ "$size" -ge "$PROGRESS_MIN_BYTES" ]; then
        set_item "$id" syncing "" "$done_bytes" "$total" ""
      fi
    else
      failed=$((failed + 1))
    fi
    in_window || break
  done < "$list.todo"

  if [ "$failed" -gt 0 ]; then
    set_item "$id" error "" "$done_bytes" "$total" "$failed file(s) failed to download - retrying later"
  elif [ "$done_bytes" -lt "$total" ]; then
    set_item "$id" pending "" "$done_bytes" "$total" "waiting for the sync window $CACHE_SYNC_WINDOW"
  else
    log "image $id ready (etag ${etag%"${etag#????????????}"})"
    set_item "$id" ready "$etag" "$done_bytes" "$total" ""
  fi
}

sync_once() {
  # shellcheck disable=SC2086
  code=$(curl -sS -o "$WORK/manifest.json" -w '%{http_code}' -m 120 $CURL_OPTS \
    -H "X-Relay-Key: ${RELAY_API_KEY}" "$MANIFEST_URL" 2>"$WORK/curl.err") || code=000
  if [ "$code" != "200" ] || ! jq -e '.isos | type == "array"' "$WORK/manifest.json" >/dev/null 2>&1; then
    log "could not fetch the cache manifest (HTTP $code) $(tr '\n' ' ' < "$WORK/curl.err" 2>/dev/null)"
    return 1
  fi
  manifest_etag="$(jq -r '.etag // ""' "$WORK/manifest.json")"

  smb="$(jq -c '.smb // empty' "$WORK/manifest.json")"
  if [ -n "$smb" ]; then
    apply_smb "$(echo "$smb" | jq -r .share)" "$(echo "$smb" | jq -r .username)" "$(echo "$smb" | jq -r .password)"
  fi

  # Forget images that are no longer assigned.
  ids="$(jq -r '.isos[].id' "$WORK/manifest.json")"
  for d in "$CACHE_DIR"/tree/* "$CACHE_DIR"/raw/* "$ITEMS"/*.json; do
    [ -e "$d" ] || continue
    id="$(basename "$d" .json)"
    if ! echo "$ids" | grep -qx "$id"; then
      case "$d" in "$ITEMS"/*) ;; *) log "removing image $id (no longer assigned)" ;; esac
      rm -rf -- "$d"
    fi
  done
  publish_status

  # First report every complete copy (fast: a stat per file), so a restarted
  # relay serves what it has while other images are still downloading.
  jq -c '.isos[]' "$WORK/manifest.json" | while IFS= read -r obj; do
    sync_iso "$obj" check
  done
  jq -c '.isos[]' "$WORK/manifest.json" | while IFS= read -r obj; do
    [ "$(jq -r .status "$ITEMS/$(echo "$obj" | jq -r .id).json" 2>/dev/null)" = "ready" ] && continue
    sync_iso "$obj" fetch
  done
  echo "$manifest_etag" > "$STATE/cache-manifest-done"
}

log "cache in ${CACHE_DIR}, served at ${CACHE_URL} (smb=${CACHE_SMB}, rate limit=${CACHE_RATE_LIMIT:-none}, window=${CACHE_SYNC_WINDOW:-any time})"
publish_status
while true; do
  ensure_http
  ensure_smb
  sync_once
  # Idle until the heartbeat sees a new manifest etag (assignments or images
  # changed on the hub), or IDLE_SECONDS pass; keep the servers alive meanwhile.
  waited=0
  while [ "$waited" -lt "$IDLE_SECONDS" ]; do
    sleep 5
    waited=$((waited + 5))
    ensure_http
    ensure_smb
    wanted="$(cat "$STATE/cache-manifest-wanted" 2>/dev/null || true)"
    applied="$(cat "$STATE/cache-manifest-done" 2>/dev/null || true)"
    [ -n "$wanted" ] && [ "$wanted" != "$applied" ] && break
  done
done
