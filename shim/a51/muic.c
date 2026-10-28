/* SPDX-License-Identifier: GPL-2.0-only
 * Galaxy A51: route the USB-C data lines to the AP's USB PHY.
 *
 * The connector's D+/D- go through the SM5713 MUIC (I2C 0x25 on the classic
 * Samsung IIC at 0x13840000, live DT i2c@13840000 / sm5713@49). S-Boot leaves
 * its switch open (TWRP's driver log at probe: cntl 0x34, mansw 0x00); Linux
 * then connects USB with MANUAL_SW = 0x09 (DM/DP = USB) and CFG1 |= 0x04
 * (manual switching), see LineageOS gta4xl drivers/muic/sm5713-muic.c
 * com_to_usb()/set_manual_sw(). Without it the DWC3 runs but its link state
 * stays "disconnected" (boots 28/29).
 *
 * The IIC sequence follows Samsung's bootloader driver (lk3rd
 * dev/i2c/exynos_i2c.c), polled: TXRXEN|MASTER_TX, address byte, START,
 * wait IRQPEND + ACK per byte, STOP; reads use a repeated START in
 * MASTER_RX with ACK disabled before the last byte.
 */
#include <stdint.h>
#include "report.h"

#define MUIC_IIC_PA     0x13840000UL
static uintptr_t IIC_PA = MUIC_IIC_PA;
#define IICCON          0x00
#define IICSTAT         0x04
#define IICDS           0x0c
#define IICCON_ACKEN    (1u << 7)
#define IICCON_TXDIV512 (1u << 6)
#define IICCON_IRQEN    (1u << 5)
#define IICCON_IRQPEND  (1u << 4)
#define IICSTAT_MASTER_RX (2u << 6)
#define IICSTAT_MASTER_TX (3u << 6)
#define IICSTAT_START   (1u << 5)       /* write: START/STOP; read: bus busy */
#define IICSTAT_TXRXEN  (1u << 4)
#define IICSTAT_LASTBIT (1u << 0)       /* 1 = no ACK */

#define GPP0CON_PA      0x139b0000UL    /* pinctrl@139B0000 bank gpp0 */
#define GPP0_I2C_PINS   (0xffu << 8)    /* gpp0-2 SDA, gpp0-3 SCL */
#define GPP0_I2C_FUNC   (0x22u << 8)    /* function 2 = I2C */

#define MUIC_ADDR       0x25
#define MUIC_CNTL       0x05
#define MUIC_MANUAL_SW  0x06
#define MUIC_CFG1       0x68
#define MANUAL_SW_USB   0x09            /* DM [5:3] = 1, DP [2:0] = 1 */
#define CFG1_MANUAL_SW  (1u << 2)

static inline uint32_t rd(uintptr_t a) { return *(volatile uint32_t *)a; }
static inline void wr(uintptr_t a, uint32_t v)
{
	*(volatile uint32_t *)a = v;
	__asm__ volatile("dsb sy" ::: "memory");
}

static void udelay(uint64_t us)
{
	uint64_t t0, t, frq;
	__asm__ volatile("mrs %0, cntfrq_el0" : "=r"(frq));
	__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(t0));
	do
		__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(t));
	while (t - t0 < us * (frq / 1000000UL));
}

/* Wait for the byte-done flag; 0 = done, -1 = timeout. */
static int iic_wait(void)
{
	for (int i = 0; i < 5000; i++) {
		if (rd(IIC_PA + IICCON) & IICCON_IRQPEND)
			return 0;
		udelay(10);
	}
	return -1;
}

static void iic_resume(void)
{
	wr(IIC_PA + IICCON, rd(IIC_PA + IICCON) & ~IICCON_IRQPEND);
}

static void iic_stop(uint32_t mode)
{
	wr(IIC_PA + IICSTAT, mode | IICSTAT_TXRXEN);    /* START bit clear = STOP */
	iic_resume();
	for (int i = 0; i < 400 && (rd(IIC_PA + IICSTAT) & IICSTAT_START); i++)
		udelay(10);
}

/*
 * START + address byte in the given mode; 0 = acked. For the second message
 * of a read (restart) the previous byte's IRQPEND is still set, exactly as in
 * the bootloader's i2c_message_start(): set up the new START first, then
 * clear the pending flag so the controller proceeds.
 */
static int iic_start(uint32_t mode, uint8_t addr_byte, int restart)
{
	if (!restart)
		for (int i = 0; i < 400 && (rd(IIC_PA + IICSTAT) & IICSTAT_START); i++)
			udelay(10);
	wr(IIC_PA + IICSTAT, mode | IICSTAT_TXRXEN);
	*(volatile uint8_t *)(IIC_PA + IICDS) = addr_byte;
	udelay(1);
	wr(IIC_PA + IICCON, rd(IIC_PA + IICCON) | IICCON_ACKEN | IICCON_IRQEN);
	wr(IIC_PA + IICSTAT, mode | IICSTAT_TXRXEN | IICSTAT_START);
	iic_resume();
	if (iic_wait())
		return -1;
	return (mode == IICSTAT_MASTER_TX && (rd(IIC_PA + IICSTAT) & IICSTAT_LASTBIT)) ? -2 : 0;
}

/* Transmit one byte in master TX; 0 = acked. */
static int iic_tx(uint8_t b)
{
	*(volatile uint8_t *)(IIC_PA + IICDS) = b;
	udelay(1);
	iic_resume();
	if (iic_wait())
		return -1;
	return (rd(IIC_PA + IICSTAT) & IICSTAT_LASTBIT) ? -2 : 0;
}

static int muic_write(uint8_t reg, uint8_t val)
{
	int r = iic_start(IICSTAT_MASTER_TX, MUIC_ADDR << 1, 0);
	if (!r)
		r = iic_tx(reg);
	if (!r)
		r = iic_tx(val);
	iic_stop(IICSTAT_MASTER_TX);
	return r;
}

/* Register read; value in 0..255, negative on error. */
static int muic_read(uint8_t reg)
{
	int r = iic_start(IICSTAT_MASTER_TX, MUIC_ADDR << 1, 0);
	if (!r)
		r = iic_tx(reg);
	if (r) {
		iic_stop(IICSTAT_MASTER_TX);
		return r - 10;
	}
	/* Repeated START in master RX; one byte, so no ACK after it. */
	r = iic_start(IICSTAT_MASTER_RX, (MUIC_ADDR << 1) | 1, 1);
	if (r) {
		iic_stop(IICSTAT_MASTER_RX);
		return r - 20;
	}
	wr(IIC_PA + IICCON, rd(IIC_PA + IICCON) & ~IICCON_ACKEN);
	iic_resume();
	if (iic_wait()) {
		iic_stop(IICSTAT_MASTER_RX);
		return -30;
	}
	int v = *(volatile uint8_t *)(IIC_PA + IICDS);
	iic_stop(IICSTAT_MASTER_RX);
	return v;
}

/* Generic transfer: write w[0..wlen), then (if rlen) repeated START and read
 * r[0..rlen) with ACK on every byte but the last. 0 on success. */
static int iic_xfer(uint8_t addr, const uint8_t *w, unsigned wlen, uint8_t *r, unsigned rlen)
{
	int e = iic_start(IICSTAT_MASTER_TX, addr << 1, 0);
	for (unsigned i = 0; !e && i < wlen; i++)
		e = iic_tx(w[i]);
	if (e || !rlen) {
		iic_stop(IICSTAT_MASTER_TX);
		return e;
	}
	e = iic_start(IICSTAT_MASTER_RX, (addr << 1) | 1, 1);
	if (e) {
		iic_stop(IICSTAT_MASTER_RX);
		return e - 20;
	}
	for (unsigned i = 0; i < rlen; i++) {
		if (i == rlen - 1)
			wr(IIC_PA + IICCON, rd(IIC_PA + IICCON) & ~IICCON_ACKEN);
		iic_resume();
		if (iic_wait()) {
			iic_stop(IICSTAT_MASTER_RX);
			return -30;
		}
		r[i] = *(volatile uint8_t *)(IIC_PA + IICDS);
	}
	iic_stop(IICSTAT_MASTER_RX);
	return 0;
}

/*
 * Touch bring-up check (read-only on the chip): Melfas MSS100 at 0x48 on the
 * classic IIC at 0x13870000 (live DT i2c@13870000 / mms_ts@48). Pins gpp1-0
 * SDA / gpp1-1 SCL, power enable gpg3-1 (tsp_ldo_en, regulator-boot-on),
 * interrupt gpa0-0 (low while events are queued). MIP4 registers from the
 * A51 kernel's melfas_mss100_reg.h.
 */
#define TOUCH_IIC_PA    0x13870000UL
#define GPP1CON_PA      0x139b0020UL
#define GPG3CON_PA      0x139b0120UL
#define GPA0CON_PA      0x11850000UL
#define MMS_ADDR        0x48

/* Little-endian 64-bit value from bytes: no unaligned loads (MMU off). */
static uint64_t le64(const uint8_t *b)
{
	uint64_t v = 0;
	for (int i = 7; i >= 0; i--)
		v = (v << 8) | b[i];
	return v;
}

int a51_touch_probe(void)
{
	uint8_t w[2], name[16] = {0}, ver[8] = {0}, fmt[4] = {0}, ready = 0, info = 0;
	uint32_t gpp1 = rd(GPP1CON_PA);
	rkv("touch_gpp1con", gpp1);
	rkv("touch_gpg3con", rd(GPG3CON_PA));
	rkv("touch_gpg3dat", rd(GPG3CON_PA + 4));
	rkv("touch_gpa0con", rd(GPA0CON_PA));
	rkv("touch_gpa0dat", rd(GPA0CON_PA + 4));
	if ((gpp1 & 0xffu) != 0x22u)
		wr(GPP1CON_PA, (gpp1 & ~0xffu) | 0x22u);       /* function 2 = I2C */
	/* S-Boot leaves tsp_ldo_en (gpg3-1) an undriven input in a normal boot
	 * (touch probe 1: CON 0, DAT bit 1 = 0, no ACK): drive it high and give
	 * the MSS100 its POWER_ON_DELAY (150 ms in the A51 driver). */
	if (!(rd(GPG3CON_PA + 4) & 2u)) {
		wr(GPG3CON_PA, (rd(GPG3CON_PA) & ~0xf0u) | 0x10u);     /* gpg3-1 output */
		wr(GPG3CON_PA + 4, rd(GPG3CON_PA + 4) | 2u);           /* high */
		udelay(200000);
	}
	rkv("touch_gpg3con_after", rd(GPG3CON_PA));
	rkv("touch_gpg3dat_after", rd(GPG3CON_PA + 4));
	IIC_PA = TOUCH_IIC_PA;
	uint32_t con = rd(IIC_PA + IICCON);
	rkv("touch_iiccon_before", con);
	if ((con & 0x4fu) == 0)
		wr(IIC_PA + IICCON, con | IICCON_TXDIV512 | 0xf);
	w[0] = 0x01; w[1] = 0x00;                       /* INFO / PRODUCT_NAME */
	int e1 = iic_xfer(MMS_ADDR, w, 2, name, sizeof(name));
	w[1] = 0x20;                                    /* INFO / VERSION_BOOT.. */
	int e2 = iic_xfer(MMS_ADDR, w, 2, ver, sizeof(ver));
	w[0] = 0x02; w[1] = 0x04;                       /* EVENT / FORMAT, SIZE, TYPE */
	int e3 = iic_xfer(MMS_ADDR, w, 2, fmt, sizeof(fmt));
	w[0] = 0x06; w[1] = 0x00;                       /* CTRL / READY_STATUS */
	int e4 = iic_xfer(MMS_ADDR, w, 2, &ready, 1);
	w[0] = 0x02; w[1] = 0x10;                       /* EVENT / PACKET_INFO */
	int e5 = iic_xfer(MMS_ADDR, w, 2, &info, 1);
	IIC_PA = MUIC_IIC_PA;
	rkv("touch_err", (uint64_t)(int64_t)(e1 | e2 | e3 | e4 | e5));
	rkv("touch_name_lo", le64(&name[0]));
	rkv("touch_name_hi", le64(&name[8]));
	rkv("touch_versions", le64(&ver[0]));
	rkv("touch_event_fmt", (uint64_t)fmt[0] | (uint64_t)fmt[1] << 8 | (uint64_t)fmt[2] << 16 | (uint64_t)fmt[3] << 24);
	rkv("touch_ready", ready);
	rkv("touch_packet_info", info);
	return e1 | e2 | e3 | e4 | e5;
}

/* Connect USB through the MUIC and report every step. 0 on success. */
int a51_muic_usb(void)
{
	uint32_t gpp0 = rd(GPP0CON_PA), con = rd(IIC_PA + IICCON), stat = rd(IIC_PA + IICSTAT);
	rkv("muic_gpp0con_before", gpp0);
	rkv("muic_iiccon_before", con);
	rkv("muic_iicstat_before", stat);
	if ((gpp0 & GPP0_I2C_PINS) != GPP0_I2C_FUNC)
		wr(GPP0CON_PA, (gpp0 & ~GPP0_I2C_PINS) | GPP0_I2C_FUNC);
	/* S-Boot normally leaves a prescaler set; otherwise the slowest one. */
	if ((con & 0x4fu) == 0)
		wr(IIC_PA + IICCON, con | IICCON_TXDIV512 | 0xf);
	rkv("muic_iiccon_used", rd(IIC_PA + IICCON));

	int cntl = muic_read(MUIC_CNTL), mansw = muic_read(MUIC_MANUAL_SW), cfg1 = muic_read(MUIC_CFG1);
	rkv("muic_cntl_before", (uint64_t)(int64_t)cntl);
	rkv("muic_manual_sw_before", (uint64_t)(int64_t)mansw);
	rkv("muic_cfg1_before", (uint64_t)(int64_t)cfg1);
	if (cntl < 0 || mansw < 0 || cfg1 < 0)
		return -1;

	int w1 = muic_write(MUIC_MANUAL_SW, MANUAL_SW_USB);
	int w2 = muic_write(MUIC_CFG1, (uint8_t)(cfg1 | CFG1_MANUAL_SW));
	rkv("muic_write_manual_sw", (uint64_t)(int64_t)w1);
	rkv("muic_write_cfg1", (uint64_t)(int64_t)w2);
	rkv("muic_manual_sw_after", (uint64_t)(int64_t)muic_read(MUIC_MANUAL_SW));
	rkv("muic_cfg1_after", (uint64_t)(int64_t)muic_read(MUIC_CFG1));
	return (w1 || w2) ? -2 : 0;
}
