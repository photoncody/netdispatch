#!/bin/sh
# Prepare /data (often a fresh root-owned bind mount), then drop to the
# unprivileged netdispatch user.
set -eu

DATA_DIR="${DATA_DIR:-/data}"
APP_UID="${PUID:-1000}"
APP_GID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR" "$DATA_DIR/isos" "$DATA_DIR/extracted" "$DATA_DIR/configs"
  # Only fix ownership of the top-level directories (never recurse through
  # multi-GB image trees on every start).
  for d in "$DATA_DIR" "$DATA_DIR/isos" "$DATA_DIR/extracted" "$DATA_DIR/configs"; do
    if [ "$(stat -c %u "$d")" != "$APP_UID" ]; then
      chown "$APP_UID:$APP_GID" "$d"
    fi
  done
  for f in "$DATA_DIR"/netdispatch.db* "$DATA_DIR/secret_key" "$DATA_DIR/initial_admin_password"; do
    if [ -e "$f" ]; then chown "$APP_UID:$APP_GID" "$f"; fi
  done
  exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --clear-groups -- "$@"
fi

exec "$@"
