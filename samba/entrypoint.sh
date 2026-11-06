#!/bin/sh
# Configure and run smbd with a single read-only share of /data/extracted.
#
# Windows 11 24H2+ WinPE refuses guest SMB and requires signing, so access is
# authenticated with SMB_USERNAME / SMB_PASSWORD (the hub embeds the same
# credentials in the per-deployment WinPE script).
set -eu

SMB_USERNAME="${SMB_USERNAME:-netdispatch}"
SMB_SHARE="${SMB_SHARE:-netdispatch}"
SHARE_PATH="${SHARE_PATH:-/data/extracted}"

if [ -z "${SMB_PASSWORD:-}" ]; then
  echo "[netdispatch-samba] SMB_PASSWORD is not set - the share is disabled."
  echo "[netdispatch-samba] Set SMB_PASSWORD in .env (shared with the server container) to enable Windows Setup deployments."
  exec sleep infinity
fi

if ! id "$SMB_USERNAME" >/dev/null 2>&1; then
  adduser -D -H -s /sbin/nologin "$SMB_USERNAME"
fi

mkdir -p "$SHARE_PATH" /var/lib/samba/private /var/log/samba /run/samba

cat > /etc/samba/smb.conf <<EOF
[global]
   server role = standalone server
   workgroup = WORKGROUP
   netbios name = NETDISPATCH
   server string = netdispatch install media
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

[${SMB_SHARE}]
   path = ${SHARE_PATH}
   comment = netdispatch extracted images (read-only)
   read only = yes
   browseable = yes
   guest ok = no
   valid users = ${SMB_USERNAME}
   force user = ${SMB_USERNAME}
   # Extracted files are 0644; let WinPE execute setup.exe & co. anyway.
   acl allow execute always = yes
EOF

printf '%s\n%s\n' "$SMB_PASSWORD" "$SMB_PASSWORD" | smbpasswd -s -a "$SMB_USERNAME" >/dev/null
testparm -s >/dev/null 2>&1 || { testparm -s; exit 1; }

printf '[netdispatch-samba] sharing %s as \\\\<host>\\%s (user %s, read-only)\n' "$SHARE_PATH" "$SMB_SHARE" "$SMB_USERNAME"
exec smbd --foreground --debug-stdout --no-process-group
