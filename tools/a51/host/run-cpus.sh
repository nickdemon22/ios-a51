#!/bin/bash
# run-cpus.sh N TAG: build a boot image (loader + XNU payload, cpus=N), write it to
# the boot partition from TWRP (verified by readback), reboot, wait for the
# dead-man reset back into TWRP and pull the pstore logs into logs/bootTAG.
# Env: KERNEL=build/<dir> with kernel.development.a5xelte, ADT=build/<file>,
# LOADER=build/<loader.bin>, EXTRA_ARGS (boot-args), WAIT (seconds).
# Lock screen + touch + USB live log (boot 39):
#   KERNEL=kernel137 ADT=a51-smp1-id.adt LOADER=loader-15min-muic.bin WAIT=1000 \
#   EXTRA_ARGS="sweetusb=2 a5fb_keep_on=1 a5fb_noconsole=1 sweetfb_hz=30" ./run-cpus.sh 7 39
set -euo pipefail
cd "$(dirname "$0")"
n=$1 tag=$2
(cd ios-a51 && python3 tools/mkpayload.py ../build/${KERNEL:-kernel128}/kernel.development.a5xelte ../build/${ADT:-a51-smp.adt} ../build/payload-$tag.bin \
  --cmdline "debug=0xc serial=3 cpus=$n keepsyms=1 -enable_kprintf_spam -noprogress rd=ufsroot0 ufsroot=userdata ufsrw=1 rootrw=1 cs_enforcement_disable=1 unentitled_ios_sim_launch=1 ignition_level=1 launchd_unsecure_cache=1 wdt=-1 wdt_test=no_user_checkin ${EXTRA_ARGS:-}" >/dev/null)
node ios-a51/tools/a51/mkboot.js --stock firmware/img/boot.img --kernel build/${LOADER:-loader-boot19.bin} --ramdisk build/payload-$tag.bin --ramdisk-compress raw --out build/boot-a51-$tag.img >/dev/null
img=build/boot-a51-$tag.img; sz=$(stat -c%s $img); want=$(sha256sum $img | cut -c1-16)
adb push $img /tmp/b.img >/dev/null
got=$(adb shell "dd if=/tmp/b.img of=/dev/block/sda12 bs=4096 conv=fsync 2>/dev/null && sync && head -c $sz /dev/block/sda12 | sha256sum | cut -c1-16")
[[ $got == "$want" ]] || { echo "readback mismatch $got != $want"; exit 1; }
echo "boot $tag (cpus=$n) written $want, rebooting at $(date +%T)"
adb reboot; t0=$(date +%s)
timeout ${WAIT:-300} adb wait-for-recovery
echo "back after $(( $(date +%s) - t0 )) s, reset $(adb shell cat /proc/reset_reason)"
mkdir -p logs/boot$tag
for f in $(adb shell ls /sys/fs/pstore/); do adb exec-out cat /sys/fs/pstore/$f > logs/boot$tag/$f; done
L=logs/boot$tag/pmsg-ramoops-0
strings $L | grep -a -E 'CPUs initialized|NOT alive|CPU_ON rc=|^panic\(' | sort -u | cut -c1-170
echo "execs: $(strings $L | grep -a -c '^exec ')  swaps: $(strings $L | grep -a -c 'SWEET FB: swap ')"
