# A51 host scripts

Run from a workspace whose layout is:

```
workspace/
  ios-a51/            this repository
  firmware/img/       stock boot.img (from the official A515FXXU8HWI1 firmware)
  ipsw/               os.dmg, systemos.dmg, appos.dmg extracted from the
                      iPhone X iOS 16.7.x IPSW
  build/              kernels (CI artifacts), ADT, loader, images
  logs/               boot logs
```

Copy (or symlink) these scripts into `workspace/`; they `cd` to their own
directory. The phone runs TWRP; every script that writes checks it is in
recovery and verifies what it wrote by SHA-256 readback.

| Script | What it does |
|---|---|
| `build-ios-root.sh` | 16 GiB HFS+ iOS root: `tools/mkiosroot.sh` + `tools/sweet-root-tweaks.py` |
| `flash-userdata.py` | writes the root to userdata (sda32) in 64 MiB chunks, resumable |
| `run-cpus.sh` | boot image (loader + payload), writes boot (sda12), reboots, pulls logs |
| `sync-userdata.py` | `pull`/`push` only the 64 MiB chunks that differ from the phone |
| `install-ssh.sh` | pull, replay iOS's HFS+ journal, add OpenSSH (`tools/sweet-root-ssh.py`), push |

SSH needs `build/procursus/tree-ssh` from `tools/procursus-tree.py` (Procursus
dists/1900: openssh-server, openssh-sftp-server, libzstd1, bash, coreutils,
...), a key pair `build/ssh/a51_ed25519` and host keys in `build/ssh/hostkeys`.
Then, with the listener `sudo sh tools/sweet-usbnet.sh logs/live.txt` running:
`ssh -i build/ssh/a51_ed25519 root@192.168.7.2`.
