#!/usr/bin/env python3
"""Exit 0 if an HFS+ image's journal holds no transactions, else 1.

  hfs-journal-state.py IMAGE

Linux mounts journaled HFS+ only with -o force and never replays the
journal. If iOS left transactions in it (reset without unmount), XNU would
replay them over anything edited on Linux, so editing is refused. Volume
header at 1024: attributes (bit 13 journaled), journalInfoBlock; the journal
info block gives the journal offset; its header has start == end when empty.
"""
import struct, sys

f = open(sys.argv[1], 'rb')
f.seek(1024)
vh = f.read(512)
if vh[:2] not in (b'H+', b'HX'):
    sys.exit('not an HFS+ volume')
attr = struct.unpack_from('>I', vh, 4)[0]
if not attr & (1 << 13):
    print('not journaled')
    sys.exit(0)
jib = struct.unpack_from('>I', vh, 12)[0]
bsize = struct.unpack_from('>I', vh, 40)[0]
f.seek(jib * bsize)
info = f.read(52)
flags, = struct.unpack_from('>I', info, 0)
offset, size = struct.unpack_from('>QQ', info, 36)
if flags & 2:
    sys.exit('journal is on another device')
if flags & 4:
    # kJIJournalNeedInitMask: never used yet (image fresh from mkiosroot.sh)
    print('journal not initialised yet: empty')
    sys.exit(0)
f.seek(offset)
jh = f.read(40)
magic, endian, start, end, jsize = struct.unpack_from('>IIQQQ', jh, 0)
if magic != 0x4a4e4c78:
    # the header may be little-endian (written by an Intel Mac or XNU arm64)
    magic, endian, start, end, jsize = struct.unpack_from('<IIQQQ', jh, 0)
if magic != 0x4a4e4c78:
    sys.exit(f'bad journal magic {magic:#x}')
print(f'journal at {offset:#x} size {jsize}: start {start:#x} end {end:#x}')
if start != end:
    sys.exit('journal has transactions: boot iOS and leave it idle, then reset again')
print('journal empty')
