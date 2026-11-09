#!/bin/sh
# Isolated PXE test LAN on a Linux dev host (run as root).
#
#   bridge ndtest0 10.77.0.1/24   <- relay (host network) and hub port 8000 are reached here
#   netns ndrouter 10.77.0.254    <- stand-in for the site's real DHCP server:
#                                    leases .100-.200, NO PXE options (like a typical router)
#   taps ndtap0..4                <- QEMU clients (see vm.sh)
#
# Needs: iproute2, iptables, dnsmasq (dnsmasq-base is enough).
set -eu

ip link add ndtest0 type bridge 2>/dev/null || true
ip addr replace 10.77.0.1/24 dev ndtest0
ip link set ndtest0 up

ip netns add ndrouter 2>/dev/null || true
if ! ip link show ndr-host >/dev/null 2>&1; then
  ip link add ndr-host type veth peer name ndr-ns
  ip link set ndr-ns netns ndrouter
fi
ip link set ndr-host master ndtest0 up
ip netns exec ndrouter ip addr replace 10.77.0.254/24 dev ndr-ns
ip netns exec ndrouter ip link set ndr-ns up
ip netns exec ndrouter ip link set lo up

for t in ndtap0 ndtap1 ndtap2 ndtap3 ndtap4; do
  ip tuntap add dev "$t" mode tap 2>/dev/null || true
  ip link set "$t" master ndtest0 up
done

# With Docker installed, br_netfilter sends bridged frames through the
# FORWARD chain (policy DROP) - client <-> router DHCP would be silently eaten.
iptables -C DOCKER-USER -i ndtest0 -o ndtest0 -j ACCEPT 2>/dev/null \
  || iptables -I DOCKER-USER -i ndtest0 -o ndtest0 -j ACCEPT 2>/dev/null \
  || true

if [ -f /run/ndrouter-dhcp.pid ]; then kill "$(cat /run/ndrouter-dhcp.pid)" 2>/dev/null || true; fi
ip netns exec ndrouter dnsmasq --conf-file=/dev/null --pid-file=/run/ndrouter-dhcp.pid \
  --port=0 --interface=ndr-ns --bind-interfaces --dhcp-range=10.77.0.100,10.77.0.200,1h \
  --dhcp-option=3 --dhcp-option=6 --log-dhcp --log-facility=/tmp/ndrouter-dhcp.log \
  --dhcp-leasefile=/tmp/ndrouter.leases --dhcp-authoritative
echo "test LAN up: bridge ndtest0 10.77.0.1/24, router DHCP in netns ndrouter (log /tmp/ndrouter-dhcp.log)"
