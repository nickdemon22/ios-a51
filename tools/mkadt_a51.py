#!/usr/bin/env python3
"""Build the Galaxy A51 (SM-A515F, Exynos 9611) ADT.

Same shape as tools/mkadt_dreamlte.py, with the A51 board facts that came out
of SM-A515F_EUR_13_Opensource and are still pending live confirmation:
  - XNU RAM: bank 1, 0x880000000, 512 MiB window. Confirmed live: S-Boot puts the
    kernel Image at ~0x80100000 and the FDT at 0x8a100000, both in bank 0, so
    bank 1 is clear for XNU. Live memory nodes: 0x880000000+2 GiB and
    0x900000000+2 GiB (4 GiB of high RAM in total, iomem 0x880000000-0x97fffffff).
  - GIC-400: GICD 0x12301000, GICC 0x12302000, verified on hardware (GICD_TYPER
    reads 0xfcef: 512 INTIDs, 8 CPUs; GICD_CTLR reads 0, i.e. S-Boot leaves the
    distributor off). The `reg` cells below are arm-io *relative*
    (0x12301000 - 0x10000000) exactly like dreamlte's 0x201000, because
    0086-a5xelte-gicv2-platform.patch maps them as `soc + r[0]`.
    0x14af1000 in /proc/iomem is abox_gic (audio), not the AP GIC.
  - timer: 26 MHz (CNTFRQ_EL0 reads 0x18cba80 at handoff). The live /timer node
    has `use-physical-timer`, so XNU uses CNTP and the GIC intid is 30
    (PPI 14), matching A5_USE_PHYS_TIMER in 0124-dreamlte-s8.patch. a5xelte uses
    27 only because the Exynos7580 DT lacks that property.
  - CPUs: logical 0-3 = Cortex-A53 MPIDR 0x0-0x3 (S-Boot boots MPIDR 0; the live
    DT names these nodes cpu@100..cpu@103), logical 4-7 = Cortex-A73 MPIDR
    0x100-0x103 (DT nodes cpu@0..cpu@3). The DT node names are the inverse of
    the MPIDRs, which is a trap.
  - S-Boot framebuffer 1080x2400 XRGB8888 at 0xca000000 — confirmed three ways:
    cmdline `s3cfb.bootloaderfb=0xca000000`, the live scanout channel
    `dpp@0x14881000 IN_BASE_ADDR_Y=0xca000000`, and the shim's own paint being
    visible on the panel.
  - Exynos UFS host 0x13520000 (CAP 0x1303ff0f, VERSION 0x0210) with the vendor
    HCI block at 0x13521100.
Identity stays the donor's iPhone X (D22AP): the A51 has no Home key.
"""
import argparse
import os
from pathlib import Path

import adt
import mkadt

DRAM_BASE = 0x880000000
DRAM_SIZE = 0x20000000          # 512 MiB, the A5XELTE/DREAMLTE-proven window
FB_PHYS, FB_W, FB_H = 0xca000000, 1080, 2400
TIMEBASE = 26000000

GICD, GICC = 0x12301000, 0x12302000
ARM_IO_BASE = 0x10000000
UFS_HCI, UFS_VHCI = 0x13520000, 0x13521100


def set_prop(node, name, value):
    for i, (key, flags, _) in enumerate(node.props):
        if key == name:
            node.props[i] = (key, flags, value)
            return
    node.props.append((name, 0, value))


def child(node, name):
    for candidate in node.children:
        if candidate.prop('name') == mkadt.cstr(name):
            return candidate
    raise ValueError(f'missing donor ADT node {name}')


def cpu_node(logical):
    """A51 is little-first: CPU0-3 are Cortex-A53, CPU4-7 Cortex-A73."""
    a53 = logical < 4
    mpidr = logical if a53 else 0x100 + (logical - 4)
    # One cluster for all eight cores (patch 0129): the A73s as cluster 1 give
    # XNU a second pset, which this non-AMP scheduler cannot handle.
    cluster = 0
    return [
        ('device_type', mkadt.cstr('cpu')),
        # SweetCPU matches 'arm,cortex-a53' (IONameMatch); the A73s list it too.
        ('compatible', (mkadt.cstr('arm,cortex-a53') if a53 else
                        mkadt.cstr('arm,cortex-a73') + mkadt.cstr('arm,cortex-a53')) +
         mkadt.cstr('ARM,v8')),
        ('reg', mkadt.u32(mpidr)),
        ('cpu-id', mkadt.u32(logical)),
        ('cluster-id', mkadt.u32(cluster)),
        ('cluster-type', mkadt.u32(0)),
        ('die-cluster-id', mkadt.u32(cluster)),
        ('cluster-core-id', mkadt.u32(logical)),
        ('l2-cache-id', mkadt.u32(0 if a53 else 1)),
        ('l3-cache-id', mkadt.u32(0)),
        ('state', mkadt.cstr('running' if logical == 0 else 'stopped')),
        ('timebase-frequency', mkadt.u32(TIMEBASE)),
        ('clock-frequency', mkadt.u32(1742000000 if a53 else 2301000000)),
        ('bus-frequency', mkadt.u32(100000000)),
        ('memory-frequency', mkadt.u32(1600000000)),
        ('peripheral-frequency', mkadt.u32(TIMEBASE)),
        ('fixed-frequency', mkadt.u32(TIMEBASE)),
    ]


def build(canvas=(FB_W, FB_H), fb_phys=FB_PHYS):
    root = mkadt.build(seed=os.urandom(64), qemu=True)
    # SweetPlatformExpert matches on the a5xelte board name; extra names are
    # accepted by IONameMatch, so list the A51 identity first.
    set_prop(root, 'compatible', mkadt.cstr('samsung,a51') +
             mkadt.cstr('samsung,a5xelte') + mkadt.cstr('samsung,exynos9610') +
             mkadt.cstr('AppleARM'))
    # Device identity. Without a serial number and the wlan/bluetooth MACs
    # (udid-version 1), MobileGestalt cannot answer UniqueDeviceID, lsd blocks
    # in MGCopyAnswer and iOS never leaves the Apple logo. Synthetic values.
    set_prop(root, 'serial-number', mkadt.cstr('F17A51XNU001'))
    set_prop(root, 'mlb-serial-number', mkadt.cstr('F17A51XNU001MLB1'))
    set_prop(root, 'model-number', mkadt.cstr('MQAX2'))
    set_prop(root, 'region-info', mkadt.cstr('LL/A'))
    chosen = child(root, 'chosen')
    set_prop(chosen, 'dram-base', mkadt.u64(DRAM_BASE))
    set_prop(chosen, 'dram-size', mkadt.u64(DRAM_SIZE))
    set_prop(chosen, 'firmware-version', mkadt.cstr('a51-entry'))
    set_prop(chosen, 'system-firmware-version', mkadt.cstr('a51-entry'))

    cpus = child(root, 'cpus')
    cpu0 = child(cpus, 'cpu0')
    for key, value in cpu_node(0):
        if key != 'cpu-id':
            set_prop(cpu0, key, value)
    for logical in range(1, 8):
        cpus.children.append(mkadt.node(f'cpu{logical}', cpu_node(logical)))

    arm_io = child(root, 'arm-io')
    set_prop(arm_io, 'device_type', mkadt.cstr('a51-io'))
    set_prop(arm_io, 'compatible', mkadt.cstr('samsung,exynos9610-io') + mkadt.cstr('arm-io'))
    set_prop(arm_io, 'ranges', mkadt.u64(0, ARM_IO_BASE, 0xf0000000))
    arm_io.children = [n for n in arm_io.children if n.prop('name') == mkadt.cstr('wdt')]
    arm_io.children.append(mkadt.node('gic', [
        ('compatible', mkadt.cstr('arm,cortex-a15-gic')),
        ('interrupt-controller', mkadt.cstr('master')),
        # Non-secure physical timer PPI 14 (INTID 30): the Samsung DT marks the
        # timer "use-physical-timer", matching A5_USE_PHYS_TIMER in the config.
        ('virtual-timer-intid', mkadt.u32(30)),
        ('reg', mkadt.u64(GICD - ARM_IO_BASE, 0x1000,
                          GICC - ARM_IO_BASE, 0x1000)),
    ]))
    arm_io.children.append(mkadt.node('disp0', [
        ('device_type', mkadt.cstr('display-subsystem')),
        ('compatible', mkadt.cstr('samsung,a51-display') +
         mkadt.cstr('samsung,a5xelte-display')),
        ('canvas-width', mkadt.u32(canvas[0])),
        ('canvas-height', mkadt.u32(canvas[1])),
    ]))
    # locally administered MACs, see the identity note above
    for name, last in (('wlan', 1), ('bluetooth', 2)):
        arm_io.children.append(mkadt.node(name, [
            ('device_type', mkadt.cstr(name)),
            ('local-mac-address', bytes([0x02, 0xa5, 0x51, 0, 0, last])),
        ]))

    root.children = [n for n in root.children if n.prop('name') not in
                     (mkadt.cstr('sweet-fbcon'), mkadt.cstr('sweet-ufs'))]
    # pe_serial.c looks the early console up by this name on the A5XELTE config.
    root.children.append(mkadt.node('a5xelte-fbcon', [
        ('reg', mkadt.u64(fb_phys, FB_W * FB_H * 4)),
        ('width', mkadt.u32(FB_W)),
        ('height', mkadt.u32(FB_H)),
        ('stride', mkadt.u32(FB_W * 4)),
    ]))
    root.children.append(mkadt.node('a51-ufs', [
        ('reg', mkadt.u64(UFS_HCI, 0x200, UFS_VHCI, 0x200)),
    ]))
    return root


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--canvas', default=f'{FB_W}x{FB_H}',
                        help='display size presented to iOS, WxH')
    parser.add_argument('--fb', default=hex(FB_PHYS),
                        help='S-Boot framebuffer physical address (from '
                             's3cfb.bootloaderfb once it is read off the device)')
    args = parser.parse_args()
    canvas = tuple(int(v) for v in args.canvas.split('x'))
    blob = adt.serialize(build(canvas, int(args.fb, 0)))
    args.output.write_bytes(blob)
    print(f'{args.output}: {len(blob)} bytes, canvas={canvas}, fb={args.fb}')
