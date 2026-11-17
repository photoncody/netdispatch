#!/bin/sh
# Builds the USB stick updater's kernel and initramfs (the Dockerfile's stick-updater stage):
#   build.sh <published updater dir> <output dir>
# Alpine's x86_64 packages are unpacked with apk --root --arch, so an arm64 builder needs no
# emulation; nothing from the target root is run here.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
app=$1
out=$2
root=/tmp/nd-root
img=/tmp/nd-initramfs

mkdir -p "$root/etc/apk" "$out"
cp /etc/apk/repositories "$root/etc/apk/"
apk add --root "$root" --arch x86_64 --keys-dir /usr/share/apk/keys/x86_64 --initdb --no-scripts --no-cache \
  busybox musl libstdc++ libgcc libssl3 ca-certificates-bundle \
  linux-lts linux-firmware-rtl_nic linux-firmware-bnx2 linux-firmware-tigon
kver=$(ls "$root/lib/modules")

# Userland: everything apk installed except the kernel, its modules and apk's own state.
mkdir -p "$img"
(cd "$root" && tar -cf - --exclude=./boot --exclude=./lib/modules --exclude=./lib/apk --exclude=./etc/apk --exclude=./var/cache .) \
  | tar -xf - -C "$img"
mkdir -p "$img/dev" "$img/proc" "$img/sys" "$img/tmp" "$img/run" "$img/root" "$img/mnt/data" "$img/mnt/boot" \
  "$img/opt/netdispatch" "$img/lib/modules/$kver/kernel"

# Only the modules in modules.txt and what they depend on, uncompressed for busybox modprobe.
grep -v -e '^#' -e '^$' "$here/modules.txt" | while read -r mod; do
  modprobe -d "$root" -S "$kver" --show-depends "$mod" | awk '$1 == "insmod" { print $2 }'
done | sort -u | while read -r ko; do
  rel=${ko#"$root"}
  mkdir -p "$img$(dirname "$rel")"
  cp "$root$rel" "$img$rel"
done
find "$img/lib/modules" -name '*.ko.gz' -exec gunzip {} +
cp "$root/lib/modules/$kver/modules.builtin" "$root/lib/modules/$kver/modules.builtin.modinfo" "$root/lib/modules/$kver/modules.order" "$img/lib/modules/$kver/"
depmod -b "$img" "$kver"

cp -a "$app/." "$img/opt/netdispatch/"
install -m 755 "$here/init" "$img/init"
grep -v -e "^#" -e "^$" "$here/modules.txt" > "$img/etc/netdispatch-modules"
[ -e "$img/dev/console" ] || mknod -m 600 "$img/dev/console" c 5 1
[ -e "$img/dev/null" ] || mknod -m 666 "$img/dev/null" c 1 3

(cd "$img" && find . | cpio -o -H newc --quiet) | xz -6 --check=crc32 > "$out/ndusb-initramfs"
cp "$root/boot/vmlinuz-lts" "$out/ndusb-vmlinuz"
echo "kernel $kver, $(find "$img/lib/modules" -name '*.ko' | wc -l) modules"
ls -l "$out"
