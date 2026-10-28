#!/usr/bin/env python3
"""Unpack Procursus rootless packages and their dependencies into a directory tree.

  procursus-tree.py PACKAGES DEBDIR TREE PACKAGE [PACKAGE ...]

PACKAGES is a decompressed apt index (dists/1900/main/binary-iphoneos-arm64/
Packages from https://apt.procurs.us). The dependency closure of the named
packages is resolved (first alternative of each "a | b", virtual packages via
Provides), each .deb is downloaded into DEBDIR (kept, SHA-256 checked) and its
data archive unpacked into TREE, which then holds var/jb/... for
sweet-root-ssh.py.

Stops on any dependency the index does not have, and finally checks every
Mach-O in TREE with llvm-otool: each library it links must exist in TREE
(@rpath and /var/jb paths) or be a system library (/usr/lib, /System, which
live in the iOS image's dyld cache). Procursus' dropbear, for one, depends on
libtomcrypt1, which the index does not carry.
"""
import hashlib, os, re, subprocess, sys, tempfile

REPO = 'https://apt.procurs.us/'


def parse(path):
    pkgs, provides = {}, {}
    for blk in open(path).read().split('\n\n'):
        d = {}
        for l in blk.split('\n'):
            if ': ' in l and not l.startswith(' '):
                k, v = l.split(': ', 1)
                d[k] = v
        if 'Package' in d:
            pkgs[d['Package']] = d
            for p in d.get('Provides', '').split(','):
                p = p.strip().split(' ')[0]
                if p:
                    provides.setdefault(p, d['Package'])
    return pkgs, provides


def closure(pkgs, provides, want):
    seen, todo, missing = [], list(want), []
    while todo:
        n = todo.pop()
        n = n if n in pkgs else provides.get(n, n)
        if n in seen:
            continue
        if n not in pkgs:
            missing.append(n)
            continue
        seen.append(n)
        d = pkgs[n]
        for field in ('Pre-Depends', 'Depends'):
            for dep in filter(None, d.get(field, '').split(',')):
                todo.append(re.split(r'[ (]', dep.split('|')[0].strip())[0])
    if missing:
        sys.exit('not in the index: ' + ', '.join(sorted(set(missing))))
    return seen


def fetch(d, debdir):
    path = os.path.join(debdir, os.path.basename(d['Filename']))
    ok = lambda: os.path.exists(path) and hashlib.sha256(open(path, 'rb').read()).hexdigest() == d['SHA256']
    if not ok():
        # curl: the repo answers 403 to urllib's User-Agent
        subprocess.run(['curl', '-sSfL', '--max-time', '300', '--retry', '3', '-o', path, REPO + d['Filename']], check=True)
        if not ok():
            sys.exit(f'{path}: SHA-256 mismatch')
    return path


def unpack(deb, tree):
    with tempfile.TemporaryDirectory() as t:
        subprocess.run(['ar', 'x', os.path.abspath(deb)], cwd=t, check=True)
        data = [x for x in os.listdir(t) if x.startswith('data.tar')]
        subprocess.run(['bsdtar', '-xpf', os.path.join(t, data[0]), '-C', tree], check=True)


def check_links(tree):
    bad = []
    for d, _, files in os.walk(tree):
        for n in files:
            p = os.path.join(d, n)
            if os.path.islink(p) or open(p, 'rb').read(4) not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe'):
                continue
            # load commands, not -L: -L also lists a dylib's own install name
            out = subprocess.run(['llvm-otool', '-l', p], capture_output=True, text=True).stdout
            for lib in re.findall(r'cmd LC_(?:LOAD|REEXPORT|LOAD_WEAK)_DYLIB\n.*\n\s+name (\S+)', out):
                if lib.startswith(('/usr/lib/', '/System/')):
                    continue
                rel = lib.replace('@rpath/', 'var/jb/usr/lib/').lstrip('/')
                if not os.path.exists(os.path.join(tree, rel)):
                    bad.append(f'{os.path.relpath(p, tree)}: {lib}')
    return bad


def main():
    if len(sys.argv) < 5:
        sys.exit(__doc__)
    index, debdir, tree, want = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
    pkgs, provides = parse(index)
    names = closure(pkgs, provides, want)
    os.makedirs(debdir, exist_ok=True)
    os.makedirs(tree, exist_ok=True)
    for n in sorted(names):
        unpack(fetch(pkgs[n], debdir), tree)
    print(f'{len(names)} packages unpacked into {tree}')
    bad = check_links(tree)
    if bad:
        sys.exit('unresolved libraries:\n  ' + '\n  '.join(bad))
    print('every Mach-O links only to libraries in the tree or the system')


if __name__ == '__main__':
    main()
