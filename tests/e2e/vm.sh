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
#   vm.sh iso-bios      boot the online-only boot ISO ($ND_ISO, default
#   vm.sh iso-uefi      /var/tmp/netdispatch-online.iso) from a CD drive, as BMC
#   vm.sh iso-uefi-sb   virtual media would. MACs 52:54:00:aa:00:06/07/08/09, diskless.
#   vm.sh iso-uefi-native  like iso-uefi with a pcnet NIC: no driver in OVMF or the signed iPXE (no
#                       option ROM), so the ISO must chain iPXE's own drivers.
#                       Same taps as bios/uefi/uefi-sb/uefi: don't run them at once.
#   vm.sh usb-uefi      boot a USB stick image ($ND_STICK, default /var/tmp/nd-stick.img, raw or .qcow2,
#   vm.sh usb-uefi-sb   made with make-stick.py) from xHCI, NIC on the test LAN, diskless.
#   vm.sh usb-bios      MACs 52:54:00:aa:00:0a/0b/0c. Same taps as uefi/uefi-sb/bios.
#   vm.sh usb-win [new]     offline install target: the stick first, then an NVMe disk
#   vm.sh usb-win-sb [new]  (64G qcow2 in /var/tmp); e1000e on QEMU's user-mode network
#                       with restrict=on: DHCP works, the hub is unreachable.
#                       MACs 52:54:00:aa:00:0d/0e. "new" wipes disk + NVRAM.
#
# Monitor socket: /tmp/nd-<mode>.mon (use screenshot.py / sendkeys.py)
# Serial log:     /tmp/nd-<mode>.serial
set -eu
mode="${1:?usage: vm.sh bios|uefi|uefi-sb|win|win-sb [new]|iso-bios|iso-uefi|iso-uefi-sb|iso-uefi-native|usb-uefi|usb-uefi-sb|usb-bios|usb-win|usb-win-sb [new]}"
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
  iso-*)
    iso="${ND_ISO:-/var/tmp/netdispatch-online.iso}"
    [ -f "$iso" ] || { echo "no boot ISO at $iso (download /api/usb/online.iso or set ND_ISO)"; exit 1; }
    case "$mode" in
      iso-bios) tap=ndtap0 mac=52:54:00:aa:00:06 ;;
      iso-uefi) tap=ndtap1 mac=52:54:00:aa:00:07 ;;
      iso-uefi-sb) tap=ndtap3 mac=52:54:00:aa:00:08 ;;
      iso-uefi-native) tap=ndtap1 mac=52:54:00:aa:00:09 ;;
      *) echo "unknown mode $mode"; exit 1 ;;
    esac
    # bootindex=2: OVMF only connects the devices in QEMU's boot order, and the
    # signed iPXE drives the NIC through the firmware's driver.
    nic="virtio-net-pci,netdev=n0,mac=$mac,bootindex=2"
    [ "$mode" = iso-uefi-native ] && nic="pcnet,netdev=n0,mac=$mac,romfile=,bootindex=2"
    fw=""
    if [ "$mode" != iso-bios ]; then
      cp -f "$vars_template" "/tmp/nd-$mode-vars.fd"
      fw="-drive if=pflash,format=raw,readonly=on,file=$code -drive if=pflash,format=raw,file=/tmp/nd-$mode-vars.fd"
    fi
    # shellcheck disable=SC2086
    qemu-system-x86_64 -name "nd-$mode" $kvm $machine -m 1536 -smp 2 $fw \
      -netdev "tap,id=n0,ifname=$tap,script=no,downscript=no" -device "$nic" \
      -drive "if=none,id=cd,media=cdrom,readonly=on,file=$iso" -device "ide-cd,drive=cd,bootindex=1" \
      $common
    ;;
  usb-*)
    stick="${ND_STICK:-/var/tmp/nd-stick.img}"
    [ -f "$stick" ] || { echo "no stick image at $stick (make one with make-stick.py or set ND_STICK)"; exit 1; }
    # A .qcow2 stick (an overlay on a raw stick image, say) keeps the original untouched.
    fmt=raw
    case "$stick" in *.qcow2) fmt=qcow2 ;; esac
    usb="-device qemu-xhci,id=xhci -drive if=none,id=stick,format=$fmt,cache=unsafe,file=$stick -device usb-storage,bus=xhci.0,drive=stick,bootindex=1"
    case "$mode" in
      usb-win|usb-win-sb)
        [ -n "$kvm" ] || { echo "$mode needs KVM (/dev/kvm)"; exit 1; }
        if [ "$mode" = usb-win ]; then mac=52:54:00:aa:00:0d; else mac=52:54:00:aa:00:0e; fi
        disk="/var/tmp/nd-$mode.qcow2" vars="/var/tmp/nd-$mode-vars.fd"
        if [ "${2:-}" = "new" ]; then rm -f "$disk" "$vars"; fi
        [ -f "$disk" ] || qemu-img create -q -f qcow2 "$disk" 64G
        [ -f "$vars" ] || cp "$vars_template" "$vars"
        # shellcheck disable=SC2086
        qemu-system-x86_64 -name "nd-$mode" $kvm $machine -smp 4 -m 6144 \
          -drive "if=pflash,format=raw,readonly=on,file=$code" \
          -drive "if=pflash,format=raw,file=$vars" \
          -netdev "user,id=n0,restrict=on" -device "e1000e,netdev=n0,mac=$mac" \
          -drive "file=$disk,if=none,id=d0,format=qcow2,cache=unsafe" \
          -device "nvme,drive=d0,serial=nd${mode%-sb}0,bootindex=2" \
          $usb -device usb-tablet $common
        ;;
      *)
        case "$mode" in
          usb-uefi) tap=ndtap1 mac=52:54:00:aa:00:0a ;;
          usb-uefi-sb) tap=ndtap3 mac=52:54:00:aa:00:0b ;;
          usb-bios) tap=ndtap0 mac=52:54:00:aa:00:0c ;;
          *) echo "unknown mode $mode"; exit 1 ;;
        esac
        fw=""
        if [ "$mode" != usb-bios ]; then
          cp -f "$vars_template" "/tmp/nd-$mode-vars.fd"
          fw="-drive if=pflash,format=raw,readonly=on,file=$code -drive if=pflash,format=raw,file=/tmp/nd-$mode-vars.fd"
        fi
        # shellcheck disable=SC2086
        qemu-system-x86_64 -name "nd-$mode" $kvm $machine -m 1536 -smp 2 $fw \
          -netdev "tap,id=n0,ifname=$tap,script=no,downscript=no" \
          -device "virtio-net-pci,netdev=n0,mac=$mac,bootindex=2" \
          $usb $common
        ;;
    esac
    ;;
  *) echo "unknown mode $mode"; exit 1 ;;
esac
echo "started $mode client (pid $(cat "$pidfile"))"
