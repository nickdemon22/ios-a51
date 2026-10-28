/* SPDX-License-Identifier: GPL-2.0-only
 * Galaxy A51: read the S2MPU09 PMIC over Samsung's SPEEDY bus (0x11a10000,
 * live DT speedy@11a10000). The USB PHY's supplies are LDO12/13/14
 * (dwc3 ldo-names vdd_ldo12/13/14; TWRP: 0.85 V / 1.8 V / 3.0 V, enabled by
 * the USB driver). SPEEDY sequence from Samsung's bootloader
 * (lk3rd dev/speedy/speedy_multi.c, random access, polled); registers from
 * the Exynos 9611 kernel (s2mpu09-regulator.h: L12CTRL 0x45.., mode bits
 * [7:6], vsel [5:0]). This file only reads.
 */
#include <stdint.h>
#include "report.h"

#define SPEEDY_PA               0x11a10000UL
#define SPEEDY_CTRL             0x000
#define SPEEDY_FIFO_CTRL        0x004
#define SPEEDY_CMD              0x008
#define SPEEDY_INT_ENABLE       0x00c
#define SPEEDY_INT_STATUS       0x010
#define SPEEDY_RX_DATA          0x038
#define SPEEDY_ENABLE           (1u << 0)
#define SPEEDY_FIFO_RESET       (1u << 31)
#define SPEEDY_TRANSFER_DONE    (1u << 0)
#define SPEEDY_ACCESS_RANDOM    (1u << 19)
#define SPEEDY_DIRECTION_READ   (0u << 20)
#define SPEEDY_RX_ALMOST_FULL_EN (1u << 8)
#define SPEEDY_RX_TRAILER_EN    (1u << 9)
#define SPEEDY_TIMEOUT_EN       (7u << 1)       /* cmd, standby, data */

#define PMIC_SLAVE              0x01            /* I2C_ADDR_PMIC */

static inline uint32_t rd(uintptr_t a) { return *(volatile uint32_t *)a; }
static inline void wr(uintptr_t a, uint32_t v)
{
	*(volatile uint32_t *)a = v;
	__asm__ volatile("dsb sy" ::: "memory");
}

static void spin_us(uint64_t us)
{
	uint64_t t0, t, frq;
	__asm__ volatile("mrs %0, cntfrq_el0" : "=r"(frq));
	__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(t0));
	do
		__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(t));
	while (t - t0 < us * (frq / 1000000UL));
}

/* One random-access read; value 0..255, or -1 on timeout. */
static int speedy_read(uint8_t slave, uint8_t reg)
{
	uint32_t addr = reg | ((uint32_t)(slave & 0xf) << 8);
	wr(SPEEDY_PA + SPEEDY_FIFO_CTRL, rd(SPEEDY_PA + SPEEDY_FIFO_CTRL) | SPEEDY_FIFO_RESET);
	spin_us(10);
	/* RX/TX trigger level 1 for random access */
	wr(SPEEDY_PA + SPEEDY_FIFO_CTRL, (rd(SPEEDY_PA + SPEEDY_FIFO_CTRL) & ~0xffffu) | 1u | (1u << 8));
	wr(SPEEDY_PA + SPEEDY_INT_STATUS, 0xffffffffu);
	wr(SPEEDY_PA + SPEEDY_INT_ENABLE, SPEEDY_TIMEOUT_EN | SPEEDY_RX_ALMOST_FULL_EN | SPEEDY_RX_TRAILER_EN);
	wr(SPEEDY_PA + SPEEDY_CMD, ((addr & 0xfff) << 7) | SPEEDY_ACCESS_RANDOM | SPEEDY_DIRECTION_READ);
	for (int i = 0; i < 10000; i++) {
		if (rd(SPEEDY_PA + SPEEDY_INT_STATUS) & SPEEDY_TRANSFER_DONE) {
			int v = (int)(rd(SPEEDY_PA + SPEEDY_RX_DATA) & 0xffu);
			wr(SPEEDY_PA + SPEEDY_INT_STATUS, 0xffffffffu);
			return v;
		}
		spin_us(1);
	}
	rkv("speedy_timeout_int_status", rd(SPEEDY_PA + SPEEDY_INT_STATUS));
	wr(SPEEDY_PA + SPEEDY_INT_STATUS, 0xffffffffu);
	return -1;
}

int a51_pmic_probe(void)
{
	rkv("speedy_ctrl", rd(SPEEDY_PA + SPEEDY_CTRL));
	rkv("speedy_fifo_ctrl", rd(SPEEDY_PA + SPEEDY_FIFO_CTRL));
	if (!(rd(SPEEDY_PA + SPEEDY_CTRL) & SPEEDY_ENABLE))
		wr(SPEEDY_PA + SPEEDY_CTRL, rd(SPEEDY_PA + SPEEDY_CTRL) | SPEEDY_ENABLE);
	int l12 = speedy_read(PMIC_SLAVE, 0x45);
	int l13 = speedy_read(PMIC_SLAVE, 0x46);
	int l14 = speedy_read(PMIC_SLAVE, 0x47);
	rkv("pmic_l12ctrl", (uint64_t)(int64_t)l12);   /* expect 0xcc when on: 0.85 V */
	rkv("pmic_l13ctrl", (uint64_t)(int64_t)l13);   /* expect 0xec when on: 1.80 V */
	rkv("pmic_l14ctrl", (uint64_t)(int64_t)l14);   /* expect 0xf0 when on: 3.00 V */
	rkv("pmic_rev_top00", (uint64_t)(int64_t)speedy_read(0x00, 0x00));
	return (l12 < 0 || l13 < 0 || l14 < 0) ? -1 : 0;
}
