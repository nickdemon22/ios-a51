#!/bin/bash
# Add the SSH server to the iOS root on the phone, writing only what changes.
# Phone in TWRP (/data not mounted), USB connected. Run as your normal user;
# it calls sudo itself (mount, root-owned files).
#   ./install-ssh.sh
# Prerequisite: build/procursus/tree-ssh from ios-a51/tools/procursus-tree.py,
# build/ssh/hostkeys/ssh_host_*_key from ssh-keygen.
# Then boot iOS with sweetusb=2 and:
#   ssh -i build/ssh/a51_ed25519 -o UserKnownHostsFile=build/ssh/known_hosts root@192.168.7.2
set -euo pipefail
cd "$(dirname "$0")"

live=build/ios16-live.img
mnt=$(mktemp -d)
trap 'mountpoint -q "$mnt" && sudo umount "$mnt"; rmdir "$mnt" 2>/dev/null || true' EXIT

if [[ ! -e $live ]]; then
	echo "== 0/5 local copy of the flashed image (reflink, instant on btrfs)"
	cp --reflink=auto --sparse=always build/ios16-root.img "$live"
fi

echo "== 1/5 pull the chunks iOS changed on the phone"
timeout 90 adb wait-for-recovery || { echo "phone not in TWRP"; exit 1; }
python3 -I sync-userdata.py pull "$live"

echo "== 2/5 replay iOS's HFS+ journal (iOS was reset, not shut down)"
python3 -I ios-a51/tools/hfs-journal-replay.py "$live"
python3 -I ios-a51/tools/hfs-journal-state.py "$live"

echo "== 3/5 install OpenSSH + shell"
sudo fsck.hfsplus -f -y "$live" >/dev/null || true
sudo mount -t hfsplus -o loop,force "$live" "$mnt"
# /var/jb is replaced below: show what sshd logged on the last boot first
for f in sshd.out sshd.log; do
	[[ -e $mnt/private/var/jb/var/log/$f ]] && { echo "-- last boot's $f:"; sudo tail -30 "$mnt/private/var/jb/var/log/$f"; }
done
ls -lnd "$mnt" "$mnt/private" "$mnt/private/var" "$mnt/private/var/root" "$mnt/private/var/root/.ssh" || true
sudo python3 -I ios-a51/tools/sweet-root-ssh.py "$mnt" build/procursus/tree-ssh build/ssh/a51_ed25519.pub build/ssh/hostkeys
sync
sudo umount "$mnt"

echo "== 4/5 filesystem check"
sudo fsck.hfsplus -f -y "$live" | tail -2

echo "== 5/5 push the changed chunks"
python3 -I sync-userdata.py push "$live"
echo "done: boot iOS, then  ssh -i build/ssh/a51_ed25519 -o UserKnownHostsFile=build/ssh/known_hosts root@192.168.7.2"
