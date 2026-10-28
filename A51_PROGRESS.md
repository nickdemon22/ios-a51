# Samsung Galaxy A51 (SM-A515F, Exynos 9611) — iOS 16 XNU Port Progress

This file tracks the bring-up of Apple's open-source XNU kernel (`xnu-8796`, iOS 16 generation) and unmodified iPhone X iOS 16.7.16 (`20H392`) userland on the **Samsung Galaxy A51 `SM-A515F` (codename `a51` / `a51nsxx`, SoC: Exynos 9611 / `exynos9610`)**.

Methodology follows `hackiosx.dev/porting` and `DREAMLTE_PROGRESS.md`. One milestone at a time. No critical partitions touched.

---

## Hardware Map (Static Reconnaissance from `SM-A515F_EUR_13_Opensource`)

All addresses below are **physical** (PA). Source files are under  
`/mnt/g/a51-port/SM-A515F_EUR_13_Opensource/Kernel.tar/Kernel/arch/arm64/boot/dts/`.

### 1. DRAM & Reserved Memory (exynos9610.dts lines 2718–2731, 1584–1702)

| Region | Base PA | Size | Notes |
|--------|---------|------|-------|
| **Bank 0 (low)** | `0x80000000` | 2 GiB (`0x80000000`) | Primary Linux RAM |
| **Bank 1 (high)** | `0x880000000` | 2 GiB (`0x80000000`) | Upper 4 GiB bank, no firmware carve-outs — **best candidate for XNU payload** |
| **Bank 2 (high)** | `0x900000000` | 2 GiB (`0x80000000`) | Upper bank |

**Reserved-memory carve-outs (must NOT overlap XNU heap):**
- `seclog_mem`              `0xC0000000`   512 KiB
- `abox_rmem`               `0xE9400000`   40 MiB
- `sec_debug_magic` (ramoops/pstore) `0x80000000`   4 KiB  ← **persistent log buffer**
- `modem_if`                `0xED000000`   138 MiB
- `cp_ram_logging`          `0xF5A00000`   30 MiB
- `chub_rmem`               `0xFD000000`   512 KiB
- `debug_snapshot`          `0xF9000000`   23 MiB
- `gnss_if`                 `0xFB000000`   8 MiB
- `wifibt_if`               (dynamic, 16 MiB)

**XNU RAM selection:** Use **Bank 1 (`0x880000000` – `0x8FFFFFFFF`)** — 2 GiB clean, no firmware reservations. ADT `dram-base = 0x880000000`, `dram-size = 0x80000000` (or 512 MiB for initial bring-up like A5XELTE/DREAMLTE).

### 2. Framebuffer & Display (DECON / DPU / DSIM)

- **DECON (display controller)**: `decon_f@0x148B0000` (exynos9610.dts:5600–5636)
  - Base PA: `0x148B0000`
  - Reg size: `0x10000`
  - IRQs: `0xC7, 0xC8, 0xCB, 0x10A` (GIC SPI)
  - `decon_cnt = 3`, `dpp_cnt = 4`, `dsim_cnt = 2`
  - `psr_mode = 2` (command mode — needs TE trigger unmask like S8)
  - `out_type = 0` (DSI), `out_idx = 0`

- **DSIM (MIPI DSI host)**: `dsim@0x148E0000` (exynos9610.dts:5587–5598)
  - Base PA: `0x148E0000`
  - Reg size: `0x100`
  - IRQ: `0xCC`
  - PHY: `phy_m4s4top_dsi0@0x11860000`

- **Panel resolution**: 1080 × 2400 (from A51 overlay `exynos9611-a51_eur_open_00.dts:2405`)
  - `melfas,display_resolution = <0x438 0x960>` → 1080 × 2400
  - Touch IC: **Melfas MMS100** (`melfas,mms_ts` @ I2C addr 0x48, IRQ GPIO ALIVE GPIO0_0)

- **Bootloader framebuffer (cont_splash)**: Not in DTS — passed at runtime via kernel cmdline `s3cfb.bootloaderfb=0xXXXXXXXX`.
  - **Candidate addresses** (by SoC family precedent):
    - Exynos 7580 (A5XELTE): `0x67000000`
    - Exynos 8895 (S8 DREAMLTE): `0xCC000000`
    - Exynos 9611 (A51): **TBD — must be read from TWRP `/proc/cmdline` or `/proc/iomem` on first boot**
  - Format: XRGB8888 (32 bpp), stride = width × 4 = 4320 bytes.

- **DECON TE trigger unmask** (command-mode panel):
  - Requires write to DECON register to unmask frame trigger (like S8 `0x1281` → `0x12860070`).
  - Exact register offset TBD from `drivers/gpu/drm/samsung/exynos/decon/` or by probing.

### 3. Persistent Logging (ramoops / pstore / sec_debug)

- **`sec_debug_magic`** at `0x80000000` (4 KiB) — exynos9610.dts:1624–1627
- **`debug_snapshot/log_pstore`** at `0xF9D10000` (2 MiB) — exynos9610.dts:1680–1683
- **TWRP pstore path**: `/sys/fs/pstore/pmsg-ramoops-0` (console) + `console-ramoops-0` (kernel)
- **Shim strategy**: Write early logs to `0x80000000` (sec_debug_magic) and/or last 4 MiB of USERDATA (like SWEET/A5XELTE). Survives warm reboot into TWRP.

### 4. Interrupt Controller (GIC)

- **GIC-400 (GICv2)** — exynos9610.dts:3110–3119
  - Compatible: `arm,cortex-a15-gic`, `arm,cortex-a9-gic`
  - **GICD**: `0x12301000` (size 0x1000)
  - **GICC**: `0x12302000` (size 0x1000)
  - **GICH**: `0x12304000` (size 0x2000)
  - **GICV**: `0x12306000` (size 0x2000)
  - Maintenance IRQ: `1 9 0xf04` (PPI 9, level-high)
  - `#interrupt-cells = <3>` (trigger, PPI/SPI, level)

- **Virtual timer PPI**: INTID 27 (IRQ 11 in Linux 1..16 PPI mapping)
### 5. CPU Topology & SMP (exynos9610.dts:2824–2910, 6332–6339)

| Core | MPIDR (reg) | Cluster | ISA | Idle States |
|------|-------------|---------|-----|-------------|
| CPU0 | `0x0`       | Little (A53) | Cortex-A53 | `bootcl-cpu-sleep` (C2) |
| CPU1 | `0x1`       | Little (A53) | Cortex-A53 | `bootcl-cpu-sleep` |
| CPU2 | `0x2`       | Little (A53) | Cortex-A53 | `bootcl-cpu-sleep` |
| CPU3 | `0x3`       | Little (A53) | Cortex-A53 | `bootcl-cpu-sleep` |
| CPU4 | `0x100`     | Big (A73)    | Cortex-A73 | `nobootcl-cpu-sleep` |
| CPU5 | `0x101`     | Big (A73)    | Cortex-A73 | `nobootcl-cpu-sleep` |
| CPU6 | `0x102`     | Big (A73)    | Cortex-A73 | `nobootcl-cpu-sleep` |
| CPU7 | `0x103`     | Big (A73)    | Cortex-A73 | `nobootcl-cpu-sleep` |

- **Enable method**: PSCI (`enable-method = "psci"`) — exynos9610.dts:2828 etc.
- **PMU/PSCI**: `exynos-pm` node at `0x11850000` + `0x12301200` (GICD ISPENDR) — exynos9610.dts:3056–3061
  - `num-gic = 0x10` (16 GIC interrupt lines for PMU)
  - PSCI CPU_ON function ID: `0xC4000003` (standard ARM64)

**Initial bring-up**: Start with `cpus=1` (only CPU0 / MPIDR 0x0, Cortex-A53). A73 cluster bring-up later.

### 6. Arch Timer & Clocks

- **Timer**: `arm,armv8-timer` at 26 MHz (`0x18CBA80`) — exynos9610.dts:3121–3127
- **Clock controller**: `samsung,exynos9610-clock` @ `0x12100000` — exynos9610.dts:3129
- **CNTFRQ_EL0**: 0 at boot (set by bootloader) — must be initialized to 26 MHz in shim.

### 7. Storage (Exynos UFS Host Controller)

- **UFS host**: `ufs@0x13520000` — exynos9610.dts:5349–5394
  - Base PA: `0x13520000` (HCI registers, size 0x200)
  - Extended regs: `0x13521100` (0x200), `0x13510000` (0x8000), `0x13530000` (0x100)
  - IRQ: `0x9D` (SPI)
  - Clocks: `GATE_UFS_EMBD_QCH_UFS`, `UFS_EMBD`
  - PHY: `ufs-phy` @ `0x13524000` (size 0x800)
  - PMU phandle: `exynos-pmu` @ `0x11860000`
  - VCC regulator: fixed `ufs-vcc` (GPIO controlled)
  - HW revision: 5
  - Lane/gear: 1 lane, gear 3 (HS-G3)

- **Block device path**: `/dev/block/platform/13520000.ufs/by-name/...` (from fstab)
  - BOOT partition: typically `sda7` (like S8)
  - USERDATA partition: typically `sda24` (57 GiB on S8, similar on A51)

### 8. Buttons & Input (GPIO Keys)

From `exynos9611-a51_eur_open_00.dts:2348–2373` (fragment@model):

| Button | Linux Key Code | GPIO Spec (ALIVE GPIO) | Wakeup |
|--------|----------------|------------------------|--------|
| Volume Down | `0x72` (114) | `<0xffffffff 0x6 0xf>` → ALIVE GPIO6_15 | No |
| Volume Up   | `0x73` (115) | `<0xffffffff 0x5 0xf>` → ALIVE GPIO5_15 | No |
| Power / Side | `0x74` (116) | `<0xffffffff 0x7 0xf>` → ALIVE GPIO7_15 | **Yes** (`gpio-key,wakeup`) |

- `pinctrl-0 = <0xe 0xf 0x10>` — references ALIVE pinctrl states.
- ALIVE GPIO base: `pinctrl@11850000` (exynos9610.dts:11–96) — GPA0/GPA1/GPA2 banks.

### 9. Touchscreen

- **Controller**: Melfas MMS100 (`melfas,mms_ts`) — A51 overlay:2395–2419
  - I2C address: `0x48` (7-bit)
  - Bus: I2C under USI/CMGP (exact bus TBD from fragment target)
  - IRQ: ALIVE GPIO0_0 (`<0xffffffff 0x0 0x0>`)
  - Power: `tsp_ldo_en` regulator (fixed 3.0 V)
  - Firmware: `tsp_melfas/mss100_a51.fw` / `mss100_a51_old.fw`
  - Features: FOD (fingerprint-on-display), LPM, dual firmware
  - Display resolution reported to driver: 1080 × 2400

### 10. Boot Image Format (Samsung S-Boot) — measured from `backup/stock-boot.img`

- **Bootloader**: Samsung S-Boot `A515FXXU8HWI1`. No `fastboot`; flash `boot.img`
  to `boot` (sda12) from recovery with `dd`.
- **Header**: Android boot image **v2** (`header_size = 1660`), **page size 2048**.
- **Kernel**: raw ARM64 `Image`, 36,974,632 bytes, `kernel_addr = 0x10008000`,
  header `text_offset = 0x80000`, `image_size = 0x2eb5000`, `flags = 0xa`
  (4 KiB pages, free physical placement), magic `ARM\x64`. **Not compressed** —
  so the shim payload goes in raw too.
- **Ramdisk**: 1,705,726 bytes, gzip.
- **DTB**: 134,936 bytes, `dtb_addr = 0x10000000`.
- **cmdline in the image**: `loop.max_part=7` (the real cmdline comes from S-Boot).
- **Trailer**: `SEANDROIDENFORCE` + padding; the partition is 61,865,984 bytes and
  the image content ends at ~38.8 MB, so ~23 MB of trailing padding must be
  carried over. `tools/a51/mkboot.js` preserves all of it byte-for-byte and only
  swaps the kernel slot.
- **Where S-Boot actually puts things** (from the shim): the kernel Image lands at
  `0x800e8000`/`0x80188000` — it varies per boot — and the FDT at `0x8a100000`.
  Both are in bank 0, so the payload must stay relocation-free and XNU's RAM must
  live in bank 1.
- **Verified boot**: `verifiedbootstate=orange`, `vbmeta.device_state=unlocked`,
  `svb.ver=SVB1.0` — a custom `boot` image is accepted.

---

## Partition Safety Checklist

| Partition | Node | Action |
|-----------|------|--------|
| `boot` | sda12 | ✅ Write test images |
| `userdata` | sda32 | ⚠️ Only after explicit user confirmation (HFS+ root image) |
| `bootloader` / `sboot` / `blcmd` (sda11) | — | ❌ NEVER |
| `efs` (sda1) / `sec_efs` (sda2) / `cpefs` (sdd1) | — | ❌ NEVER |
| `modem` / `radio` (sda14) | — | ❌ NEVER |
| `persist` (sda17) / `param` (sda3) / `up_param` (sda4) | — | ❌ NEVER |
| `recovery` (sda13) / `vbmeta` (sda22) / `vbmeta_samsung` (sda23) | — | ❌ NEVER |
| `misc` (sda18) / `keydata` / `keystorage` / `keyrefuge` / `steady` / `uh` / `uhcfg` / `spu` / `omr` / `bota` | — | ❌ NEVER |
| `dtbo` (sda10) | — | read only |

---

## Build, flash and read-back commands (all verified working)

```powershell
# Build the shim and repack it into the stock boot image
powershell -ExecutionPolicy Bypass -File tools\a51\build-all.ps1

# Capture everything from a live recovery (root; never writes a partition)
powershell -ExecutionPolicy Bypass -File tools\a51\phase1-dump2.ps1

# Flash, verify by readback, reboot, wait, pull the log
powershell -ExecutionPolicy Bypass -File tools\a51\flash-test1.ps1
powershell -ExecutionPolicy Bypass -File tools\a51\flash-test1.ps1 -Restore

# Read the shim's report (the channel that actually works)
adb exec-out cat /sys/fs/pstore/dmesg-ramoops-0
```

`tools\a51\pull-log.ps1` / `kcore-log.js` / `decode-log.js` are kept for the
`/dev/mem` and `/proc/kcore` routes, but this OrangeFox has neither.

---

## Log Format for Device Testing

Each device test boot must record in this file:
- **Boot #** and date
- **Image SHA-256** (host + device `sha256sum` + partition readback)
- **Screen observation**
- **`dmesg-ramoops-0` report** (saved under `backup/live/`)
- **What changed** in shim / mkadt / xnu since the previous boot

---

## Phase 1 Status: ✅ complete — hardware map confirmed on device

**Hardware Map**: ✅ extracted from `SM-A515F_EUR_13_Opensource` (see above), plus two
facts confirmed from the Samsung register headers this session:
  - `HW_SW_TRIG_CONTROL = 0x0070`, `HW_SW_TRIG_TIMER = 0x0074`, `HW_TE_CNT = 0x0078`,
    `SHADOW_REG_UPDATE_REQ = 0x0060` (`drivers/video/fbdev/exynos/dpu20/cal_9610/regs-decon.h`)
    — DPU20 shares the S8's shadow-update protocol, so `SHADOW_REG_UPDATE_REQ | bit31 | 0x3f`
    is the A51 equivalent of `DECON_UPDATE`.
  - PMU at `0x11860000` with `EXYNOS_SWRESET = 0x0400`, `INFORM0 = 0x0800`,
    `S5P_PMU_SPARE2/3 = 0x0908/0x090C` (`include/linux/soc/samsung/exynos-regs-pmu.h`).
    INFORM2/3 (`0x808`/`0x80c`) take the recovery-boot magic `0x12345678`/`0x12345674`.

**Device dumps / partition backups**: ✅ **complete** (OrangeFox `fox_a51`, adbd running as root)

Captured into `backup/` and `backup/live/`:
  - `ro.bootloader = A515FXXU8HWI1`, `ro.boot.dtbo_idx = 3`, `ro.boot.revision = 8`,
    `ro.product.device = a51`, serial (redacted, see backup/live/cmdline.txt) — the three revision facts are
    **confirmed on device**, so the overlay to read is `exynos9611-a51_eur_open_03.dts`.
  - `live/cmdline.txt` — includes `s3cfb.bootloaderfb=0xca000000`, `lcdtype=8388739`,
    `ess_setup=0xf9000000`, `sec_avc_log=0x40000@0x91202000`,
    `sec_tsp_log=0x40000@0x91244000`, `sec_disp_log=0x100000@0x91286000`,
    `sec_debug_next=0x800000@0x91388000`, `lpddr4_size=6`, `cp_reserved_mem=off`,
    `verifiedbootstate=orange`, `vbmeta.device_state=unlocked`.
  - `live/iomem.txt`, `live/dmesg.txt`, `live/fdt.dtb` (225,218 bytes, FDT v17),
    `partitions.txt`, `stock-boot.img`, `stock-dtbo.img`, `stock-vbmeta.img`.
  - The first attempt at 22:30 came back `Permission denied` for everything
    because that adb shell was not root; `tools/a51/phase1-dump2.ps1` now checks
    the identity first and falls back to `su -c`. Do **not** call `adb root`
    against this recovery — adbd is already root and the command hangs
    `wait-for-device`.

**Live DTB facts worth keeping** (`tools/a51/fdtdump.py backup/live/fdt.dtb`):
  - memory: `0x80000000+0x3ab00000`, `0xc0000000+0x20000000`, `0xe1900000+0x1e700000`,
    `0x880000000+0x80000000`, `0x900000000+0x80000000` (4 GiB of high RAM in two banks).
  - `/chosen`: `linux,initrd-start = 0x89000000`, `initrd-end = 0x89edb800` — S-Boot
    puts the ramdisk inside bank 1, so an XNU payload based at `0x880000000` must be
    copied out before the ramdisk area is reused.
  - CPU nodes are named the *inverse* of their MPIDRs: `cpu@100..cpu@103` are the
    Cortex-A53s with `reg = 0x0..0x3`, and `cpu@0..cpu@3` are the Cortex-A73s with
    `reg = 0x100..0x103`.
  - `/timer`: `clock-frequency = 0x18cba80`, `use-physical-timer`, interrupts
    `<1 13> <1 14> <1 11> <1 10>` → non-secure physical = PPI 14 = INTID 30.
  - `/interrupt-controller@12300000`: `reg = 0x12301000/0x12302000/0x12304000/0x12306000`.
    The `gicd`/`gicc` at `0x14af1000`/`0x14af2000` in `/proc/iomem` belong to
    `/abox_gic@0x14AF0000` (audio), not the AP.
  - `/reserved-memory/debug_snapshot/log_pstore`: `reg = 0xf9d10000 0x200000`, and
    dmesg confirms `ramoops: attached 0x200000@0xf9d10000, ecc: 0/0`.
  - touch: `melfas,mms_ts` at I2C `0x48` on `i2c@13870000`, IRQ ALIVE GPIO0_0,
    `display_resolution = <0x438 0x960>`.

**Confirmed partition map (`backup/partitions.txt`, UFS LUN0 = sda, LUN3 = sdd):**

| partition | node | partition | node |
|-----------|------|-----------|------|
| efs | sda1 | recovery | sda13 |
| sec_efs | sda2 | radio | sda14 |
| param | sda3 | cp_debug | sda15 |
| up_param | sda4 | nad_refer | sda16 |
| bota | sda5 | persistent | sda17 |
| keystorage | sda6 | misc | sda18 |
| steady | sda7 | keyrefuge | sda19 |
| uh | sda8 | keydata | sda20 |
| uhcfg | sda9 | dqmdbg | sda21 |
| **dtbo** | **sda10** | **vbmeta** | **sda22** |
| blcmd | sda11 | vbmeta_samsung | sda23 |
| **boot** | **sda12** | metadata | sda24 |
| | | super | sda25 |
| prism | sda26 | optics | sda27 |
| cache | sda28 | omr | sda29 |
| spu | sda30 | nad_fw | sda31 |
| **userdata** | **sda32** | cpefs | sdd1 |

This corrects the earlier guess in the safety table below: `boot` is **sda12**
(not p10) and `userdata` is **sda32** (not p24).

---

## Phase 2 Status: ✅ flashed, ran on hardware, evidence recovered

Toolchain provisioned on the host (no WSL and no git on this machine, so a
bare-metal GCC was installed instead):
  - `G:\a51-port\toolchain\gcc\xpack-aarch64-none-elf-gcc-13.3.1-1.1\bin` (GCC 13.3.1)
  - `G:\a51-port\toolchain\python` (CPython 3.12.10 embeddable, `tools/` on its path)
  - `E:\node.js\node.exe` (v22) drives the boot-image work

Artifacts:
  - `shim/a51/{start.S,main.c,a51.ld,build.ps1,build.sh,README.md}`
  - `shim/a51/out/loader.bin` — 22936 bytes, ARM64 `Image` header at offset 0,
    `text_offset=0x80000`, `flags=0xa` (matching stock), `image_size` patched to
    the real length, and **zero absolute relocations** (the build refuses to link
    otherwise; a `volatile uintptr_t` local is used where GCC would otherwise
    materialise a physical constant through a literal pool)
  - `backup/boot-a51-test1.img` — 24913920 bytes, built from the real
    `backup/stock-boot.img`, sha256
    `e47d5c3322ab3ec5f7690bf3a34f98d7c7c6885a2da68a240f93322dd649d7f2`

`tools/a51/mkboot.js` repacks the stock image surgically: header v2, page 2048,
the gzip ramdisk (1705726 bytes), the DTB (134936 bytes), the `SEANDROIDENFORCE`
trailer and the ~23 MB of trailing partition padding are carried over
byte-for-byte, and only the kernel slot changes. It gzips the payload
automatically if the stock kernel is gzip-compressed; here the stock kernel is a
raw `Image`, so the payload goes in raw. **Correction (boot 9):** what matters is
the *ramdisk*, not the kernel — S-Boot never decompresses it, so a gzipped
ramdisk is invisible to the payload finder; `make-boot.ps1 -RamdiskCompress`
(default `raw`) now controls this. The first two test images were built
from `OrangeFox-Unofficial-a51.img` before the stock dump existed — same header
version, page size and kernel format, so they were valid vehicles — but every
result quoted below comes from the stock-based image.

Shim behaviour (boot 1):
  1. Runs wherever S-Boot puts it (EL1, MMU off), no relocations.
  2. Seeds `CNTFRQ_EL0` with 26 MHz if the bootloader left it 0 (it does not).
  3. Captures 24 slots — CurrentEL, SCTLR_EL1, FDT PA, image PA, MPIDR_EL1,
     CNTFRQ/CNTPCT, ID_AA64MMFR0_EL1, ID_AA64PFR0_EL1, GICD_TYPER, UFS CAP and
     version, DECON GLOBAL_CONTROL / SHADOW_REQ / HW_SW_TRIG_CONTROL / HW_TE_CNT /
     interrupt enable+pending / SCALED_SIZE, DSIM, all eight DPP register windows,
     framebuffer PA, stage, heartbeat, run counter — as a binary record (to
     `0x91202000`, `0x91286000`, `0x91388000`, which turned out not to persist)
     and as an ASCII report.
  4. Publishes the ASCII report where recovery can actually read it: a forged
     ramoops record at `0xf9d10000` (`sig 0x43474244`, `start=0`, `size`, plus the
     `====1700000000.000000000-D\n` header that `ramoops_pstore_read()` requires)
     and a raw copy in the pmsg zone at `0xf9e90000` →
     `/sys/fs/pstore/dmesg-ramoops-0` and `pmsg-ramoops-0`.
  5. Paints the framebuffer at `0xca000000`, flushes the whole 1080x2400 canvas,
     then kicks `SHADOW_REG_UPDATE_REQ` and toggles `SW_TRIG_EN` for 15 s while
     counting FRAME_DONE events.
  6. Asks the bootloader for recovery (PMU INFORM2/3 magic `0x12345678` /
     `0x12345674` + `EXYNOS_SWRESET` at `0x11860400`), falls back to
     `PSCI_SYSTEM_RESET`, and after three runs stops resetting and halts so the
     phone cannot loop forever. **The recovery request works**: adb is back in
     OrangeFox about 50 s after the shim starts.

Evidence extraction: `adb exec-out cat /sys/fs/pstore/dmesg-ramoops-0`.
`tools/a51/pull-log.ps1`, `kcore-log.js` and `decode-log.js` are kept for the
`/dev/mem` and `/proc/kcore` routes, but this recovery has neither.

Flash: `tools/a51/flash-test1.ps1` — refuses to run without `backup/stock-boot.img`,
verifies the push by sha256 and the flash by an 8-page readback, reboots, waits up
to four minutes for adb and then pulls the logs. `-Restore` puts stock back.

---

## Phase 3 Status: ✅ ADT + board layer ready, ⏳ needs a Linux/CI host to compile

Donor ADT unblocked (no `ipsw` CLI, no git):
  - `https://api.ipsw.me/v4/ipsw/iPhone10,3/20H392` → iPhone10,3/iPhone10,6
    16.7.16 (20H392), 6171035648 bytes.
  - `tools/remotezip.py` pulled only `Firmware/all_flash/DeviceTree.d22ap.im4p`
    (33719 bytes, CRC-32 ok) into `research/ipsw-x/dt/` via HTTP range requests.
  - The im4p payload is LZFSE (`bvx2`), and `adt.py` shells out to `lzfse`.
    Windows copy found in the existing Ghidra install and staged at
    `G:\a51-port\toolchain\bin\lzfse.exe`; with that on PATH the payload unpacks
    to `research/ipsw-x/d22ap.adt` (186084 bytes, `EmbeddedDeviceTrees-7153.140.37.700.2`).
  - `tools/mkadt_a51.py` now emits a complete ADT (12208 bytes) whose root
    children are `chosen, defaults, cpus, arm-io, buttons, product,
    a5xelte-fbcon, a51-ufs` — `/product` carries `product-name = 'iPhone X'`.
    `tools/check-adt.py` round-trips it and prints every node the kernel needs.

Board layer, expressed once in `tools/a51/a51_board.py` and consumed two ways:
  - `tools/a51/apply-a51.py XNU_TREE` — runs `tools/dreamlte/apply-dreamlte.py`
    first (that is the layer which turns every hard-coded A5XELTE constant into
    an `A5_*` macro and brings in the Exynos UFS quirks), then inserts an
    `#if defined(A51)` branch ahead of the dreamlte one and re-points
    `UFS_ADT_NODE`/`UFS_DEFAULT_PART`. `--skip-stage1` for a tree that is
    already at 0124; validated against `tools/a51/make-synthetic-tree.py`.
  - `xnu/patches/0125-a51-exynos9611.patch` — generated by
    `tools/a51/mkpatch0125.py`, which reconstructs the post-0124 text from
    0124's own hunks and emits 17 hunks over 8 files: the `A5XELTE.h` board
    block, `sweet_ufs.c`, the extended DREAMLTE guards (`arm_vm_init.c` ×3,
    `pe_serial.c` ×3, `SweetCPU.cpp` ×2, `SweetFramebuffer.cpp` ×3,
    `a5xelte_keys.c`), and three `SweetBattery.cpp` hunks (see below).
    `tools/a51/verify-patch0125.py` checks the counts, that every context and
    removed line matches the post-0124 reconstruction at the stated line
    numbers, and that both routes produce the same board lines.

Values baked in, all measured (see the boot-1 table above):
`A5_FB_PHYS 0xca000000`, `1080x2400`, stride 4320, 5×2 MiB blocks,
`A5_ENTRY_MARKER_PHYS 0xf9d10100`, `A5_BOOT_MPIDR 0x0`,
`A5_KLOG_PHYS 0xf9d10000` / `A5_KLOG_SIZE 0x80000`,
`A5_USE_PHYS_TIMER 1` + `A5XELTE_VTIMER_INTID 30`, `A5_TOUCH_DEFAULT 0`
(Melfas MMS100 has no driver yet), `UFS_ADT_NODE "/a51-ufs"`,
`UFS_DEFAULT_PART "userdata"` (A51 GPT names are lower case; the S8's were not).

**Blocker for compiling**: `.github/workflows/xnu.yml` builds on `macos-14` with
Xcode 15 (`iig`, `Kernel.framework` private headers, KDK `KernelSupport` libs)
through `blacktop/darwin-xnu-build`. A Linux/WSL host cannot produce the kernel
binary; it can only prepare and verify the tree. This host now has WSL (see
below) but no macOS, and no `gh`/git credentials for the Actions route.

### First macOS CI build: undefined `_dreamlte_battery_read`

`.github/workflows/xnu.yml` publishes the kernel as an artifact and now also
uploads its full `build.log` (`build-log`, 24 MB / 124k lines) because the
runner truncates long step logs **from the end**, which is where `make` prints
its errors — run `40452629749` died with exit code 2 and not one diagnostic in
the visible log. With the artifact, run `40454004618` showed the real failure:

```
Undefined symbols for architecture arm64:
  "_dreamlte_battery_read", referenced from:
      SweetBattery::poll() in SweetBattery.cpo
ld: symbol(s) not found for architecture arm64
make[3]: *** [.../kernel.development.a5xelte.unstripped] Error 1
```

Root cause: `iokit/Kernel/arm/SweetBattery.cpp` is built for
`ARM64_BOARD_CONFIG_A5XELTE` (it is `MACHINE_CONFIG=A5XELTE` on both boards),
and 0124 put the S8 fuel-gauge extern **and** the call to it behind
`#if defined(ARM64_BOARD_CONFIG_A5XELTE)`, while the definition lives in
`bsd/dev/a5xelte_touch.c` under `#if defined(DREAMLTE)`. Extending that
DREAMLTE guard instead would link S8 MAX77865 HSI2C reads into the A51 kernel,
so `a51_board.A5XELTE_GUARD_SITES` now puts an A51 branch first in all four
sites: no gauge extern and inline SPMI stubs with `SWEET_BAT_MAH 4000u` (the
A51 pack), `poll()` publishing the same unmeasured AC default the S8 path falls
back to, and `start()` never arming the 30 s timer (`fReal = false`, so a
`sweetbattery=1` boot arg stays inert). The file also gains
`#include <pexpert/arm64/board_config.h>`, which is where `A51` is defined and
which it did not include before (it keyed only off the `-D` flags).

### Verified against the real xnu tree (WSL)

`tools/dreamlte/prep-xnu-tree.sh` was run in WSL against xnu-8796.141.3:

* all patches up to 0124 apply (122 commits; `0031`/`0041` SKIP as before — they
  are superseded by the a5xelte series),
* **`0125-a51-exynos9611.patch` applies** (`FIXUP … (patch-p1)`), no `.rej`/`.orig`
  left behind, and the resulting `A5XELTE.h` has the A51 branch with the dreamlte
  values preserved under `#elif defined(DREAMLTE)`,
* `bsd/dev/sweet_ufs.c` picks up `UFS_ADT_NODE "/a51-ufs"` and
  `UFS_DEFAULT_PART "userdata"`,
* `tools/a51/verify-board-routes.sh` proves the two routes are equivalent: a tree
  patched to 0124 + `apply-a51.py --skip-stage1` is **byte-identical** to a tree
  patched through 0125 (`diff -rq` over the whole tree is empty).

Trees live at `~/xnuwork/xnu` (through 0125) and `~/xnuwork124/xnu` (0124 +
script) inside WSL.

### WSL restored on this host

The platform had been removed (`LxssManager` service gone, no `wslservice.exe`,
`System32\lxss` nearly empty) while the distro registration survived, so
`wsl.exe` reported "not installed". Reinstalled per-machine from the already
downloaded `Microsoft.WSL_3.0.3.0_x64_ARM64.msixbundle` (WSL 3.0.3, kernel
6.18.54.1-1) via an elevated `Add-AppxPackage`. The existing **Ubuntu 26.04.1**
distro boots from `G:\a51-port\wsl_system\ext4.vhdx` and sees the workspace at
`/mnt/g/a51-port`. Available inside it: git, clang 21, gcc 15, make, python3,
`aarch64-linux-gnu-gcc`, patch, cpio, bison, flex. Missing: dtc, ninja, lz4,
lzfse (the Windows `lzfse.exe` covers the im4p case).

### userdata is encrypted — Phase 5 needs a decision

The first block of `userdata` (LBA `0x1f7500`) is high-entropy with no f2fs,
ext4 or HFS+ magic, i.e. Android's metadata encryption is active (the `metadata`
partition at LBA `0x16900` holds the key). Putting the iOS 16.7.16 HFS+ root
there means overwriting it and losing the Android installation.

**Authorisation: granted by the user on 2026-10-27** ("Разрешаю перезаписать
userdata"). Scope of that permission: write the iOS rootfs to `userdata`
(LBA `0x1f7500 … 0x1dc98ff`) when Phase 5 gets there. It does **not** extend to
any other partition — `boot` stays the only other writable target, and
`bootloader/efs/sec_efs/cpefs/modem/persist/param/up_param/recovery/vbmeta/
vbmeta_samsung/misc/keydata/keystorage/keyrefuge/steady/uh/uhcfg/spu/omr/bota/
dtbo` remain read-only or untouched.

---

## Waiting on: a write-capable GitHub token

The kernel can only be built by `.github/workflows/xnu.yml` on `macos-14`.
The token supplied on 2026-10-27 (fine-grained, expires 2027-01-25) is
**read-only**: `git ls-remote` succeeds on every repo, but `POST /user/repos`,
`POST /repos/.../forks` and `POST /repos/.../git/refs` all return
`403 Resource not accessible by personal access token`. Probes are in
`tools/a51/gh-token-check.sh`, `gh-capabilities.sh`, `gh-token-probe.sh` (the
last one creates and immediately deletes a throwaway ref to test write access).

What is needed, either form:
* **fine-grained PAT** with *Contents: Read and write* + *Actions: Read and
  write* on at least one public repo (plus *Administration: Read and write* if a
  dedicated `ios-a51` repo should be created), or
* **classic PAT** with the `repo` and `workflow` scopes.

Everything else is staged: the local git repo is initialised with one commit
(`9792b5f…`, 273 files, 2.9 MB, `backup/` and `research/` excluded by
`.gitignore`, no serials or tokens in tracked files — see
`tools/a51/prepublish-check.sh`), the workflow is set to trigger on the
`a51-bringup` branch, and `tools/a51/gh-push-branch.sh` pushes there without
touching any existing branch.

As soon as a write-capable token lands in
`G:\a51-port\toolchain\gh_token.txt`, the remaining sequence is: push → watch
the run → download the `xnu-kernel` artifact → `tools/mkadt_a51.py` +
`tools/mkpayload.py` → `mkboot.js --kernel shim/a51/out/loader.bin --ramdisk
payload.bin` → flash → read `/sys/fs/pstore/pmsg-ramoops-0` for the XNU console.

---

## Host toolchain (provisioned this session)

| tool | path |
|------|------|
| aarch64-none-elf GCC 13.3.1 | `G:\a51-port\toolchain\gcc\xpack-aarch64-none-elf-gcc-13.3.1-1.1\bin` |
| CPython 3.12.10 (embeddable) | `G:\a51-port\toolchain\python\python.exe`, with `G:\a51-port\ios-a51\tools` appended to `python312._pth` so `import adt`/`mkadt` work |
| lzfse | `G:\a51-port\toolchain\bin\lzfse.exe` (copied from the Ghidra install) |
| Node v22 | `E:\node.js\node.exe` (boot-image work) |
| adb | `E:\platform-tools-latest-windows\platform-tools\adb.exe` |

`adb root` must NOT be used against this OrangeFox: adbd is already root and the
command restarts it, after which `adb wait-for-device` blocks indefinitely.

---

## Device state at the end of this session

- `boot` (sda12) holds the **boot-10** image (sha256
  `88095b0a6bd59b860e59db94a40be9ab146be6fbf966432862d9aab40fd91135`,
  39 739 392 B — the raw-ramdisk payload boot that reached XNU and faulted).
  The stock image is preserved as local `backup/stock-boot.img` sha256
  `20baf0d0e48747f890bf2342a56993f1df5e6af02c77199116a839325cbfaa28`,
  verified three ways: the local file, readback of the partition header, and the
  on-device copy `/sdcard/stock-boot.img`.
- The phone was left in recovery (not rebooted into Android) so adb stays
  authorised.
- Nothing but `boot` was ever written. `dtbo`, `vbmeta`, `efs`, `recovery`,
  `param` and `misc` were read only; `misc` and `param` are all zeroes, so the
  recovery latch is not coming from a BCB.

---

## Next actions, in order

1. **Compile XNU.** Done by `.github/workflows/xnu.yml` on `macos-14` (push to
   `main` with `xnu/patches/**`). `dxb/build.sh` applies 0001–0125 and builds
   `MACHINE_CONFIG=A5XELTE`; `tools/a51/gh-fetch-kernel.sh <run>` pulls the
   `xnu-kernel` artifact into `out/kernel.development.a5xelte` (waits for the run
   to finish) and prints the Build diagnostics and the LSE count (must be 0).
2. **Boot the kernel:** `tools/a51/make-boot.ps1 -Kernel
   out/kernel.development.a5xelte -Out out/boot-a51.img` (raw ramdisk is the
   default: SWEETXNU payload + A51 ADT, `cpus=1`), then flash **manually, step
   by step** — push → sha256 verify → `dd if=/tmp/boot-a51.img
   of=/dev/block/by-name/boot bs=4096` → `sync` → readback compare (head 64 KiB
   + ramdisk at `bs=4096 skip=14`) → `adb shell reboot`. `flash-boot.ps1` and
   `pull-pstore.ps1` hang (PowerShell `| Out-String` buffering + `adb
   wait-for-device`) and must not be used until fixed. Pull pstore to a fresh
   `backup/logs/pstore-bootN/` dir; success signal is `=== XNUELTE1 ===`
   (marker reached) and/or `--- XNU console ---`. Target: "Waiting for root
   device".
3. **Phase 4 UFS:** the host is UFSHCI 2.1 at `0x13520000` (CAP `0x1303ff0f`),
   PHY `0x13524000`, IRQ `0x9d`, and `userdata` is `sda32`. The S8's Exynos
   quirks in `bsd/dev/sweet_ufs.c` are the starting hypothesis; the probe above
   already confirms them, including the LUN `0xD0` START STOP UNIT wake-up.
4. **Phase 5 input:** keys are ALIVE `GPIO5_15`/`GPIO6_15`/`GPIO7_15`
   (`a5xelte_keys.c` scheme), touch is Melfas MMS100 at I2C `0x48` with IRQ on
   ALIVE `GPIO0_0` — a new driver, `A5_TOUCH_DEFAULT` is 0 until it exists.

---

## Phase 4 Status: 🟢 UFS host proven read-only from the shim

Boot 6 (`b38a2c57…28fe26`, report `backup/live/boot8-dmesg-ramoops-0.txt`) ran
`shim/a51/ufs_probe.c`, adapted from the S8's proven probe. Everything the XNU
`bsd/dev/sweet_ufs.c` driver needs is now measured on this exact unit:

| item | value | note |
|------|-------|------|
| CAP | `0x1303ff0f` | bit24 (64AS) set → 64-bit DMA works |
| VERSION | `0x0210` | UFSHCI 2.1 |
| HCS / HCE | `0x10f` / `0x1` | host ready. **Unlike the S8, HCE reads 1 here** |
| S-Boot UTRLBA | `0xc9b7aa40` | the bootloader's list, in bank 0 |
| our UTRLBA | `0x880000000` | accepted (`RSR=1`) → bank 1 DMA is fine |
| TXPRDT / RXPRDT | `0x0c` / `0x0c` | 16-byte PRDT entries, ≤4 KiB each |
| VS NEXUS | `0xffff` | per-slot SCSI-command bits |
| UFSPRSECURITY | `0xefe26492` | DESCTYPE ≠ 3 → `prdt_stride = 16` |
| UTRD lengths | bytes | `resp 512<<16|512`, `PRDT 1024<<16|n*16` — the S8's `BROKEN_DWORD_UTRD` quirk applies |

**Wake-up sequence (identical to the S8):** the first TUR/INQUIRY return
CHECK CONDITION with sense `02/04/02` (NOT READY, initializing command
required); `START STOP UNIT START=1` on LUN 0 also fails; **`START STOP UNIT`
with power condition ACTIVE to the well-known LUN `0xD0` succeeds**, after which
TUR, READ CAPACITY and READ(10) all work. XNU must do this before mounting.

**Device geometry:** `last_lba = 0x1dc9fff`, `block_size = 0x1000` →
31,302,656 × 4 KiB = **128 GB**. GPT header `EFI PART` at LBA 1, entry array at
LBA 2, 128 entries, alternate header at the last LBA.

**Partition map read off the device (4 KiB LBA, inclusive):**

| partition | first | last | blocks | bytes |
|-----------|-------|------|--------|-------|
| efs | 0x400 | 0x17ff | 0x1400 | 20 MiB |
| sec_efs | 0x1800 | 0x2bff | 0x1400 | 20 MiB |
| param | 0x2c00 | 0x33ff | 0x800 | 8 MiB |
| up_param | 0x3400 | 0x3bff | 0x800 | 8 MiB |
| bota | 0x3c00 | 0x59ff | 0x1e00 | 30 MiB |
| keystorage | 0x5a00 | 0x5a7f | 0x80 | 512 KiB |
| steady | 0x5a80 | 0x5e7f | 0x400 | 4 MiB |
| uh | 0x5e80 | 0x607f | 0x200 | 2 MiB |
| uhcfg | 0x6080 | 0x647f | 0x400 | 4 MiB |
| dtbo | 0x6480 | 0x6c7f | 0x800 | 8 MiB |
| blcmd | 0x6c80 | 0x6c8f | 0x10 | 64 KiB |
| **boot** | **0x6c90** | **0xa78f** | 0x3b00 | 59 MiB |
| recovery | 0xa790 | 0xeb5f | 0x43d0 | 68 MiB |
| radio | 0xeb60 | 0x1315f | 0x4600 | 70 MiB |
| cp_debug | 0x13160 | 0x1365f | 0x500 | 5 MiB |
| nad_refer | 0x13660 | 0x1375f | 0x100 | 1 MiB |
| persistent | 0x13760 | 0x137df | 0x80 | 512 KiB |
| misc | 0x137e0 | 0x138df | 0x100 | 1 MiB |
| keyrefuge | 0x138e0 | 0x148df | 0x1000 | 16 MiB |
| keydata | 0x148e0 | 0x158df | 0x1000 | 16 MiB |
| dqmdbg | 0x158e0 | 0x168df | 0x1000 | 16 MiB |
| vbmeta | 0x168e0 | 0x168ef | 0x10 | 64 KiB |
| vbmeta_samsung | 0x168f0 | 0x168ff | 0x10 | 64 KiB |
| metadata | 0x16900 | 0x188ff | 0x2000 | 32 MiB |
| super | 0x18900 | 0x1b00ff | 0x197800 | 6.6 GiB |
| prism | 0x1b0100 | 0x1e20ff | 0x32000 | 200 MiB |
| optics | 0x1e2100 | 0x1e34ff | 0x1400 | 20 MiB |
| cache | 0x1e3500 | 0x1efcff | 0xc800 | 200 MiB |
| omr | 0x1efd00 | 0x1f2eff | 0x3200 | 50 MiB |
| spu | 0x1f2f00 | 0x1f60ff | 0x3200 | 50 MiB |
| nad_fw | 0x1f6100 | 0x1f74ff | 0x1400 | 20 MiB |
| **userdata** | **0x1f7500** | **0x1dc98ff** | 0x1bd2400 | **111.3 GiB** |

`userdata` is where the iOS 16.7.16 HFS+ root goes (offset
`0x1f7500 * 4096 = 0x1f7500000` = 8,444,903,424 bytes, length
`0x1bd2400 * 4096` = 119,443,927,040 bytes). These numbers, not `/dev/block`
symlinks, are what the XNU driver will use.

The shim's DMA buffer sits at `0x880000000` (80 KiB: UTRL 4 KiB, UCD 4 KiB +
PRDT, data 64 KiB), i.e. the same bank XNU will use — safe because nothing else
touches bank 1 before the kernel owns it.

---

## Boot log

Boot 1 (shim v5) ran on the device on 2026-10-26/27. Evidence: the shim forges a
ramoops record into `log_pstore` and it comes back as
`/sys/fs/pstore/dmesg-ramoops-0` (saved at `backup/live/boot6-dmesg-ramoops-0.txt`).
Screen was observed directly: colour bars + `HACKIOS SHIM A51 REV8 OK`.

| # | image sha256 | result | evidence |
|---|--------------|--------|----------|
| 1 | `29d21c29…d636dff2` | inconclusive | no readable channel yet |
| 2 | `d472fa4d…7b72118c` | **shim ran** (90 s hold) | returned after 139 s; colour bars seen on panel; `RSTSTAT: SWRESET` |
| 3 | `b5886f7c…49ebecd20` | **shim ran, log recovered** | `dmesg-ramoops-0` = `=== A51SHIM4 ===` report |
| 4 | `e47d5c33…2dd649d7f2` | **shim ran, full DPU survey** | `dmesg-ramoops-0` = `=== A51SHIM5 ===` report |
| 5 | `9b7dd444…291dc1fa` | **shim ran, panel dashboard** | `backup/live/boot7-dmesg-ramoops-0.txt` = `=== A51SHIM6 ===`; 25 s hold, 17,626,356 trigger loops, **1901 FRAME_DONE (76 fps)**, TE counters 0x875/0x8c5 |
| 6 | `b38a2c57…28fe26` | **shim ran, UFS read-only probe succeeded** | `backup/live/boot8-dmesg-ramoops-0.txt`: GPT `EFI PART`, 128 GB, full partition table, `userdata` at LBA `0x1f7500` |
| 7 | `d31bc53b…f755714` | **shim ran, read path self-verified** | `backup/live/boot9-dmesg-ramoops-0.txt`: LBA `0x6c90` reads back `ANDROID!` with `kernel_size=0xc45a` = 50266 = exactly the flashed `loader.bin`, `page_size=0x800`; `userdata` LBA `0x1f7500` is high-entropy (no f2fs/ext4/HFS magic) → metadata-encrypted |
| 8 | `9aee5678…fd8b77fa0` | **shim ran, XNU handoff path validated** | `backup/live/boot10-dmesg-ramoops-0.txt`: `linux,initrd-start=0x89000000`, `initrd-end=0x891a0800` (1 705 984 B = the stock ramdisk), `payload_magic_ok=0` — the FDT walk and payload detection work against S-Boot's live tree |
| 9 | gzip image, sha not retained (overwritten by #10) | **gzip ramdisk rejected, self-diagnosed** | `backup/logs/pstore-boot9/pmsg-ramoops-0`: `initrd_size=0x5e2800` (stock ramdisk + *gzipped* payload), `payload_magic_ok=0` → shim requested recovery. **S-Boot does not decompress the ramdisk** — it copies bytes verbatim to `0x89000000` and only advertises `/chosen/linux,initrd-{start,end}`; `make-boot.ps1` gained `-RamdiskCompress raw` (now the default) |
| 10 | `88095b0a6bd59b8…d91135` | **XNU entered, died on an alignment fault** | `backup/logs/pstore-boot10/pmsg-ramoops-0`: `payload_magic_ok=1`, all 10 segments copied (`__TEXT`…`__LINKEDIT`, `__DATA` vmsize 0x934000 > file 0x48000), `adt_pa=0x8828ac000`, `boot_args_pa=0x8828af000`, `topOfKernelData=0x8828b0000`, ends `=== A51SHIM6 jumping to XNU ===`; **`XNUELTE1` marker absent (grep 0) → fault inside `__start`**; phone bootlooped, operator recovered manually |

> **Evidence naming:** `backup/logs/pstore-boot9|boot10/` are the two *payload*
> boots (#9/#10 above). The older `backup/live/boot9|boot10-*.txt` files are two
> earlier diagnostic boots (#7/#8) — distinguish them by `initrd_size`:
> `0x1a0800` (stock ramdisk), `0x5e2800` (gzip payload), `0xfbc800` (raw payload).

### Boot 10 root cause: an 8-byte Device-memory store with the MMU off

The report stops at `=== A51SHIM6 jumping to XNU ===`, so the shim's job was
done: `x0 = 0x8828af000` (boot_args PA), MMU/caches off, `ic iallu`. The kernel
entry `__start` @ `0xfffffff007424488` is a single `b _start_first_cpu`
(@ `0xfffffff0074260dc`), whose first A5XELTE sequence is:

```asm
movk x10, #0xf9e9, lsl #16
movk x10, #0xc              ; x10 = 0xf9e9000c  (A5_ENTRY_MARKER_PHYS)
movk x11, #0x3145, lsl #48  ; ... "XNUELTE1"
str  x11, [x10]             ; 8-byte store, MMU still off → Device memory
dsb  sy
```

* With the MMU off every access is Device/Strongly-ordered, and **unaligned
  accesses to Device memory always raise a alignment fault** — `0xf9e9000c` is
  only 4-byte aligned (it was picked to be the first byte of the pstore record
  data, `RAMOOPS_HDR_SIZE` = 12).
* `VBAR_EL1` is programmed only *after* this store (start.s:135-137), so the
  fault lands on S-Boot's stale vectors → watchdog reset → bootloop.
* Corroborating: `grep XNUELTE1` on the pulled pmsg = 0 hits, the pmsg header is
  intact (a successful store would have overwritten `offset` and made pstore drop
  the record), and `llvm-objdump` shows this `str x11, [x10]` is the **only**
  8-byte store in the whole early path (`La5_draw_text` and the green block use
  `str w`). The S8/A5 marker addresses (`0x920ff000`, `0x469ff100`) are 8-byte
  aligned, which is why the donor never hit this.
* The shim never writes SCTLR (MMU/D-cache stay off as S-Boot left them), and
  `klog_init` maps the zone through `ml_io_map`, so the console path itself is
  sound.

**Fix (regenerated into 0125, both patch routes verified byte-identical):**
`A5_ENTRY_MARKER_PHYS` → `0xf9e90010` (still inside the record, 8-byte aligned;
rewrites the report's first line to `=== XNUELTE1 ===` at file offset 4).
Next boot (#11) must show `=== XNUELTE1 ===` in `pmsg-ramoops-0`, then
`--- XNU console ---` from `klog_init`, targeting "Waiting for root device".

### Ready for the kernel: one image, two behaviours

`shim/a51` now contains the payload handoff (`payload_boot.c`, adapted from the
proven `shim/loader.c` / `shim/a5xelte/xnu_boot.c`) and the same binary is both
the diagnostic and the loader:

* ramdisk without a `SWEETXNU` payload → register capture, DPU survey, read-only
  UFS probe, dashboard, 15 s trigger loop, PMU recovery reset (what runs today);
* ramdisk **with** a payload → log the payload header, copy the segments to
  `phys_base + (vmaddr - virt_base)`, place the ADT and `boot_args` behind the
  kernel, fill `boot_args.Video` with `0xca000000` / 1080×2400 / stride 4320,
  `deviceTreeP` as a VA, then `ic iallu` and jump with the MMU off.

`tools/a51/mkboot.js` gained `--ramdisk <payload> [--ramdisk-compress auto|gzip|raw]`
and rewrites the header's `ramdisk_size`; verified by repacking a dummy payload
and re-listing the result (kernel 53946 B raw, ramdisk gzipped, DTB and the
`SEANDROIDENFORCE` trailer untouched).

The initrd lands at `0x89000000`, inside bank 0, while the XNU window is bank 1
at `0x880000000` — `payload_boot()` refuses to run if they ever overlap.

---

Boot 5 replaced the colour bars with an instrument panel, because the screen is
the one channel a human can read without a host: the complete 16-bit RGB565
palette (65536 cells, 4×4 px each, 1024×1024 at y=452), a 16-step gray ramp plus
a continuous 256-step gradient, eight Bayer-4×4 ordered-dither halftones, R/G/B/
Y/W/K blocks to confirm channel order, the captured registers as hex text, and a
live counter band (T=, LOOPS=, FRAMES=, TE=, STAGE=) refreshed every 500 ms with
a shadow-update kick so it is visible proof that DECON keeps latching.

**Screen observation (boot 5, reported by the operator):** colours and palettes
were visible, and something at the bottom was blinking. That confirms the two
things the port depends on: writes anywhere in the 1080×2400 canvas at
`0xca000000` reach the panel, and the 500 ms counter band updates are latched
while the shim runs — i.e. DECON keeps scanning and re-latching without any
panel re-init, so XNU can redraw continuously and later flip pages. Channel
order (R/G/B vs B/G/R) was not positively identified and still needs a look at
the `R G B Y W K` row.

### Measured facts (from the A51SHIM5 record)

CPU / entry handoff from S-Boot:

| item | value | meaning |
|------|-------|---------|
| CurrentEL | `0x4` | EL1h |
| SCTLR_EL1 | `0x30c5083a` | bit0=0 → **MMU off**, caches off |
| MPIDR_EL1 | `0x80000000` | Aff0=0 → CPU0, Cortex-A53 little cluster |
| ID_AA64PFR0_EL1 | `0x2222` | EL0/EL1 AArch64-only, **no EL2/EL3** → GICv2, no virtualization |
| ID_AA64MMFR0_EL1 | `0x1122` | PARange=2 → **40-bit PA**; 4 KiB granule |
| CNTFRQ_EL0 | `0x18cba80` | 26 MHz, already set by S-Boot |
| FDT_PA | `0x8a100000` | stable across runs (bank 0) |
| IMAGE_PA | `0x800e8000`, then `0x80188000` | **not fixed** — S-Boot relocates; image must stay relocation-free |

Interrupt controller (`0x12301000` confirmed working — the `0x14af1000` "gicd"
in `/proc/iomem` is `abox_gic`, the audio GIC, not the AP one):

| reg | value | meaning |
|-----|-------|---------|
| GICD_TYPER | `0xfcef` | ITLines=15 → 512 INTIDs; CPUNumber=7 → **8 CPUs** |
| GICD_CTLR | `0x0` | **distributor disabled at handoff** — XNU must enable it |
| GICC_IIDR | `0x0202143b` | ARM GIC-400 (JEP106 0x43b) |

Storage:

| reg | value |
|-----|-------|
| UFS CAP @ `0x13520000` | `0x1303ff0f` |
| UFS VERSION @ `0x13520008` | `0x0210` (UFSHCI 2.1) |

Display — S-Boot leaves the pipeline running, so XNU can write `0xca000000`
directly and it is scanned out with no DECON programming at all:

| reg | value | meaning |
|-----|-------|---------|
| DECON GLOBAL_CONTROL | `0x113` | EN + RUN_STATUS + **OPERATION_MODE_CMD** (command mode) |
| DECON HW_SW_TRIG_CONTROL | `0x3010` → `0x3014` | our `TIMER_EN` write took effect; `SW_TRIG_EN`=0, `HW_TRIG_EN`=0, `HW_TRIG_MASK_DECON`=1 |
| DECON HW_TE_CNT | `0x02c602c6` → `0x062a065f` | TE ticking (~105/s) |
| DECON INTERRUPT_ENABLE / PENDING | `0x3011` / `0x3000` | frame start+done enabled |
| DECON SCALED_SIZE_CONTROL_0 | `0x09600438` | 2400 × 1080 |
| DECON WIN0 ctl0 | `0x102` | only window 0 in use |
| frame_done events in 15 s | 1151 (~77 Hz) | panel actively scanning |
| DSIM +0x00/+0x18/+0x20 | `0x02010000` / `0x20000000` / `0x04131f05` | DSI host up |

**Scanout channel (the one XNU will flip in Phase 4):**

```
dpp@0x14881000  IDMA_ENABLE=0x4 (OP_STATUS running)
                IN_CON=0x02000000  SRC_SIZE=0x09600438  SRC_OFFSET=0
                IMG_SIZE=0x09600438  IN_BASE_ADDR_Y=0xca000000  <== framebuffer
dpp@0x14891000  (second window of the same node) ENABLE=0x5 IMG_SIZE=0x09600438
all other DPP windows (14882000/14892000/14883000/14896000/14884000/14895000) idle
```

Note the first probe read `0x14884000` (dpp0 in DT order) and saw zeros; the live
channel is the node at `0x14881000`. Both register windows of each `dpp@` node
must be surveyed — `IN_BASE_ADDR_Y` is at `+0x40` of the **first** window.

### Channels that do NOT work (do not retry)

- `/dev/mem`: absent; `mknod c 1 1` gives `ENXIO` → `CONFIG_DEVMEM` off.
- `/proc/kcore`: absent.
- `sec_avc_log`/`sec_tsp_log`/`sec_disp_log`/`sec_debug_next` writes do not come
  back: `/proc/tsp_msg` only exposes the recovery touch driver's own 25 KiB, and
  the shim's run counter reads back 1 every boot, i.e. nothing persisted.
- AP RTC (`0x11a20000`): writes land (the shim reads back `41-11-11 11:11:11`)
  but `/proc/driver/rtc` still shows the PMIC time — `use-chub-only`, so the AP
  RTC is not the system RTC. Dead end.

### The channel that works

Forge a ramoops record. `lib/debug-snapshot-pstore.c` sizes the zones
`record=console=ftrace=pmsg=SZ_512K` inside `log_pstore` (`0xf9d10000`, 2 MiB),
and `fs/pstore/ram.c` lays them out in that order:

| zone | PA |
|------|----|
| dump0 (dmesg) | `0xf9d10000` |
| console | `0xf9d90000` |
| ftrace | `0xf9e10000` |
| pmsg | `0xf9e90000` |

Each zone starts with `struct persistent_ram_buffer { u32 sig=0x43474244;
s32 start; s32 size; u8 data[]; }` (ecc 0/0, so no parity bytes). A DMESG record
also needs the ASCII header `====<sec>.<nsec>-D\n` or `ramoops_pstore_read()`
zaps it. Result: `/sys/fs/pstore/dmesg-ramoops-0`. This is the evidence channel
for every later stage, including the XNU early console.

---

## Phases 5–9: iOS 16 userland on the A51 (2026-10-28/29) — lock screen, touch, USB, SSH

Device: SM-A515F, S-Boot `A515FXXU8HWI1`, TWRP 3.7.1 in recovery. Boot images are
written to boot (sda12) and verified by readback; the iOS root lives in userdata
(sda32, Android erased with the owner's consent). Host scripts:
`tools/a51/host/` (see its README). Kernel patches 0126–0137 build in CI
(`xnu.yml`); the loader is `shim/a51` built with
`CROSS=aarch64-linux-gnu- EXTRA_CFLAGS="-DDEADMAN_MS=900000UL -DDEADMAN_MPIDR=0x103UL"`.

| Boot | Result | Key change |
|---|---|---|
| 12 | XNU to "platform expert registered", AppleCLCD 1080x2400 | shim dead-man timer: CPU1 (PSCI CPU_ON after GICD_CTLR enable; Samsung PSCI does not pass the context id) warm-resets into TWRP via PMU INFORM + SWRESET, so pstore keeps the XNU console |
| 14 | sweet_ufs finds userdata, reads its superblock | read-only `rd=ufsroot0 ufsroot=userdata` |
| 15 | iOS 16.7.16 root mounts, launchd + ~290 xpcproxy launches | 16 GiB HFS+ root (`build-ios-root.sh`), `ufsrw=1 rootrw=1 launchd_unsecure_cache=1` |
| 16 | Apple logo on the panel | 0126: DECON command mode needs a software trigger after each copy |
| 19, 23 | 7/7 CPUs, no panic | 0127 (plain PSCI CPU_ON via the low trampoline), 0129 + ADT: one cluster (a second pset panicked "ticket lock unexpectedly owned") |
| 26/27 | **lock screen** | ADT identity (serial number, wlan/bluetooth MACs): without them lsd blocks in MGCopyAnswer(UniqueDeviceID); now in `mkadt_a51.py`. Boot-args `a5fb_noconsole=1 a5fb_keep_on=1` |
| 33 | **unlocked by touch** | 0131 Melfas MSS100 on the classic IIC 0x13870000 (gpg3-1 power), 0132 wake on first touch |
| 38 | **USB gadget** (1209:0001 "Sweet iOS (USB Ethernet)"), live kernel log over UDP | 0130 DWC3 + HS PHY bring-up, MUIC switch (`shim/a51/muic.c`); 0137: the A51 kernel's `phy_exynos_usb_v3p1_pipe_ovrd` (USBCON LINK_CTRL bits 16, 18 set, 17 clear; 0x48 bit 4). Without it the DWC3 never pulls D+ up (LINESTATE SE0) |
| 39 | **SSH** as root over USB (192.168.7.2) | `tools/sweet-root-ssh.py`, see below |

Touch recovery gesture (0133): holding the top-left corner (x, y < 100) for
3 s warm-resets into TWRP, so no button combination is needed.

### SSH (measured)

- Procursus (dists/1900, rootless) packages under `/private/var/jb`, ad-hoc
  signed, accepted by the A5XELTE AMFI policy. `tools/procursus-tree.py`
  resolves the closure, stops on missing dependencies and checks every Mach-O
  link: Procursus' dropbear needs libtomcrypt1, which the index lacks
  (crash loop "Library not loaded: @rpath/libtomcrypt.1.dylib"), so OpenSSH
  9.7p1 is used.
- Procursus binaries resolve users with libiosexec (`ie_getpwnam`), which
  reads only `/var/jb/etc/{spwd.db,pwd.db,group}`, never `/etc`. Without
  those, root is an invalid user and every login is refused (sshd still
  starts: its config has `UsePrivilegeSeparation no`). The sshd job runs
  `pwd_mkdb` first. Apple's own lookups (Libinfo) are not involved.
- iOS is reset, not shut down, so its HFS+ journal holds transactions; Linux
  never replays them. `tools/hfs-journal-replay.py` replays them (as XNU does)
  before the image is edited; hfsprogs 540 fsck wrongly reports the
  little-endian journal as empty.
- Edits go through `sync-userdata.py`: only 64 MiB chunks that differ are
  pulled/pushed (a pull is ~8 min of hashing on the phone).

### Known issues / next

- UI is slow: CoreAnimation renders on the CPU; frames are copied to the
  DECON command-mode framebuffer (`sweetfb_hz`). GPU (Mali-G72 MP3) is next.
- locationd, WirelessRadioManagerd, PosterBoard crash (no baseband, Wi-Fi,
  GPU); usermanagerd fails its multi-user setup ("FAILED to Set Foreground ID").
- One core (MPIDR 0x103) is kept for the dead-man timer (`cpus=7`).
