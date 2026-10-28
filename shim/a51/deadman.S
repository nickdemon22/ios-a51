/* SPDX-License-Identifier: GPL-2.0-only
 * Dead-man timer for kernel boots.
 *
 * XNU runs with cpus=1, so CPU1 (MPIDR 0x1, the second Cortex-A53) is idle.
 * Just before the handoff the shim powers it on through PSCI CPU_ON at
 * deadman_entry. It spins with interrupts masked and the MMU off until the
 * system counter passes deadman_deadline_cell, then performs the same recovery
 * request as recovery_reset() in main.c: INFORM2/3 + SPARE2/3 magic, then PMU
 * SWRESET. It also leaves a proof-of-life record in deadman_alive.
 *
 * Two Samsung EL3 quirks, both measured on SM-A515F (boots 11g-11k):
 *  - CPU_ON returns 0 but the core never runs while the GIC-400 distributor is
 *    off (GICD_CTLR = 0, as S-Boot leaves it); start_deadman() enables it.
 *  - the PSCI context id does not arrive in x0, so the deadline is read from
 *    memory instead.
 *
 * A warm SWRESET keeps DRAM, so the XNU console in the pmsg ramoops zone
 * survives into recovery, and the operator never has to cut power (a manual
 * Power+VolDown reset is MPON, which loses the log).
 */
.section .text, "ax"
.global deadman_entry
deadman_entry:
    msr daifset, #15
    /* The deadline (CNTPCT ticks) comes from memory: Samsung's PSCI
     * CPU_ON does not deliver the context id in x0 (measured: a woken CPU1
     * never saw its deadline pass). x0 is only recorded. */
    adr x9, deadman_alive
    str x0, [x9, #56]
    ldr x19, deadman_deadline_cell
    /* Coherency controls of this core, read through deadman_probe's trap
     * catcher (EL3 may not grant EL1 access): CPUECTLR, CPUACTLR + ESRs. */
    add x0, x9, #64
    bl deadman_probe
    bl cpu_mhz
    str x0, [x9, #96]            /* alive[12]: this core's MHz */
    /* Proof of life for the boot CPU (MMU off on both sides, so plain
     * stores are visible): magic, CurrentEL, CNTPCT, SCTLR, MPIDR. The
     * magic is stored last, so it also means every field above is valid. */
    mrs x10, CurrentEL
    mrs x11, cntpct_el0
    mrs x12, sctlr_el1
    mrs x13, mpidr_el1
    stp x10, x11, [x9, #8]
    stp x12, x13, [x9, #24]
    movz x10, #0x4c41            /* "CPU1ALIV" little endian */
    movk x10, #0x4549, lsl #16
    movk x10, #0x4c41, lsl #32
    movk x10, #0x5649, lsl #48
    str x10, [x9]
    dsb sy
    mov x14, #1                  /* stage 1: waiting for the deadline */
    str x14, [x9, #48]
1:  isb
    mrs x1, cntpct_el0
    str x1, [x9, #40]            /* heartbeat: last CNTPCT seen */
    cmp x1, x19
    b.lo 1b
    mov x14, #2                  /* stage 2: writing the reset request */
    str x14, [x9, #48]
    dsb sy

    movz x2, #0x1186, lsl #16    /* PMU 0x11860000 */
    movz w3, #0x5678
    movk w3, #0x1234, lsl #16    /* 0x12345678 */
    movz w4, #0x5674
    movk w4, #0x1234, lsl #16    /* 0x12345674 */
    str w3, [x2, #0x808]
    str w4, [x2, #0x80c]
    str w3, [x2, #0x908]
    str w4, [x2, #0x90c]
    dsb sy
    mov w5, #1
    str w5, [x2, #0x400]         /* EXYNOS_SWRESET */
    dsb sy
    mov x14, #3                  /* stage 3: SWRESET written, still running */
    str x14, [x9, #48]
    dsb sy
2:  wfe
    b 2b

/* uint64_t cpu_mhz(void): this core's clock, from PMCCNTR over 10 ms of the
 * 26 MHz system counter. Clobbers x1-x5. */
.global cpu_mhz
cpu_mhz:
    mov x1, #1                   /* PMCR_EL0.E */
    orr x1, x1, #4               /* .C: reset the cycle counter */
    msr pmcr_el0, x1
    mov x1, #(1 << 31)
    msr pmcntenset_el0, x1       /* enable PMCCNTR */
    isb
    mrs x2, cntpct_el0
    mrs x3, pmccntr_el0
    movz x4, #0xf7a0             /* 260000 ticks = 10 ms */
    movk x4, #0x3, lsl #16
1:  isb
    mrs x5, cntpct_el0
    sub x5, x5, x2
    cmp x5, x4
    b.lo 1b
    mrs x1, pmccntr_el0
    sub x0, x1, x3
    mov x1, #10000
    udiv x0, x0, x1              /* cycles per 10 ms / 10000 = MHz */
    ret

/* void deadman_probe(uint64_t out[4]): out = { CPUECTLR_EL1, ESR if that
 * read trapped (else 0), CPUACTLR_EL1, ESR }. A trapping read goes to
 * deadman_vectors, which records ESR_EL1 and skips the instruction; the
 * caller's VBAR_EL1 is restored. Clobbers x1-x5, x16, x17. */
.global deadman_probe
deadman_probe:
    mrs x1, vbar_el1
    adr x2, deadman_vectors
    msr vbar_el1, x2
    isb
    adr x3, deadman_trap_esr
    str xzr, [x3]
    mov x4, xzr
    mrs x4, S3_1_C15_C2_1        /* CPUECTLR_EL1 (A53/A73: bit 6 SMPEN) */
    ldr x5, [x3]
    stp x4, x5, [x0]
    str xzr, [x3]
    mov x4, xzr
    mrs x4, S3_1_C15_C2_0        /* CPUACTLR_EL1 */
    ldr x5, [x3]
    stp x4, x5, [x0, #16]
    msr vbar_el1, x1
    isb
    ret

.balign 2048
deadman_vectors:
.rept 16
    b deadman_trap
.balign 128
.endr
deadman_trap:
    mrs x17, esr_el1
    adr x16, deadman_trap_esr
    str x17, [x16]
    mrs x17, elr_el1
    add x17, x17, #4
    msr elr_el1, x17
    eret

.section .data, "aw"
.balign 8
deadman_trap_esr:
    .quad 0
.global deadman_alive
deadman_alive:
    /* 0 magic, 1 CurrentEL, 2 CNTPCT, 3 SCTLR, 4 MPIDR, 5 heartbeat,
     * 6 stage, 7 x0 at entry, 8-11 deadman_probe output */
    .quad 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
.global deadman_deadline_cell
deadman_deadline_cell:
    .quad 0
