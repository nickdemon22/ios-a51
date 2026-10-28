#!/usr/bin/env python3
"""Keep a local copy of the A51's iOS root (userdata) in step with the phone, from TWRP.

  python3 -I sync-userdata.py pull IMAGE   # make IMAGE equal to the phone's userdata
  python3 -I sync-userdata.py push IMAGE   # write the chunks of IMAGE that differ

Both compare 64 MiB chunks by SHA-256 (hashed on the phone with one dd per
chunk) and move only the chunks that differ, so after a pull, editing the
image (mount, change files, unmount) and pushing touch a few hundred MiB
instead of the whole 16 GiB. Pushed chunks are read back and verified.
IMAGE starts as a copy of the image last flashed (build/ios16-root.img);
the phone writes its root while iOS runs, so always pull before editing.

Refuses to run unless the phone is in TWRP, the target is sda32 named
"userdata", it is not mounted, and the image fits.
"""
import hashlib, subprocess, sys

DEV, NAME = '/dev/block/sda32', 'userdata'
CHUNK = 64 << 20
BS = 4 << 20


def adb(*args):
    r = subprocess.run(['adb', *args], capture_output=True)
    if r.returncode:
        sys.exit(f'adb {" ".join(args)} failed: {r.stderr.decode().strip()}')
    return r.stdout


def check(size):
    if b'recovery' not in adb('get-state'):
        sys.exit('phone is not in recovery')
    link = adb('shell', f'readlink /dev/block/by-name/{NAME}').decode().strip()
    if link != DEV:
        sys.exit(f'{NAME} is {link}, expected {DEV}')
    if DEV.encode() in adb('shell', 'cat /proc/mounts'):
        sys.exit(f'{DEV} is mounted; unmount /data in TWRP first')
    part = int(adb('shell', f'blockdev --getsize64 {DEV}'))
    if size > part or size % CHUNK:
        sys.exit(f'image {size} does not fit partition {part} in whole chunks')


def phone_hashes(total):
    script = (f'i=0; while [ $i -lt {total} ]; do '
              f'dd if={DEV} bs={BS} skip=$((i * {CHUNK // BS})) count={CHUNK // BS} 2>/dev/null | sha256sum; '
              f'i=$((i + 1)); done')
    out = adb('shell', script).decode().split('\n')
    hashes = [l.split()[0] for l in out if l.strip()]
    if len(hashes) != total:
        sys.exit(f'phone returned {len(hashes)} hashes, expected {total}')
    return hashes


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('pull', 'push'):
        sys.exit(__doc__)
    mode, img = sys.argv[1:]
    with open(img, 'r+b') as f:
        f.seek(0, 2)
        size = f.tell()
        check(size)
        total = size // CHUNK
        print(f'hashing {total} chunks on the phone...', flush=True)
        remote = phone_hashes(total)
        moved = 0
        for i in range(total):
            f.seek(i * CHUNK)
            data = f.read(CHUNK)
            if hashlib.sha256(data).hexdigest() == remote[i]:
                continue
            seek = i * (CHUNK // BS)
            if mode == 'pull':
                data = adb('exec-out', f'dd if={DEV} bs={BS} skip={seek} count={CHUNK // BS} 2>/dev/null')
                if len(data) != CHUNK or hashlib.sha256(data).hexdigest() != remote[i]:
                    sys.exit(f'chunk {i}: pull verify failed')
                f.seek(i * CHUNK)
                f.write(data)
            else:
                tmp = img + '.chunk'
                with open(tmp, 'wb') as t:
                    t.write(data)
                adb('push', tmp, '/tmp/chunk.bin')
                adb('shell', f'dd if=/tmp/chunk.bin of={DEV} bs={BS} seek={seek} conv=notrunc,fsync 2>/dev/null; rm -f /tmp/chunk.bin')
                got = adb('shell', f'dd if={DEV} bs={BS} skip={seek} count={CHUNK // BS} 2>/dev/null | sha256sum').split()[0].decode()
                if got != hashlib.sha256(data).hexdigest():
                    sys.exit(f'chunk {i}: push verify failed')
            moved += 1
            print(f'\rchunk {i + 1}/{total}: {moved} {mode}ed', end='', flush=True)
        f.flush()
    print(f'\ndone: {moved} of {total} chunks {mode}ed and verified')


if __name__ == '__main__':
    main()
