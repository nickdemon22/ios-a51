#!/bin/bash
# Build the A51 iOS 16.7.16 root image (iPhone X userland) on this PC.
# Run as your normal user; it calls sudo itself (mounts, iOS file owners).
#   ./build-ios-root.sh
# Inputs:  ipsw/os.dmg ipsw/systemos.dmg ipsw/appos.dmg (extracted from the IPSW)
# Output:  build/ios16-root.img (16 GiB HFS+), checked with fsck
set -euo pipefail
cd "$(dirname "$0")"

img=build/ios16-root.img
mnt=$(mktemp -d)
trap 'mountpoint -q "$mnt" && sudo umount "$mnt"; rmdir "$mnt" 2>/dev/null || true' EXIT

for f in ipsw/os.dmg ipsw/systemos.dmg ipsw/appos.dmg; do
	[[ -f $f ]] || { echo "missing $f" >&2; exit 1; }
done
if [[ -e $img ]]; then
	echo "== 1/3 skipped: $img already exists (remove it to rebuild)"
else
	echo "== 1/3 copying the iOS volumes into a 16 GiB HFS+ image (takes a while)"
	# mkiosroot.sh ends with a strict fsck that Linux hfsprogs always fails on
	# a minor volume-header field (VIStat 0x8000) even after a clean repair.
	bash ios-a51/tools/mkiosroot.sh ipsw/os.dmg ipsw/systemos.dmg ipsw/appos.dmg "$img" 16G ||
		[[ -e $img ]]
fi

echo "== 2/3 bring-up changes (Setup skip, activation, watchdogs, no baseband)"
sudo fsck.hfsplus -f -y "$img" >/dev/null || true
sudo mount -t hfsplus -o loop,force "$img" "$mnt"
sudo python3 -I ios-a51/tools/sweet-root-tweaks.py "$mnt"
sync
sudo umount "$mnt"

echo "== 3/3 filesystem repair and check (a lone 'Volume header needs minor repair' is the known hfsprogs quirk)"
sudo fsck.hfsplus -f -y "$img" | tail -2
sudo fsck.hfsplus -f -n "$img" | tail -4 || true
ls -l "$img"
echo "done: $img"
