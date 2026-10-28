#!/usr/bin/env python3
"""Replay an HFS+ journal written by iOS into an image, then mark it empty.

  hfs-journal-replay.py IMAGE

What XNU's replay_journal() (bsd/vfs/vfs_journal.c) does on mount, for an
image Linux is about to edit: Linux mounts journaled HFS+ only with -o force
and never replays, and hfsprogs 540 fsck reports a little-endian (arm64)
journal as empty. Without this, the phone's next mount would replay old
transactions over the edits.

Journal (TN1150): info block -> journal offset; journal header (magic
JNLx, endian 0x12345678 in the writer's byte order, start, end, size,
blhdr_size, checksum over 44 bytes, jhdr_size); from start to end, block
list headers (max_blocks, num_blocks, bytes_used, checksum over 32 bytes,
pad, block_info[num_blocks] = {bnum in jhdr_size units, bsize, next}; entry
0 is the header itself) followed by the blocks. The journal is circular
over [jhdr_size, size).
"""
import struct, sys


def cksum(b):
    c = 0
    for x in b:
        c = ((c << 8) ^ (c + x)) & 0xffffffff
    return ~c & 0xffffffff


def main():
    f = open(sys.argv[1], 'r+b')
    f.seek(1024)
    vh = f.read(512)
    if vh[:2] not in (b'H+', b'HX') or not struct.unpack_from('>I', vh, 4)[0] & (1 << 13):
        sys.exit('not a journaled HFS+ volume')
    jib, bsize = struct.unpack_from('>I', vh, 12)[0], struct.unpack_from('>I', vh, 40)[0]
    f.seek(jib * bsize)
    info = f.read(52)
    flags, = struct.unpack_from('>I', info, 0)
    if flags & 4:
        print('journal not initialised: nothing to replay')
        return
    jbase, = struct.unpack_from('>Q', info, 36)
    f.seek(jbase)
    hdr = bytearray(f.read(48))
    for e in '<>':
        magic, endian, start, end, jsize, blhdr_size, ck, jhdr_size = struct.unpack_from(e + 'IIQQQIII', hdr, 0)
        if magic == 0x4a4e4c78 and endian == 0x12345678:
            break
    else:
        sys.exit('bad journal header')
    chk = bytearray(hdr[:44])
    struct.pack_into(e + 'I', chk, 36, 0)
    if cksum(chk) != ck:
        sys.exit('journal header checksum mismatch')
    print(f'journal ({"little" if e == "<" else "big"}-endian) size {jsize:#x} start {start:#x} end {end:#x} blhdr {blhdr_size:#x} jhdr {jhdr_size}')
    if start == end:
        print('journal empty')
        return

    def jread(off, n):
        """n bytes at journal offset off, wrapping over [jhdr_size, jsize)."""
        out = b''
        while n:
            if off >= jsize:
                off = jhdr_size + (off - jsize)
            k = min(n, jsize - off)
            f.seek(jbase + off)
            out += f.read(k)
            off += k
            n -= k
        return out, off

    off, lists, blocks, nbytes = start, 0, 0, 0
    while off != end:
        bl, data_off = jread(off, blhdr_size)
        maxb, num, used, bck = struct.unpack_from(e + 'HHII', bl, 0)
        c = bytearray(bl[:32])
        struct.pack_into(e + 'I', c, 8, 0)
        if cksum(c) != bck or num > maxb or used < blhdr_size or used > jsize:
            sys.exit(f'bad block list header at {off:#x}: stopping before writing it')
        for i in range(1, num):
            bnum, bsz, _ = struct.unpack_from(e + 'QII', bl, 16 + 16 * i)
            data, data_off = jread(data_off, bsz)
            if bnum == 0xffffffffffffffff:
                continue
            f.seek(bnum * jhdr_size)
            f.write(data)
            blocks += 1
            nbytes += bsz
        lists += 1
        off += used
        if off >= jsize:
            off = jhdr_size + (off - jsize)
    struct.pack_into(e + 'Q', hdr, 8, end)
    struct.pack_into(e + 'I', hdr, 36, 0)
    struct.pack_into(e + 'I', hdr, 36, cksum(hdr[:44]))
    f.seek(jbase)
    f.write(hdr)
    f.flush()
    print(f'replayed {lists} block lists, {blocks} blocks ({nbytes} bytes); journal now empty')


if __name__ == '__main__':
    main()
