#!/bin/sh
# Build the A51 boot-1 diagnostic shim with an aarch64 bare-metal toolchain.
# Produces out/loader.bin (raw ARM64 Image, header at offset 0).
set -eu
cd "$(dirname "$0")"

: "${CROSS:=aarch64-none-elf-}"
CC="${CROSS}gcc"
OBJCOPY="${CROSS}objcopy"
READELF="${CROSS}readelf"
NM="${CROSS}nm"

CFLAGS="-O2 -ffreestanding -nostdlib -fno-pic -fno-pie -fno-builtin \
 -mgeneral-regs-only -mstrict-align -fno-stack-protector \
 -fno-asynchronous-unwind-tables -march=armv8-a -Wall -Wextra -Werror ${EXTRA_CFLAGS:-}"

mkdir -p out
$CC $CFLAGS -c start.S -o out/start.o
$CC $CFLAGS -c main.c -o out/main.o
$CC $CFLAGS -c ufs_probe.c -o out/ufs_probe.o
$CC $CFLAGS -c payload_boot.c -o out/payload_boot.o
$CC $CFLAGS -c ../fdt.c -o out/fdt.o
$CC $CFLAGS -c deadman.S -o out/deadman.o

OBJS="out/start.o out/main.o out/ufs_probe.o out/payload_boot.o out/fdt.o out/deadman.o"
abs=$($READELF -r $OBJS | grep -c ABS64 || true)
[ "$abs" = 0 ] || { echo "error: $abs absolute relocations" >&2; exit 1; }

$CC $CFLAGS -static -T a51.ld -Wl,--build-id=none -Wl,--no-warn-rwx-segments -Wl,-N \
    $OBJS -o out/loader.elf
$OBJCOPY -O binary out/loader.elf out/loader.bin

size=$($NM out/loader.elf | awk '$3=="_end"{print strtonum("0x"$1)}')
[ -n "$size" ] || size=$(wc -c < out/loader.bin)
truncate -s "$size" out/loader.bin

python3 - <<'PY' 2>/dev/null || true
from pathlib import Path
import struct
p = Path('out/loader.bin').read_bytes()
assert p[56:60] == b'ARM\x64', 'ARM64 Image magic missing'
assert struct.unpack_from('<Q', p, 8)[0] == 0x80000, 'text_offset'
print(f'out/loader.bin: {len(p)} bytes; ARM64 Image header OK')
PY
ls -l out/loader.bin
