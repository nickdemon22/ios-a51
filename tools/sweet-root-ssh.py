#!/usr/bin/env python3
"""Add an SSH server (OpenSSH) and a shell to an iOS root made by tools/mkiosroot.sh.

  sudo sweet-root-ssh.py [--diag] MOUNTED_ROOT JB_TREE AUTHORIZED_KEY.pub HOSTKEY_DIR

JB_TREE holds var/jb/... made by tools/procursus-tree.py from Procursus iOS 16
(dists/1900, iphoneos-arm64) packages: openssh-server, bash, coreutils and
their dependencies. Their binaries are ad-hoc signed and load their libraries
from /var/jb, which the SWEET AMFI policy accepts like any signed root binary.
HOSTKEY_DIR holds ssh_host_{ed25519,ecdsa,rsa}_key(.pub), made on the PC with
ssh-keygen, so the host key does not change between reinstalls.

  /private/var/jb          the tree, root-owned; host keys in etc/ssh
  launchd.plist            job com.sweet.sshd: sweet-sshd builds the user
                           database, then sshd -D on port 22, no PAM, no
                           StrictModes (the real path of ~root/.ssh runs
                           through /private, made on Linux; a key login was
                           refused with them), log in /var/jb/var/log/sshd.log
                           (/dev/console does not reach the UDP log); the
                           earlier com.sweet.dropbear job is removed
                           (Procursus' dropbear needs libtomcrypt1, which
                           the 1900 index lacks)
                           with --diag, job com.sweet.sshdiag: once the USB
                           link is up, sends SSHDIAG lines to the PC's live
                           log (udp/5140 on 192.168.7.1, bash's /dev/udp):
                           accounts, user database, sshd -T, sshd.log, then
                           runs sshd -d on port 2222 (says why a login is
                           refused). Keys as qemu-t8030's bash job
                           (POSIXSpawnType Interactive, no pressured exit);
                           a job without POSIXSpawnType never started.
                           Without --diag the job is removed.
  /var/jb/etc              master.passwd, passwd, group, shells: Procursus
                           binaries resolve users with libiosexec
                           (ie_getpwnam), which reads only
                           /var/jb/etc/{spwd.db,pwd.db,group}, never /etc;
                           without them root is an invalid user and every
                           login is refused. sweet-sshd makes the .db files
                           with pwd_mkdb at each start
  passwd                   "sshd" (uid 75, as iOS's _sshd): sshd's privilege
                           separation user
  root                     shell /var/jb/bin/bash, listed in /etc/shells;
                           AUTHORIZED_KEY in ~root/.ssh
  /bin/sh, /bin/bash       links to /var/jb/bin/bash: iOS has no /bin/sh,
                           which is root's stock shell (sshd refuses users
                           whose shell does not exist) and what system(3)
                           runs

Run as root. Idempotent: /private/var/jb is replaced on every run. The phone
is reached over the USB Ethernet gadget (sweetusb=2) at 192.168.7.2.
"""
import os, plistlib, shutil, sys

LAUNCHD = 'System/Library/xpc/launchd.plist'
JOB = '/System/Library/LaunchDaemons/com.sweet.sshd.plist'
OLD_JOB = '/System/Library/LaunchDaemons/com.sweet.dropbear.plist'
LOG_JOB = '/System/Library/LaunchDaemons/com.sweet.sshlog.plist'
DIAG_JOB = '/System/Library/LaunchDaemons/com.sweet.sshdiag.plist'
WRAPPER = '/var/jb/usr/libexec/sweet-sshd'
WRAPPER_SCRIPT = r"""#!/var/jb/usr/bin/bash
# com.sweet.sshd: make Procursus' user database, then run sshd (tools/sweet-root-ssh.py)
# Procursus binaries (sshd, id, bash, ...) never call Apple's getpwnam: Procursus
# prepends #include <libiosexec.h> to the SDK's pwd.h/grp.h, so getpwnam is
# ie_getpwnam (libiosexec 1.3.1, LIBIOSEXEC_PREFIXED_ROOT=1), which reads only
# the Berkeley DB files /var/jb/etc/spwd.db (euid 0) / pwd.db, and getgrnam
# reads /var/jb/etc/group. pwd_mkdb (system-cmds, same libc dbopen) makes them
# from /var/jb/etc/master.passwd; it needs group "wheel" in /var/jb/etc/group
# and renames its input file to master.passwd, so it gets a copy.
export PATH=/var/jb/usr/bin:/var/jb/bin:/var/jb/usr/sbin:/var/jb/sbin:/usr/bin:/bin:/usr/sbin:/sbin
etc=/var/jb/etc
{
	rm -f $etc/pwd.db.tmp $etc/spwd.db.tmp $etc/master.passwd.new $etc/master.passwd.new.orig
	cp $etc/master.passwd $etc/master.passwd.new &&
		/var/jb/usr/sbin/pwd_mkdb -p -d $etc $etc/master.passwd.new
	echo "pwd_mkdb exit $?"
	ls -ln $etc
	id; id root; id sshd
} >/var/jb/var/log/pwd_mkdb.log 2>&1
exec /var/jb/usr/sbin/sshd -D -o UsePAM=no -o StrictModes=no -o LogLevel=VERBOSE -E /var/jb/var/log/sshd.log
"""
DIAG = '/var/jb/usr/libexec/sweet-sshdiag'
DIAG_SCRIPT = r'''#!/var/jb/usr/bin/bash
# com.sweet.sshdiag: SSH diagnostics to the PC's live log (tools/sweet-root-ssh.py)
export PATH=/var/jb/usr/bin:/var/jb/bin:/var/jb/usr/sbin:/var/jb/sbin:/usr/bin:/bin:/usr/sbin:/sbin
udp=/dev/udp/192.168.7.1/5140
for i in $(seq 120); do echo "SSHDIAG start" 2>/dev/null >$udp && break; sleep 2; done
exec >$udp 2>&1
tag() { while IFS= read -r l; do echo "SSHDIAG $1 $l"; done; }
{ id; id root; who am i; ls -lnd / /bin /bin/sh /private /private/var /private/var/root \
	/private/var/root/.ssh /private/var/root/.ssh/authorized_keys /var/jb/bin/bash \
	/var/jb/var/empty /var/jb/var/log; ls -ln /var/jb/var/log; cat /etc/shells; } 2>&1 | tag sys
grep -E '^(root|sshd|_sshd):' /etc/master.passwd /etc/passwd 2>&1 | tag pw
# Procursus getpwnam = libiosexec: /var/jb/etc/{spwd.db,pwd.db,group}, made by pwd_mkdb in sweet-sshd
cat /var/jb/var/log/pwd_mkdb.log 2>&1 | tag mkdb
ls -ln /var/jb/etc 2>&1 | tag db
grep -E '^(root|sshd|_sshd):' /var/jb/etc/master.passwd /var/jb/etc/passwd 2>&1 | tag jbpw
grep -E '^wheel:' /var/jb/etc/group 2>&1 | tag jbpw
cat /var/jb/etc/does-not-exist 2>&1 | tag errno	# ENOENT expected; "Invalid argument" = kernel bug
for u in root 0 sshd 75 mobile; do echo "id $u: $(id $u 2>&1)"; done 2>&1 | tag id
echo "~root=$(bash -c 'echo ~root')" 2>&1 | tag id
# redo pwd_mkdb here so its own output reaches the PC even if the sshd wrapper failed
rm -f /var/jb/etc/pwd.db.tmp /var/jb/etc/spwd.db.tmp /var/jb/etc/master.passwd.new /var/jb/etc/master.passwd.new.orig
cp /var/jb/etc/master.passwd /var/jb/etc/master.passwd.new && pwd_mkdb -p -d /var/jb/etc /var/jb/etc/master.passwd.new 2>&1 | tag mkdb2
echo "pwd_mkdb exit ${PIPESTATUS[0]}; $(id root 2>&1)" | tag mkdb2
ls -ln /var/jb/etc/pwd.db /var/jb/etc/spwd.db 2>&1 | tag mkdb2
ls -ld /etc 2>&1 | tag id
sshd -T -o UsePAM=no -o StrictModes=no 2>&1 | grep -iE 'permitroot|pubkey|password|authorizedkeys|strict|usepam|usedns|allowusers|denyusers|privilegesep' | tag cfg
cat /var/jb/var/log/sshd.log /var/jb/var/log/sshd.out 2>&1 | tail -n 30 | tag log
while true; do
	# re-exec needs the absolute path
	/var/jb/usr/sbin/sshd -d -D -p 2222 -o UsePAM=no -o StrictModes=no -o LogLevel=DEBUG2 2>&1 | tag dbg
	sleep 2
done
'''
SHELL = '/var/jb/bin/bash'
PRIVSEP = {'master.passwd': 'sshd:*:75:75::0:0:sshd Privilege separation:/var/jb/var/empty:/usr/bin/false',
           'passwd': 'sshd:*:75:75:sshd Privilege separation:/var/jb/var/empty:/usr/bin/false'}


def install_tree(root, tree, hostkeys, diag):
    src = os.path.join(tree, 'var/jb')
    dst = os.path.join(root, 'private/var/jb')
    # all of it comes from JB_TREE: replace it (copytree cannot overwrite symlinks)
    if os.path.lexists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)
    ssh = os.path.join(dst, 'etc/ssh')
    for n in os.listdir(hostkeys):
        if n.startswith('ssh_host_'):
            shutil.copy(os.path.join(hostkeys, n), os.path.join(ssh, n))
    for d, dirs, files in os.walk(dst):
        for n in [d] + [os.path.join(d, x) for x in dirs + files]:
            os.lchown(n, 0, 0)
    for n in os.listdir(ssh):
        if n.startswith('ssh_host_') and n.endswith('_key'):
            os.chmod(os.path.join(ssh, n), 0o600)
    os.chmod(os.path.join(dst, 'var/empty'), 0o755)
    os.makedirs(os.path.join(dst, 'var/log'), exist_ok=True)
    for path, text in ((DIAG, DIAG_SCRIPT),) * diag + ((WRAPPER, WRAPPER_SCRIPT),):
        path = os.path.join(root, path.lstrip('/').replace('var/jb', 'private/var/jb', 1))
        with open(path, 'w') as f:
            f.write(text)
        os.chown(path, 0, 0)
        os.chmod(path, 0o755)
    print('installed', dst)


def launchd(root, diag):
    path = os.path.join(root, LAUNCHD)
    st = os.stat(path)
    with open(path, 'rb') as f:
        plist = plistlib.load(f)
    jobs = plist['LaunchDaemons']
    jobs.pop(OLD_JOB, None)
    jobs[JOB] = {
        'Label': 'com.sweet.sshd',
        # pwd_mkdb for libiosexec's user database, then sshd (see WRAPPER_SCRIPT)
        'ProgramArguments': ['/var/jb/usr/bin/bash', WRAPPER],
        'EnvironmentVariables': {'PATH': '/var/jb/usr/bin:/var/jb/bin:/var/jb/usr/sbin:/var/jb/sbin:/usr/bin:/bin:/usr/sbin:/sbin'},
        'StandardOutPath': '/var/jb/var/log/sshd.out',
        'StandardErrorPath': '/var/jb/var/log/sshd.out',
        'UserName': 'root',
        'RunAtLoad': True,
        'KeepAlive': True,
        'POSIXSpawnType': 'Interactive',
    }
    jobs.pop(LOG_JOB, None)
    jobs.pop(DIAG_JOB, None)
    if diag:
        jobs[DIAG_JOB] = {
            'Label': 'com.sweet.sshdiag',
            'ProgramArguments': ['/var/jb/usr/bin/bash', DIAG],
            'UserName': 'root',
            'RunAtLoad': True,
            'KeepAlive': True,
            'POSIXSpawnType': 'Interactive',
            'EnablePressuredExit': False,
        }
    with open(path, 'wb') as f:
        plistlib.dump(plist, f, fmt=plistlib.FMT_BINARY)
    os.chown(path, st.st_uid, st.st_gid)
    os.chmod(path, st.st_mode & 0o7777)
    print('launchd.plist: job com.sweet.sshd' + ', com.sweet.sshdiag' * diag)


def bin_sh(root):
    os.makedirs(os.path.join(root, 'bin'), exist_ok=True)
    for n in ('sh', 'bash'):
        path = os.path.join(root, 'bin', n)
        if not os.path.lexists(path):
            os.symlink(SHELL, path)
            os.lchown(path, 0, 0)
    print('/bin/sh, /bin/bash ->', SHELL)


def accounts(root):
    for name in ('master.passwd', 'passwd'):
        path = os.path.join(root, 'private/etc', name)
        lines = open(path).read().split('\n')
        for i, l in enumerate(lines):
            f = l.split(':')
            if f[0] == 'root' and len(f) > 1:
                f[-1] = SHELL
                lines[i] = ':'.join(f)
        lines = [l for l in lines if not l.startswith('sshd:')]
        while lines and not lines[-1]:
            lines.pop()
        open(path, 'w').write('\n'.join(lines + [PRIVSEP[name]]) + '\n')
    shells = os.path.join(root, 'private/etc/shells')
    have = open(shells).read().split() if os.path.exists(shells) else []
    if SHELL not in have:
        with open(shells, 'a') as f:
            f.write(SHELL + '\n')
    # Procursus' libiosexec resolves users from /var/jb/etc, never from /etc:
    # same files there; sweet-sshd turns master.passwd into pwd.db/spwd.db
    jbetc = os.path.join(root, 'private/var/jb/etc')
    os.makedirs(jbetc, exist_ok=True)
    for name, mode in (('master.passwd', 0o600), ('passwd', 0o644), ('group', 0o644), ('shells', 0o644)):
        dst = os.path.join(jbetc, name)
        shutil.copyfile(os.path.join(root, 'private/etc', name), dst)
        os.chown(dst, 0, 0)
        os.chmod(dst, mode)
    print('root shell', SHELL, '+ privsep user sshd; copies in /var/jb/etc for libiosexec')


def authorized_key(root, key):
    d = os.path.join(root, 'private/var/root/.ssh')
    os.makedirs(d, exist_ok=True)
    os.chown(d, 0, 0)
    os.chmod(d, 0o700)
    path = os.path.join(d, 'authorized_keys')
    keys = open(path).read().split('\n') if os.path.exists(path) else []
    line = open(key).read().strip()
    if line not in keys:
        keys = [k for k in keys if k] + [line]
    open(path, 'w').write('\n'.join(keys) + '\n')
    os.chown(path, 0, 0)
    os.chmod(path, 0o600)
    print('authorized key', line.split()[-1])


def main():
    args = sys.argv[1:]
    diag = '--diag' in args
    if diag:
        args.remove('--diag')
    if len(args) != 4 or not os.path.isdir(os.path.join(args[0], 'System/Library/xpc')):
        sys.exit(__doc__)
    root, tree, key, hostkeys = args
    install_tree(root, tree, hostkeys, diag)
    launchd(root, diag)
    accounts(root)
    bin_sh(root)
    authorized_key(root, key)


if __name__ == '__main__':
    main()
