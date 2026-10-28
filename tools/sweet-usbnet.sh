#!/bin/sh
# Host side of the phone's USB Ethernet (kernel sweetusb=2, patches 0075-0077).
#   tools/sweet-usbnet.sh [LOGFILE]
# Waits for the phone's CDC-ECM interface (host MAC 02:00:5e:77:ee:01),
# gives it 192.168.7.1/24 (the phone is 192.168.7.2), then prints the live
# kernel console the phone streams to UDP port 5140 (and appends it to
# LOGFILE if given). Needs sudo for the address.
set -eu

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
	echo "Usage: $(basename "$0") [LOGFILE]"
	echo "Wait for the phone's USB Ethernet interface and stream the live kernel UDP console."
	exit 0
fi

mac=02:00:5e:77:ee:01
echo "waiting for the phone's USB Ethernet ($mac)..." >&2

# Every phone reboot re-creates the interface without the address, so keep
# re-applying it while the listener runs.
keep_address() {
	while true; do
		ifname=$(ip -o link | grep -i "$mac" | awk -F': ' '{print $2}' | cut -d@ -f1 | head -1)
		if [ -n "$ifname" ] && ! ip -o -4 addr show dev "$ifname" | grep -q 192.168.7.1/24; then
			nmcli dev set "$ifname" managed no 2>/dev/null || true
			sudo ip addr replace 192.168.7.1/24 dev "$ifname"
			sudo ip link set "$ifname" up
			echo "$ifname up: 192.168.7.1 <-> phone 192.168.7.2; live log on udp/5140" >&2
		fi
		sleep 1
	done
}
keep_address &
trap 'kill $! 2>/dev/null' EXIT INT TERM

listen_udp() {
	port=5140
	if command -v socat >/dev/null 2>&1; then
		socat -u "udp-recv:$port,reuseaddr" -
	elif command -v ncat >/dev/null 2>&1; then
		ncat -l -u -k "$port"
	elif nc -h 2>&1 | grep -q -- '-k'; then
		nc -ukl "$port"
	else
		while true; do
			nc -u -l -p "$port" 2>/dev/null || nc -u -l "$port" 2>/dev/null || break
		done
	fi
}

if [ $# -ge 1 ]; then
	listen_udp | tee -a "$1"
else
	listen_udp
fi
