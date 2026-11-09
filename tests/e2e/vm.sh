#!/bin/sh
# Start a QEMU PXE client on the test LAN (run as root, after net-up.sh).
#
#   vm.sh bios          SeaBIOS + QEMU's iPXE NIC ROM (a client that "already runs iPXE")
#                       MAC 52:54:00:aa:00:01, diskless
#   vm.sh uefi          OVMF native EDK2 PXE (the efi ROM only supplies the NIC driver)
#                       MAC 52:54:00:aa:00:02, diskless
#   vm.sh win [new]     Windows install target: KVM, OVMF, e1000e NIC + NVMe disk (inbox
#                       drivers in WinPE), boot order network -> disk, 64G qcow2 in /var/tmp
#                       MAC 52:54:00:aa:00:03. "new" wipes disk + NVRAM.
#   vm.sh uefi-sb       like uefi, with Secure Boot enforced and only the stock Microsoft
#                       keys enrolled (OVMF_CODE_4M.secboot.fd + OVMF_VARS_4M.ms.fd)
#                       MAC 52:54:00:aa:00:04, diskless. Needs a relay with SECURE_BOOT=true.
#   vm.sh win-sb [new]  like win, with Secure Boot as in uefi-sb
#                       MAC 52:54:00:aa:00:05
#
# Monitor socket: /tmp/nd-<mode>.mon (use screenshot.py / sendkeys.py)
# Serial log:     /tmp/nd-<mode>.serial
set -eu
mode="${1:?usage: vm.sh bios|uefi|uefi-sb|win|win-sb [new]}"
pidfile="/tmp/nd-$mode.pid"
if [ -f "$pidfile" ]; then kill "$(cat "$pidfile")" 2>/dev/null || true; rm -f "$pidfile"; sleep 1; fi

# Debian's OVMF (edk2 >= 2025.02) disables network boot without an RNG device.
common="-device virtio-rng-pci -vga std -display none -monitor unix:/tmp/nd-$mode.mon,server,nowait -serial file:/tmp/nd-$mode.serial -daemonize -pidfile $pidfile"
kvm=""; [ -w /dev/kvm ] && kvm="-enable-kvm -cpu host"

# UEFI firmware. The Secure Boot build needs SMM and a secure pflash, or a
# guest could rewrite the key databases.
ovmf="/usr/share/OVMF"
case "$mode" in
  *-sb)
    code="$ovmf/OVMF_CODE_4M.secboot.fd"; vars_template="$ovmf/OVMF_VARS_4M.ms.fd"
    machine="-machine q35,smm=on -global driver=cfi.pflash01,property=secure,value=on"
    ;;
  *)
    code="$ovmf/OVMF_CODE_4M.fd"; vars_template="$ovmf/OVMF_VARS_4M.fd"
    machine="-machine q35"
    ;;
esac

case "$mode" in
  bios)
    # shellcheck disable=SC2086
    qemu-system-x86_64 -name nd-bios $kvm -m 1536 -smp 2 \
      -netdev tap,id=n0,ifname=ndtap0,script=no,downscript=no \
      -device virtio-net-pci,netdev=n0,mac=52:54:00:aa:00:01,bootindex=1 \
      -boot n $common
    ;;
  uefi|uefi-sb)
    if [ "$mode" = uefi ]; then tap=ndtap1 mac=52:54:00:aa:00:02; else tap=ndtap3 mac=52:54:00:aa:00:04; fi
    cp -f "$vars_template" "/tmp/nd-$mode-vars.fd"
    # shellcheck disable=SC2086
    qemu-system-x86_64 -name "nd-$mode" $kvm $machine -m 1536 -smp 2 \
      -drive "if=pflash,format=raw,readonly=on,file=$code" \
      -drive "if=pflash,format=raw,file=/tmp/nd-$mode-vars.fd" \
      -netdev "tap,id=n0,ifname=$tap,script=no,downscript=no" \
      -device "virtio-net-pci,netdev=n0,mac=$mac,bootindex=1" \
      -boot n $common
    ;;
  win|win-sb)
    [ -n "$kvm" ] || { echo "$mode needs KVM (/dev/kvm)"; exit 1; }
    if [ "$mode" = win ]; then tap=ndtap2 mac=52:54:00:aa:00:03; else tap=ndtap4 mac=52:54:00:aa:00:05; fi
    disk="/var/tmp/nd-$mode.qcow2" vars="/var/tmp/nd-$mode-vars.fd"
    if [ "${2:-}" = "new" ]; then rm -f "$disk" "$vars"; fi
    [ -f "$disk" ] || qemu-img create -q -f qcow2 "$disk" 64G
    [ -f "$vars" ] || cp "$vars_template" "$vars"
    # shellcheck disable=SC2086
    qemu-system-x86_64 -name "nd-$mode" $kvm $machine -smp 4 -m 6144 \
      -drive "if=pflash,format=raw,readonly=on,file=$code" \
      -drive "if=pflash,format=raw,file=$vars" \
      -netdev "tap,id=n0,ifname=$tap,script=no,downscript=no" \
      -device "e1000e,netdev=n0,mac=$mac,bootindex=1" \
      -drive "file=$disk,if=none,id=d0,format=qcow2,cache=unsafe" \
      -device "nvme,drive=d0,serial=nd${mode%-sb}0,bootindex=2" \
      -device qemu-xhci -device usb-tablet $common
    ;;
  *) echo "unknown mode $mode"; exit 1 ;;
esac
echo "started $mode client (pid $(cat "$pidfile"))"
