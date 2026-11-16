#!/usr/bin/env python3
"""Reference USB stick writer for the QEMU harness (Linux, root).

It makes a netdispatch stick the way USB Creator does, through the same hub
API, but into a raw disk image (or onto a real disk) instead:

  sudo python3 tests/e2e/make-stick.py pair --hub http://10.77.0.1:8000
      prints a code to approve under Boot media -> USB Creator, then saves the
      creator key to ~/.netdispatch-creator-key (or --key-file)

  sudo python3 tests/e2e/make-stick.py create --hub http://10.77.0.1:8000 \\
      --profile Lab --name "Lab stick" --out /var/tmp/nd-stick.img --size 16G [--no-images]

Layout (MBR): partition 1 exFAT NDDATA (images, unattend parts, stick.json),
partition 2 FAT32 NDBOOT (active; boot template + scripts + wimboot files).
Needs: sfdisk, losetup, mkfs.exfat, mount. Python standard library only.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

MIB = 1024 * 1024
SECTOR = 512
DEFAULT_KEY_FILE = Path.home() / ".netdispatch-creator-key"


def api(hub: str, path: str, *, key: dict | None = None, body=None, method: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(hub + path, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Content-Type", "application/json")
    for k, v in (key or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if r.headers.get_content_type() == "application/json" else (raw, r.headers)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method or 'GET'} {path}: HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def cmd_pair(a) -> None:
    start = api(a.hub, "/api/usb/app/pair/start", body={"name": f"make-stick on {os.uname().nodename}"})
    print(f"Approve pairing code {start['code']} in the dashboard (Boot media -> USB Creator).", flush=True)
    while True:
        time.sleep(3)
        r = api(a.hub, "/api/usb/app/pair/poll", body={"poll_token": start["poll_token"]})
        if r["status"] == "approved":
            Path(a.key_file).write_text(r["creator_key"] + "\n")
            os.chmod(a.key_file, 0o600)
            print(f"paired as {r['name']!r}; key saved to {a.key_file}")
            return


def download(url: str, dest: Path, size: int, mtime: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size == size:
        return
    part = dest.with_name(dest.name + ".nd-part")
    with urllib.request.urlopen(url, timeout=120) as r, open(part, "wb") as f:
        shutil.copyfileobj(r, f, 4 * MIB)
    if part.stat().st_size != size:
        sys.exit(f"{url}: got {part.stat().st_size} bytes, expected {size}")
    os.replace(part, dest)
    os.utime(dest, (mtime, mtime))


def parse_size(text: str) -> int:
    units = {"M": MIB, "G": 1024 * MIB}
    return int(float(text[:-1]) * units[text[-1].upper()]) if text[-1].upper() in units else int(text)


def cmd_create(a) -> None:
    ckey = {"X-Creator-Key": Path(a.key_file).read_text().strip()}
    profiles = api(a.hub, "/api/usb/app/profiles", key=ckey)["profiles"]
    profile = next((p for p in profiles if p["name"] == a.profile or str(p["id"]) == a.profile), None)
    if profile is None:
        sys.exit(f"no profile {a.profile!r}; have: {[p['name'] for p in profiles]}")
    made = api(a.hub, "/api/usb/app/sticks", key=ckey, body={"name": a.name, "profile_id": profile["id"]})
    sid, skey = made["stick"]["id"], made["stick_key"]
    key = {"X-Stick-Key": skey, "X-Stick-Id": sid}
    manifest = api(a.hub, f"/api/usb/app/sticks/{sid}/manifest", key=key)
    images = [] if a.no_images else [i for i in manifest["images"] if i.get("files")]
    boot_bytes = sum(i["boot_bytes"] for i in images)
    boot_mb = a.boot_mb or max(2048, (2 * boot_bytes + 512 * MIB) // MIB + 1)
    total = int(run("blockdev", "--getsize64", a.out)) if a.out.startswith("/dev/") else parse_size(a.size)
    print(f"stick {sid}: {len(images)} image(s), boot partition {boot_mb} MiB, disk {total // MIB} MiB")

    # Partitions, MiB-aligned: NDDATA first (Explorer shows the first one), NDBOOT last.
    boot_sectors = boot_mb * MIB // SECTOR
    data_start = MIB // SECTOR
    boot_start = (total // SECTOR - boot_sectors) // 2048 * 2048
    if a.out.startswith("/dev/"):
        dev = a.out
    else:
        Path(a.out).unlink(missing_ok=True)
        with open(a.out, "wb") as f:
            f.truncate(total)
        dev = a.out
    table = (
        "label: dos\n"
        f"start={data_start}, size={boot_start - data_start}, type=7\n"
        f"start={boot_start}, size={boot_sectors}, type=c, bootable\n"
    )
    subprocess.run(["sfdisk", "-q", "--wipe", "always", dev], input=table, text=True, check=True)
    mbr, _ = api(a.hub, "/api/usb/app/mbr", key=key)
    tmpl, headers = api(a.hub, f"/api/usb/app/boot-template?size_mb={boot_mb}&offset={boot_start}", key=key)
    head = gzip.decompress(tmpl)
    assert len(mbr) == 440 and len(head) == int(headers["X-ND-Head-Bytes"])
    with open(dev, "r+b") as f:
        f.write(mbr)  # boot code only: the partition table stays
        f.seek(boot_start * SECTOR)
        f.write(head)

    if dev.startswith("/dev/"):
        run("blockdev", "--rereadpt", dev)
        loop = dev
    else:
        loop = run("losetup", "--find", "--show", "--partscan", dev).strip()
    p = "p" if loop[-1].isdigit() else ""
    mnt = Path(tempfile.mkdtemp(prefix="nd-stick-"))
    try:
        subprocess.run(["mkfs.exfat", "-L", "NDDATA", f"{loop}{p}1"], check=True, capture_output=True)
        (mnt / "data").mkdir()
        (mnt / "boot").mkdir()
        run("mount", f"{loop}{p}1", str(mnt / "data"))
        run("mount", "-t", "vfat", f"{loop}{p}2", str(mnt / "boot"))
        roots = {"data": mnt / "data", "boot": mnt / "boot"}
        have, report = {}, []
        for img in images:
            base = img["cache_url"]
            print(f"  {img['name']}: {len(img['files'])} files from {base or 'the hub'}", flush=True)
            for f in img["files"]:
                url = f"{base}/{f['cache']}" if base else a.hub + f["src"]
                download(url, roots[f["part"]] / f["dest"], f["size"], f["mtime"])
            have[str(img["iso_id"])] = img["etag"]
            total_bytes = img["boot_bytes"] + img["data_bytes"]
            report.append({"iso_id": img["iso_id"], "etag": img["etag"], "status": "ready",
                           "bytes_done": total_bytes, "bytes_total": total_bytes})
        layout = api(a.hub, f"/api/usb/app/sticks/{sid}/layout", key=key, body={"have": have})
        for problem in layout["problems"]:
            print(f"  warning: {problem}")
        for d in layout["replace_dirs"]:
            shutil.rmtree(roots[d["part"]] / d["path"], ignore_errors=True)
        for f in layout["files"]:
            dest = roots[f["part"]] / f["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            # newline="": the .cmd and answer-file parts keep their CRLF.
            with open(dest, "w", encoding="utf-8", newline="") as out:
                out.write(f["content"])
        (roots["data"] / "netdispatch" / "stick.key").write_text(skey + "\n")
        api(a.hub, f"/api/usb/app/sticks/{sid}/report", key=key, body={"items": report})
    finally:
        for m in ("data", "boot"):
            subprocess.run(["umount", str(mnt / m)], capture_output=True)
        if not dev.startswith("/dev/"):
            subprocess.run(["losetup", "-d", loop], capture_output=True)
        shutil.rmtree(mnt, ignore_errors=True)
    print(f"wrote {a.out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pair", "create"):
        s = sub.add_parser(name)
        s.add_argument("--hub", required=True, help="hub boot URL, e.g. http://10.77.0.1:8000")
        s.add_argument("--key-file", default=str(DEFAULT_KEY_FILE))
        if name == "create":
            s.add_argument("--profile", required=True, help="profile name or id")
            s.add_argument("--name", required=True, help="stick name shown in the dashboard")
            s.add_argument("--out", required=True, help="raw image file, or a /dev/ disk")
            s.add_argument("--size", default="16G", help="image size, e.g. 16G (ignored for a disk)")
            s.add_argument("--boot-mb", type=int, default=0, help="boot partition size (default: from the images)")
            s.add_argument("--no-images", action="store_true", help="online-only stick")
    a = ap.parse_args()
    a.hub = a.hub.rstrip("/")
    (cmd_pair if a.cmd == "pair" else cmd_create)(a)


if __name__ == "__main__":
    main()
