/* SPDX-License-Identifier: GPL-2.0-only
 * A51 XNU payload handoff: act as iBoot for the SWEETXNU payload that
 * tools/mkpayload.py puts in the boot image's ramdisk.
 *
 * S-Boot decompresses the ramdisk and advertises it through
 * /chosen/linux,initrd-{start,end} (measured on this device: the OrangeFox
 * ramdisk showed up as 0x89000000-0x89edb800), so the payload is found exactly
 * the way the SWEET and a5xelte loaders find it.
 *
 * Layout work is the donor's, proven one: copy each segment to
 * phys_base + (vmaddr - virt_base), put the ADT and then boot_args behind the
 * kernel image, and jump to the entry point with the MMU and caches off and
 * x0 = boot_args physical address.
 *
 * A51 board facts used here are the measured ones: 4 KiB pages, framebuffer
 * 0xca000000 1080x2400 stride 4320, and the kernel RAM window comes from the
 * payload header (tools/mkadt_a51.py sets dram-base 0x880000000).
 */
#include <stdint.h>
#include "report.h"
#include "../fdt.h"

#define BOOT_LINE_LENGTH	608
#define PAGE_SIZE		4096UL

#define FB_PHYS			0xca000000UL
#define FB_WIDTH		1080U
#define FB_HEIGHT		2400U
#define FB_STRIDE		(FB_WIDTH * 4U)

struct payload_seg {
	uint64_t vmaddr, vmsize, file_off, file_size;
};

struct payload {
	char magic[8];
	uint32_t version, nsegs;
	uint64_t virt_base, entry_va, adt_off, adt_size, kernel_end;
	uint64_t phys_base, mem_size;
	char cmdline[BOOT_LINE_LENGTH];
	struct payload_seg segs[];
};

/* pexpert/pexpert/arm64/boot.h, Revision/Version 2 */
struct boot_video {
	uint64_t v_baseAddr, v_display, v_rowBytes, v_width, v_height, v_depth;
};

struct boot_args {
	uint16_t Revision, Version;
	uint64_t virtBase, physBase, memSize, topOfKernelData;
	struct boot_video Video;
	uint32_t machineType;
	uint64_t deviceTreeP;
	uint32_t deviceTreeLength;
	char CommandLine[BOOT_LINE_LENGTH];
	uint64_t bootFlags, memSizeActual;
};

_Static_assert(__builtin_offsetof(struct payload, segs) == 0x2a8, "payload header layout");
_Static_assert(__builtin_offsetof(struct boot_args, Video) == 40, "boot_args layout");
_Static_assert(__builtin_offsetof(struct boot_args, CommandLine) == 108, "boot_args layout");
_Static_assert(sizeof(struct boot_args) == 736, "boot_args layout");

/* MMU off: everything is Device memory, so copy in aligned 8-byte words. */
static void copy64(uintptr_t dst, uintptr_t src, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	volatile const uint64_t *s = (volatile const uint64_t *)src;
	for (uint64_t i = 0; i < (n + 7) / 8; i++)
		d[i] = s[i];
}

static void zero64(uintptr_t dst, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	for (uint64_t i = 0; i < n / 8; i++)
		d[i] = 0;
}

static int magic_ok(const char *m)
{
	static const char want[] = "SWEETXNU";
	for (int i = 0; i < 8; i++)
		if (m[i] != want[i])
			return 0;
	return 1;
}

typedef void (*xnu_entry_t)(uint64_t boot_args_pa);

/* Dead-man timer (deadman.S): CPU1 forces a warm recovery reset this long
 * after the handoff, so a hung kernel still leaves its log readable. */
#ifndef DEADMAN_FALLBACK_MS
#define DEADMAN_FALLBACK_MS	10000UL
#endif
#ifndef DEADMAN_MS
#define DEADMAN_MS		60000UL
#endif
#ifndef DEADMAN_MPIDR
#define DEADMAN_MPIDR		0x1UL     /* any core the kernel will not start */
#endif
#define TIMER_HZ		26000000UL
#define PSCI_CPU_ON_64		0xc4000003UL

extern void deadman_entry(void);
extern int a51_muic_usb(void);
extern int a51_touch_probe(void);
extern int a51_pmic_probe(void);
extern void deadman_probe(uint64_t out[4]);
extern volatile uint64_t deadman_alive[13];
extern uint64_t cpu_mhz(void);
extern volatile uint64_t deadman_deadline_cell;

static int64_t psci_cpu_on(uint64_t mpidr, uint64_t entry, uint64_t ctx)
{
	register uint64_t x0 __asm__("x0") = PSCI_CPU_ON_64;
	register uint64_t x1 __asm__("x1") = mpidr;
	register uint64_t x2 __asm__("x2") = entry;
	register uint64_t x3 __asm__("x3") = ctx;
	__asm__ volatile("smc #0" : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3) : : "memory");
	return (int64_t)x0;
}

#define GICD_CTLR		0x12301000UL

static void start_deadman(void)
{
	uint64_t now;
	/* S-Boot leaves the GIC-400 distributor off (GICD_CTLR = 0), and with
	 * it off CPU_ON returns 0 but the core never runs (boot 11g; with it on,
	 * boot 11h). Both CPUs keep DAIF masked, and XNU re-initialises the GIC. */
	rkv("gicd_ctlr_before", *(volatile uint32_t *)GICD_CTLR);
	*(volatile uint32_t *)GICD_CTLR = 3;
	__asm__ volatile("dsb sy" ::: "memory");
	rkv("gicd_ctlr_after", *(volatile uint32_t *)GICD_CTLR);
	__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(now));
	const uint64_t deadline = now + DEADMAN_MS * (TIMER_HZ / 1000UL);
	deadman_deadline_cell = deadline;
	__asm__ volatile("dsb sy" ::: "memory");
	rkv("deadman_deadline", deadline);
	rkv("deadman_psci_ret",
	    (uint64_t)psci_cpu_on(DEADMAN_MPIDR, (uintptr_t)deadman_entry, deadline));
}

int payload_present(uintptr_t fdt, uint64_t *rd_start_out)
{
	uint64_t rd_start, rd_end;

	if (!fdt_chosen_u64(fdt, "linux,initrd-start", &rd_start) ||
	    !fdt_chosen_u64(fdt, "linux,initrd-end", &rd_end))
		return 0;
	if (rd_end <= rd_start || rd_end - rd_start < sizeof(struct payload))
		return 0;
	if (!magic_ok(((const struct payload *)(uintptr_t)rd_start)->magic))
		return 0;
	if (rd_start_out)
		*rd_start_out = rd_start;
	return 1;
}

/* Never returns on success. On failure it reports why and returns non-zero so
 * the caller can fall back to the diagnostic dashboard. */
int payload_boot(uintptr_t fdt)
{
	uint64_t rd_start = 0, rd_end = 0;

	if (!fdt_chosen_u64(fdt, "linux,initrd-start", &rd_start) ||
	    !fdt_chosen_u64(fdt, "linux,initrd-end", &rd_end)) {
		rputs("payload: no initrd in /chosen\n");
		return 1;
	}
	rkv("initrd_start", rd_start);
	rkv("initrd_end", rd_end);

	const struct payload *p = (const struct payload *)(uintptr_t)rd_start;
	if (!magic_ok(p->magic)) {
		rputs("payload: no SWEETXNU magic in the ramdisk\n");
		return 2;
	}
	if (p->version != 1 || (p->adt_off & 7) || (rd_start & 7) || !p->nsegs ||
	    p->nsegs > 32) {
		rputs("payload: bad header\n");
		rkv("version", p->version);
		rkv("nsegs", p->nsegs);
		return 3;
	}

	const uint64_t pa_of_va_base = p->phys_base - p->virt_base;
	rkv("phys_base", p->phys_base);
	rkv("virt_base", p->virt_base);
	rkv("mem_size", p->mem_size);
	rkv("entry_va", p->entry_va);
	rkv("kernel_end", p->kernel_end);
	rkv("adt_off", p->adt_off);
	rkv("adt_size", p->adt_size);

	if (rd_end > p->phys_base && rd_start < p->phys_base + p->mem_size) {
		rputs("payload: ramdisk overlaps the kernel window\n");
		return 4;
	}

	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *s = &p->segs[i];
		uintptr_t dst = (uintptr_t)(s->vmaddr + pa_of_va_base);
		rkv("seg vmaddr", s->vmaddr);
		rkv("seg dst pa", dst);
		rkv("seg file_size", s->file_size);
		rkv("seg vmsize", s->vmsize);
		copy64(dst, (uintptr_t)rd_start + s->file_off, s->file_size);
		if (s->vmsize > s->file_size) {
			uint64_t used = (s->file_size + 7) & ~7UL;
			zero64(dst + used, s->vmsize - used);
		}
	}

	const uintptr_t adt_pa = (uintptr_t)(p->kernel_end + pa_of_va_base);
	copy64(adt_pa, (uintptr_t)rd_start + p->adt_off, p->adt_size);
	const uintptr_t ba_pa = (adt_pa + p->adt_size + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1);
	const uintptr_t top = ba_pa + PAGE_SIZE;
	rkv("adt_pa", adt_pa);
	rkv("boot_args_pa", ba_pa);
	rkv("topOfKernelData", top);

	zero64(ba_pa, PAGE_SIZE);
	volatile struct boot_args *ba = (volatile struct boot_args *)ba_pa;
	ba->Revision = 2;
	ba->Version = 2;
	ba->virtBase = p->virt_base;
	ba->physBase = p->phys_base;
	ba->memSize = p->mem_size;
	ba->topOfKernelData = top;
	ba->Video.v_baseAddr = FB_PHYS;
	ba->Video.v_display = 1;
	ba->Video.v_rowBytes = FB_STRIDE;
	ba->Video.v_width = FB_WIDTH;
	ba->Video.v_height = FB_HEIGHT;
	ba->Video.v_depth = 32;
	ba->machineType = 0;
	/* XNU reads the ADT with the MMU on, so this is a VA, not a PA. */
	ba->deviceTreeP = adt_pa - pa_of_va_base;
	ba->deviceTreeLength = (uint32_t)p->adt_size;
	for (int i = 0; i < BOOT_LINE_LENGTH; i++)
		ba->CommandLine[i] = p->cmdline[i];
	ba->bootFlags = 0;
	ba->memSizeActual = p->mem_size;

#ifdef DEADMAN_ONLY
	/* Test build: prove the dead-man reset without entering the kernel. If
	 * CPU1 never fires, the boot CPU runs the same reset itself
	 * DEADMAN_FALLBACK_MS later, so the report always comes back through a
	 * warm reset. */
	{
		/* Coherency controls of the boot CPU (CPUECTLR bit 6 = SMPEN). */
		uint64_t probe[4];
		deadman_probe(probe);
		rkv("cpu0_cpuectlr", probe[0]);
		rkv("cpu0_cpuectlr_trap_esr", probe[1]);
		rkv("cpu0_cpuactlr", probe[2]);
		rkv("cpu0_cpuactlr_trap_esr", probe[3]);
	}
	start_deadman();
	{
		uint64_t t0, t;
		__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(t0));
		do
			__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(t));
		while (deadman_alive[0] == 0 && t - t0 < TIMER_HZ);
		rkv("cpu1_alive_magic", deadman_alive[0]);
		rkv("cpu1_currentel", deadman_alive[1]);
		rkv("cpu1_sctlr", deadman_alive[3]);
		rkv("cpu1_mpidr", deadman_alive[4]);
		rkv("cpu1_cpuectlr", deadman_alive[8]);
		rkv("cpu1_cpuectlr_trap_esr", deadman_alive[9]);
		rkv("cpu1_cpuactlr", deadman_alive[10]);
		rkv("cpu1_cpuactlr_trap_esr", deadman_alive[11]);
		rkv("deadman_core_mhz", deadman_alive[12]);
		rkv("boot_core_mhz", cpu_mhz());
	}
#ifdef USB_PROBE
	{
		/* Read-only: is the A51's USB (DWC3 at 0x13200000, USB2 PHY at
		 * 0x131d0000, PMU PHY control 0x11860704) left powered and clocked
		 * by S-Boot? A gated block may hang the read; the marker before
		 * each step shows how far it got, and CPU1 still resets. */
		/* Inline steps, not a table of string pointers: the shim must stay
		 * free of absolute relocations (build.sh refuses them). */
#define USB_PROBE_READ(name, pa) do {				\
			append_marker(name);				\
			emit_report();					\
			rkv(name, *(volatile uint32_t *)(uintptr_t)(pa));	\
		} while (0)
		USB_PROBE_READ("pmu_usb_phy_ctrl", 0x11860704UL);
		USB_PROBE_READ("phy_0x00", 0x131d0000UL);
		USB_PROBE_READ("phy_0x04", 0x131d0004UL);
		USB_PROBE_READ("phy_0x08", 0x131d0008UL);
		USB_PROBE_READ("phy_0x0c", 0x131d000cUL);
		USB_PROBE_READ("phy_0x10", 0x131d0010UL);
		USB_PROBE_READ("phy_0x14", 0x131d0014UL);
		USB_PROBE_READ("phy_0x18", 0x131d0018UL);
		USB_PROBE_READ("phy_0x1c", 0x131d001cUL);
		USB_PROBE_READ("phy_0x30", 0x131d0030UL);
		USB_PROBE_READ("phy_0x34", 0x131d0034UL);
		USB_PROBE_READ("dwc3_gsnpsid", 0x1320c120UL);
		USB_PROBE_READ("dwc3_ghwparams0", 0x1320c140UL);
		USB_PROBE_READ("dwc3_gctl", 0x1320c110UL);
		USB_PROBE_READ("dwc3_gusb2phycfg", 0x1320c200UL);
		USB_PROBE_READ("dwc3_dctl", 0x1320c704UL);
		USB_PROBE_READ("dwc3_dsts", 0x1320c70cUL);
#undef USB_PROBE_READ
	}
#endif
#ifdef PMIC_PROBE
	append_marker("pmic");
	emit_report();
	rkv("pmic_result", (uint64_t)(int64_t)a51_pmic_probe());
#endif
#ifdef USBCLK_PROBE
	{
		/* Read-only: USB clocks at handoff (exynos9610 cmucal-sfr.c):
		 * CMU_TOP 0x12100000 USB bus/USB30DRD mux, div, gate; CMU_USB
		 * 0x13030000 user muxes, gates (MANUAL bit 20, CG_VAL bit 21) and
		 * q-channels; then the USBCON PHY block. */
#define USBCLK_READ(name, pa) do {				\
			append_marker(name);				\
			emit_report();					\
			rkv(name, *(volatile uint32_t *)(uintptr_t)(pa));	\
		} while (0)
		USBCLK_READ("top_mux_usb_bus", 0x12101084UL);
		USBCLK_READ("top_div_usb_bus", 0x1210188cUL);
		USBCLK_READ("top_mux_usb30drd", 0x1210108cUL);
		USBCLK_READ("top_div_usb30drd", 0x12101894UL);
		USBCLK_READ("top_gate_usb30drd", 0x12102098UL);
		USBCLK_READ("usb_user_bus", 0x13030100UL);
		USBCLK_READ("usb_user_usb30drd", 0x13030140UL);
		USBCLK_READ("usb_ctrl_option", 0x13030800UL);
		USBCLK_READ("usb_g_oscrst", 0x13032000UL);
		USBCLK_READ("usb_g_phyctrl20", 0x13032034UL);
		USBCLK_READ("usb_g_phyctrl30_0", 0x13032038UL);
		USBCLK_READ("usb_g_bus_early", 0x13032040UL);
		USBCLK_READ("usb_g_ref_clk", 0x13032044UL);
		USBCLK_READ("usb_q_usb30", 0x13033028UL);
		USBCLK_READ("usb_q_phy20", 0x1303302cUL);
		USBCLK_READ("usb_q_phy30_0", 0x13033030UL);
		USBCLK_READ("usbcon_00", 0x131d0000UL);
		USBCLK_READ("usbcon_clkrst", 0x131d0004UL);
		USBCLK_READ("usbcon_pwr", 0x131d0024UL);
		USBCLK_READ("usbcon_link_ctrl", 0x131d0034UL);
		USBCLK_READ("usbcon_utmi", 0x131d0050UL);
		USBCLK_READ("usbcon_hsp", 0x131d0054UL);
		USBCLK_READ("usbcon_hsp_tune", 0x131d0058UL);
		USBCLK_READ("usbcon_hsp_test", 0x131d005cUL);
		USBCLK_READ("pmu_usb_phy", 0x11860704UL);
	}
#endif
#ifdef GPU_PROBE
	{
		/* Read-only: Mali-G72 at 0x11500000 (live DT mali@11500000), power
		 * domain pd-g3d: PMU G3D_CONFIGURATION 0x4060 / STATUS 0x4064
		 * (gpu_pmu_status_reg_offset, local_pwr_mask 0xf). The GPU's own
		 * registers are read only if the domain reports on. */
#define GPU_PROBE_READ(name, pa) do {				\
			append_marker(name);				\
			emit_report();					\
			rkv(name, *(volatile uint32_t *)(uintptr_t)(pa));	\
		} while (0)
		GPU_PROBE_READ("pmu_g3d_conf", 0x11864060UL);
		GPU_PROBE_READ("pmu_g3d_status", 0x11864064UL);
		if ((*(volatile uint32_t *)(uintptr_t)0x11864064UL & 0xfu) == 0xfu) {
			GPU_PROBE_READ("gpu_id", 0x11500000UL);
			GPU_PROBE_READ("gpu_l2_features", 0x11500004UL);
			GPU_PROBE_READ("gpu_tiler_features", 0x1150000cUL);
			GPU_PROBE_READ("gpu_mem_features", 0x11500010UL);
			GPU_PROBE_READ("gpu_mmu_features", 0x11500014UL);
			GPU_PROBE_READ("gpu_as_present", 0x11500018UL);
			GPU_PROBE_READ("gpu_js_present", 0x1150001cUL);
			GPU_PROBE_READ("gpu_irq_rawstat", 0x11500020UL);
			GPU_PROBE_READ("gpu_status", 0x11500034UL);
			GPU_PROBE_READ("gpu_shader_present", 0x11500100UL);
			GPU_PROBE_READ("gpu_tiler_present", 0x11500110UL);
			GPU_PROBE_READ("gpu_l2_present", 0x11500120UL);
			GPU_PROBE_READ("gpu_shader_ready", 0x11500140UL);
			GPU_PROBE_READ("gpu_tiler_ready", 0x11500150UL);
			GPU_PROBE_READ("gpu_l2_ready", 0x11500160UL);
			GPU_PROBE_READ("gpu_coherency_features", 0x11500300UL);
			GPU_PROBE_READ("gpu_thread_max_threads", 0x115000a0UL);
			GPU_PROBE_READ("gpu_texture_features0", 0x115000b0UL);
		}
#undef GPU_PROBE_READ
	}
#endif
#ifdef TOUCH_PROBE
	append_marker("touch");
	emit_report();
	rkv("touch_result", (uint64_t)(int64_t)a51_touch_probe());
#endif
#ifdef MUIC_USB
	append_marker("muic");
	emit_report();
	rkv("muic_result", (uint64_t)(int64_t)a51_muic_usb());
#endif
	append_marker("deadman armed, not jumping");
	emit_report();
	{
		uint64_t now;
		__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(now));
		deadman_deadline_cell = now + (DEADMAN_MS + DEADMAN_FALLBACK_MS) * (TIMER_HZ / 1000UL);
		__asm__ volatile("dsb sy" ::: "memory");
		deadman_entry();
	}
#endif
#if defined(MUIC_USB) && !defined(DEADMAN_ONLY)
	/* USB-C data lines to the AP's PHY before the kernel's USB gadget runs. */
	rkv("muic_result", (uint64_t)(int64_t)a51_muic_usb());
#endif
	append_marker("jumping to XNU");
	emit_report();
	log_field(20, 10);

	const uintptr_t entry_pa = (uintptr_t)(p->entry_va + pa_of_va_base);
	rkv("entry_pa", entry_pa);

	/* Armed last, after the final report write, so a short DEADMAN_MS
	 * measures the kernel alone. Its rkv lines are not emitted. */
#ifndef DEADMAN_ONLY
	start_deadman();
#endif

	/* Everything was written with the MMU and caches off; drop stale
	 * I-cache lines so the kernel's own text is what we just copied. */
	__asm__ volatile("dsb sy; ic iallu; dsb sy; isb" ::: "memory");
	((xnu_entry_t)entry_pa)(ba_pa);

	rputs("payload: XNU returned!\n");
	append_marker("xnu returned");
	emit_report();
	return 5;
}
