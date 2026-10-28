#!/usr/bin/env python3
"""Write the iOS root image to the A51's userdata partition from TWRP, verified.

  python3 -I flash-userdata.py build/ios16-root.img [--state flash.state]

64 MiB chunks: each chunk is pushed to TWRP's /tmp, written with dd at its
offset, then read back on the phone and compared by SHA-256. All-zero chunks
are written too: a file may hold zeros that must not read back as old Android
data. Finished chunks are recorded in the
state file, so an interrupted run resumes.

Refuses to run unless the phone is in TWRP, the target is sda32 named
"userdata", it is not mounted, and the image fits.
"""
import hashlib, os, subprocess, sys

DEV, NAME = '/dev/block/sda32', 'userdata'
CHUNK = 64 << 20
BS = 4 << 20


def adb(*args, data=None):
    r = subprocess.run(['adb', *args], input=data, capture_output=True)
    if r.returncode:
        sys.exit(f'adb {" ".join(args)} failed: {r.stderr.decode().strip()}')
    return r.stdout.decode().strip()


def main():
    img = sys.argv[1]
    state = sys.argv[sys.argv.index('--state') + 1] if '--state' in sys.argv else img + '.flash-state'

    if 'recovery' not in adb('get-state'):
        sys.exit('phone is not in recovery')
    link = adb('shell', f'readlink /dev/block/by-name/{NAME}')
    if link != DEV:
        sys.exit(f'{NAME} is {link}, expected {DEV}')
    if DEV in adb('shell', 'cat /proc/mounts'):
        sys.exit(f'{DEV} is mounted; unmount /data in TWRP first')
    part = int(adb('shell', f'blockdev --getsize64 {DEV}'))
    size = os.path.getsize(img)
    if size > part or size % CHUNK:
        sys.exit(f'image {size} does not fit partition {part} in whole chunks')

    done = set()
    if os.path.exists(state):
        done = {int(x) for x in open(state).read().split()}
    total = size // CHUNK
    written = 0
    tmp = state + '.chunk'
    with open(img, 'rb') as f, open(state, 'a') as log:
        for i in range(total):
            if i in done:
                continue
            f.seek(i * CHUNK)
            data = f.read(CHUNK)
            want = hashlib.sha256(data).hexdigest()
            seek = i * (CHUNK // BS)
            # TWRP's adbd passes no stdin to exec-in, so stage the chunk in /tmp.
            with open(tmp, 'wb') as t:
                t.write(data)
            adb('push', tmp, '/tmp/chunk.bin')
            adb('shell', f'dd if=/tmp/chunk.bin of={DEV} bs={BS} seek={seek} conv=notrunc,fsync 2>/dev/null; rm -f /tmp/chunk.bin')
            got = adb('shell', f'dd if={DEV} bs={BS} skip={seek} count={CHUNK // BS} 2>/dev/null | sha256sum').split()[0]
            if got != want:
                sys.exit(f'chunk {i} verify failed: {got} != {want}')
            written += 1
            log.write(f'{i}\n')
            log.flush()
            print(f'\rchunk {i + 1}/{total}: {written} written and verified', end='', flush=True)
    print(f'\ndone: {written} chunks written and verified this run, {total} total')


if __name__ == '__main__':
    main()
