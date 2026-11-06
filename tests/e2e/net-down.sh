#!/bin/sh
# Tear down everything net-up.sh / vm.sh created (run as root).
# Uses pidfiles, never `pkill -f`: a pattern like "qemu.*ndtap" also matches
# the shell that is running this command over ssh and kills it.
set +e
for pidfile in /tmp/nd-bios.pid /tmp/nd-uefi.pid /tmp/nd-win.pid /run/ndrouter-dhcp.pid; do
  [ -f "$pidfile" ] && kill "$(cat "$pidfile")" 2>/dev/null
  rm -f "$pidfile"
done
sleep 1
for l in ndtap0 ndtap1 ndtap2 ndr-host ndtest0; do ip link del "$l" 2>/dev/null; done
ip netns del ndrouter 2>/dev/null
iptables -D DOCKER-USER -i ndtest0 -o ndtest0 -j ACCEPT 2>/dev/null
echo "test LAN removed"
