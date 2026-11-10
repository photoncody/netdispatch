#!/bin/sh
# Downloads the upstream library pinned in upstream.lock into external/unattend-generator.
#
#   ./fetch-upstream.sh
#
# The archive is checked against its SHA-256. Two build-time adjustments are made to the
# extracted copy (nothing upstream is edited in this repository):
#   - TargetFrameworks is reduced to net10.0, so restore does not pull packs for net8/net9.
#   - A Directory.Build.props records the commit as SourceRevisionId. The archive has no .git,
#     and BuildInfo reads the commit from the assembly's informational version.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/upstream.lock"
dest="$here/external/unattend-generator"
stamp="$dest/.netdispatch-commit"

if [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$UPSTREAM_COMMIT" ]; then
  echo "upstream $UPSTREAM_COMMIT already present"
  exit 0
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

curl -fsSL -o "$tmp/upstream.tar.gz" \
  "https://github.com/cschneegans/unattend-generator/archive/$UPSTREAM_COMMIT.tar.gz"
echo "$UPSTREAM_SHA256  $tmp/upstream.tar.gz" | sha256sum -c - >/dev/null

rm -rf "$dest"
mkdir -p "$dest"
tar xzf "$tmp/upstream.tar.gz" -C "$dest" --strip-components=1

csproj="$dest/UnattendGenerator.csproj"
frameworks='<TargetFrameworks>net8.0;net9.0;net10.0</TargetFrameworks>'
if ! grep -q "$frameworks" "$csproj"; then
  echo "error: upstream's TargetFrameworks is no longer $frameworks; update fetch-upstream.sh" >&2
  exit 1
fi
sed -i "s|$frameworks|<TargetFrameworks>net10.0</TargetFrameworks>|" "$csproj"

cat >"$dest/Directory.Build.props" <<EOF
<Project>
  <!-- Written by fetch-upstream.sh. The archive has no .git, so the commit is recorded here. -->
  <PropertyGroup>
    <SourceRevisionId>$UPSTREAM_COMMIT</SourceRevisionId>
  </PropertyGroup>
</Project>
EOF

echo "$UPSTREAM_COMMIT" >"$stamp"
echo "upstream $UPSTREAM_COMMIT extracted to $dest"
