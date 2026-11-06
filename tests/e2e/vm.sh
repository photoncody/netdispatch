#!/bin/sh
# Start a QEMU PXE client on the test LAN (run as root, after net-up.sh).
#
#   vm.sh bios        SeaBIOS + QEMU's iPXE NIC ROM (a client that "already runs iPXE")
#                     MAC 52:54:00:aa:00:01, diskless
#   vm.sh uefi        OVMF native EDK2 PXE (the efi ROM only supplies the NIC driver)
#                     MAC 52:54:00:aa:00:02, diskless
#   vm.sh win [new]   Windows install target: KVM, OVMF, e1000e NIC + NVMe disk (inbox
#                     drivers in WinPE), boot order network -> disk, 64G qcow2 in /var/tmp
#                     MAC 52:54:00:aa:00:03. "new" wipes disk + NVRAM.
#
# Monitor socket: /tmp/nd-<mode>.mon (use screenshot.py / sendkeys.py)
# Serial log:     /tmp/nd-<mode>.serial
set -eu
mode="${1:?usage: vm.sh bios|uefi|win [new]}"
pidfile="/tmp/nd-$mode.pid"
if [ -f "$pidfile" ]; then kill "$(cat "$pidfile")" 2>/dev/null || true; rm -f "$pidfile"; sleep 1; fi

# Debian's OVMF (edk2 >= 2025.02) disables network boot without an RNG device.
common="-device virtio-rng-pci -vga std -display none -monitor unix:/tmp/nd-$mode.mon,server,nowait -serial file:/tmp/nd-$mode.serial -daemonize -pidfile $pidfile"
kvm=""; [ -w /dev/kvm ] && kvm="-enable-kvm -cpu host"

case "$mode" in
  bios)
    # shellcheck disable=SC2086
    qemu-system-x86_64 -name nd-bios $kvm -m 1536 -smp 2 \
      -netdev tap,id=n0,ifname=ndtap0,script=no,downscript=no \
      -device virtio-net-pci,netdev=n0,mac=52:54:00:aa:00:01,bootindex=1 \
      -boot n $common
    ;;
  uefi)
    cp -f /usr/share/OVMF/OVMF_VARS_4M.fd /tmp/nd-uefi-vars.fd
    # shellcheck disable=SC2086
    qemu-system-x86_64 -name nd-uefi $kvm -machine q35 -m 1536 -smp 2 \
      -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
      -drive if=pflash,format=raw,file=/tmp/nd-uefi-vars.fd \
      -netdev tap,id=n0,ifname=ndtap1,script=no,downscript=no \
      -device virtio-net-pci,netdev=n0,mac=52:54:00:aa:00:02,bootindex=1 \
      -boot n $common
    ;;
  win)
    [ -n "$kvm" ] || { echo "win needs KVM (/dev/kvm)"; exit 1; }
    if [ "${2:-}" = "new" ]; then rm -f /var/tmp/nd-win.qcow2 /var/tmp/nd-win-vars.fd; fi
    [ -f /var/tmp/nd-win.qcow2 ] || qemu-img create -q -f qcow2 /var/tmp/nd-win.qcow2 64G
    [ -f /var/tmp/nd-win-vars.fd ] || cp /usr/share/OVMF/OVMF_VARS_4M.fd /var/tmp/nd-win-vars.fd
    # shellcheck disable=SC2086
    qemu-system-x86_64 -name nd-win $kvm -machine q35 -smp 4 -m 6144 \
      -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
      -drive if=pflash,format=raw,file=/var/tmp/nd-win-vars.fd \
      -netdev tap,id=n0,ifname=ndtap2,script=no,downscript=no \
      -device e1000e,netdev=n0,mac=52:54:00:aa:00:03,bootindex=1 \
      -drive file=/var/tmp/nd-win.qcow2,if=none,id=d0,format=qcow2,cache=unsafe \
      -device nvme,drive=d0,serial=ndwin0,bootindex=2 \
      -device qemu-xhci -device usb-tablet $common
    ;;
  *) echo "unknown mode $mode"; exit 1 ;;
esac
echo "started $mode client (pid $(cat "$pidfile"))"
