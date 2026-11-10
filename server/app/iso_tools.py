"""ISO handling: filename hygiene, 7-Zip extraction, layout detection and
case-insensitive, traversal-safe path resolution."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import (
    BOOT_LINUX,
    BOOT_SANBOOT,
    BOOT_WINDOWS_SETUP,
    BOOT_WINPE,
    ISO_ERROR,
    ISO_EXTRACTING,
    ISO_MISSING,
    ISO_READY,
    ISO_UPLOADING,
    IsoImage,
)
from .pxe_engine import RAW_ISO_INITRD

log = logging.getLogger("netdispatch.iso")

ALLOWED_EXTENSIONS = (".iso", ".img")
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class ExtractionError(RuntimeError):
    pass


def sanitize_filename(name: str) -> str:
    base = PurePosixPath((name or "").replace("\\", "/")).name
    base = _UNSAFE_NAME.sub("_", base).strip("._")
    if not base or not base.lower().endswith(ALLOWED_EXTENSIONS):
        raise ValueError("filename must end in .iso or .img")
    if len(base) > 200:
        stem, ext = os.path.splitext(base)
        base = stem[: 200 - len(ext)] + ext
    return base


def seven_zip() -> str | None:
    return shutil.which("7zz") or shutil.which("7z")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def list_files(root: Path, limit: int | None = None) -> list[str]:
    """Relative POSIX paths of all files below root, sorted."""
    out: list[str] = []
    if not root.is_dir():
        return out
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            out.append(Path(dirpath, f).relative_to(root).as_posix())
            if limit and len(out) >= limit:
                return sorted(out)
    return sorted(out)


def resolve_ci(root: Path, rel: str) -> Path | None:
    """Resolve ``rel`` below ``root`` case-insensitively.

    Returns None for anything that is not an existing regular file inside
    ``root`` (rejects ``..``, absolute paths and symlink escapes).
    """
    parts = [p for p in PurePosixPath(rel.replace("\\", "/")).parts if p not in ("", "/")]
    if not parts or any(p in ("..", ".") for p in parts):
        return None
    cur = root
    for part in parts:
        candidate = cur / part
        if candidate.exists():
            cur = candidate
            continue
        if not cur.is_dir():
            return None
        lower = part.lower()
        match = next((c for c in cur.iterdir() if c.name.lower() == lower), None)
        if match is None:
            return None
        cur = match
    try:
        cur.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return cur if cur.is_file() else None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass
class Detection:
    boot_method: str
    detected_os: str
    kernel_path: str | None = None
    initrd_paths: list[str] = field(default_factory=list)
    cmdline: str | None = None
    bcd_path: str | None = None
    boot_sdi_path: str | None = None
    boot_wim_path: str | None = None
    setup_path: str | None = None


def detect_layout(root: Path) -> Detection:
    files = list_files(root)
    index = {f.lower(): f for f in files}

    def has(path: str) -> str | None:
        return index.get(path.lower())

    def first(*paths: str) -> str | None:
        for p in paths:
            hit = has(p)
            if hit:
                return hit
        return None

    def match(pattern: str) -> list[str]:
        rx = re.compile(pattern, re.IGNORECASE)
        return sorted(f for f in files if rx.match(f))

    # --- Windows / WinPE ---------------------------------------------------
    boot_wim = has("sources/boot.wim")
    if boot_wim:
        bcd = first("boot/bcd", "efi/microsoft/boot/bcd")
        sdi = first("boot/boot.sdi", "efi/microsoft/boot/boot.sdi")
        install = first("sources/install.wim", "sources/install.esd", "sources/install.swm")
        if install:
            return Detection(
                BOOT_WINDOWS_SETUP,
                "Windows Setup",
                bcd_path=bcd,
                boot_sdi_path=sdi,
                boot_wim_path=boot_wim,
                setup_path=first("setup.exe", "sources/setup.exe") or "setup.exe",
            )
        name = "Hiren's BootCD PE" if has("hbcd_pe.ini") else "WinPE"
        return Detection(BOOT_WINPE, name, bcd_path=bcd, boot_sdi_path=sdi, boot_wim_path=boot_wim)

    # --- Ubuntu & derivatives (casper) -----------------------------------
    kernel = first("casper/vmlinuz", "casper/vmlinuz.efi")
    if kernel:
        initrd = first("casper/initrd", "casper/initrd.lz", "casper/initrd.gz", "casper/initrd.img")
        return Detection(
            BOOT_LINUX,
            "Ubuntu / casper live",
            kernel_path=kernel,
            initrd_paths=[initrd] if initrd else [],
            cmdline="boot=casper ip=dhcp url={iso_url} cloud-config-url=/dev/null",
        )

    # --- Fedora / RHEL / Rocky / Alma ---------------------------------------
    kernel = has("images/pxeboot/vmlinuz")
    if kernel:
        initrd = has("images/pxeboot/initrd.img")
        squash = has("liveos/squashfs.img")
        if squash and not has(".treeinfo"):
            return Detection(
                BOOT_LINUX,
                "Fedora / RHEL live",
                kernel_path=kernel,
                initrd_paths=[initrd] if initrd else [],
                cmdline=f"root=live:{{files_url}}/{squash} rd.live.image ip=dhcp",
            )
        return Detection(
            BOOT_LINUX,
            "Fedora / RHEL installer",
            kernel_path=kernel,
            initrd_paths=[initrd] if initrd else [],
            cmdline="inst.repo={files_url} ip=dhcp",
        )

    # --- Arch Linux --------------------------------------------------------
    kernel = has("arch/boot/x86_64/vmlinuz-linux")
    if kernel:
        initrd = has("arch/boot/x86_64/initramfs-linux.img")
        return Detection(
            BOOT_LINUX,
            "Arch Linux",
            kernel_path=kernel,
            initrd_paths=[initrd] if initrd else [],
            cmdline="archisobasedir=arch archiso_http_srv={files_url}/ ip=dhcp",
        )

    # --- Debian live ---------------------------------------------------------
    kernels = match(r"^live/vmlinuz[^/]*$")
    if kernels:
        initrds = match(r"^live/initrd[^/]*$")
        squash = has("live/filesystem.squashfs") or "live/filesystem.squashfs"
        return Detection(
            BOOT_LINUX,
            "Debian live",
            kernel_path=kernels[0],
            initrd_paths=initrds[:1],
            cmdline=f"boot=live components fetch={{files_url}}/{squash} ip=dhcp",
        )

    # --- Alpine --------------------------------------------------------------
    kernels = match(r"^boot/vmlinuz-[a-z0-9]+$")
    if kernels and (has(".alpine-release") or has("apks/.boot_repository") or match(r"^apks/")):
        kernel = kernels[0]
        flavor = kernel.rsplit("-", 1)[1]
        initrd = has(f"boot/initramfs-{flavor}")
        apks = (has("apks/.boot_repository") or "apks/x").rsplit("/", 1)[0]
        modloop = has(f"boot/modloop-{flavor}") or f"boot/modloop-{flavor}"
        return Detection(
            BOOT_LINUX,
            f"Alpine Linux ({flavor})",
            kernel_path=kernel,
            initrd_paths=[initrd] if initrd else [],
            cmdline=(
                "ip=dhcp modules=loop,squashfs "
                f"alpine_repo={{files_url}}/{apks} modloop={{files_url}}/{modloop}"
            ),
        )

    # --- Proxmox installers (VE, Backup Server, Mail Gateway) ----------------
    kernel = has("boot/linux26")
    if kernel and match(r"^\.[a-z0-9-]+-cd-id\.txt$"):
        initrd = has("boot/initrd.img")
        # The installer's init only looks for its ISO on local disks, or at
        # /proxmox.iso inside the initramfs: iPXE puts the raw ISO there.
        # rootfstype=ramfs: the default tmpfs root is capped at half of the
        # RAM the kernel has set up that early, and unpacking the ISO into it
        # failed with "write error" on a 6 GB client.
        return Detection(
            BOOT_LINUX,
            _proxmox_name(root, has(".cd-info")),
            kernel_path=kernel,
            initrd_paths=([initrd] if initrd else []) + [f"{RAW_ISO_INITRD} /proxmox.iso"],
            cmdline="ro ramdisk_size=16777216 rw quiet splash=silent rootfstype=ramfs",
        )

    return Detection(BOOT_SANBOOT, "Unknown (raw ISO via sanboot)")


def _proxmox_name(root: Path, cd_info: str | None) -> str:
    """"Proxmox VE 9.2 installer", from the ISO's ``.cd-info`` when readable."""
    info: dict[str, str] = {}
    if cd_info:
        try:
            for line in (root / cd_info).read_text(errors="replace").splitlines()[:50]:
                key, sep, value = line.partition("=")
                if sep:
                    info[key.strip()] = value.strip().strip("'\"")
        except OSError:
            pass
    name = " ".join(v for v in (info.get("PRODUCTLONG"), info.get("RELEASE")) if v)
    name = re.sub(r"[^A-Za-z0-9 ._-]", "", name)[:60].strip()
    return f"{name or 'Proxmox'} installer"


def apply_detection(iso: IsoImage, det: Detection) -> None:
    iso.boot_method = det.boot_method
    iso.detected_os = det.detected_os
    iso.kernel_path = det.kernel_path
    iso.initrd_paths = det.initrd_paths
    iso.cmdline = det.cmdline
    iso.bcd_path = det.bcd_path
    iso.boot_sdi_path = det.boot_sdi_path
    iso.boot_wim_path = det.boot_wim_path
    iso.setup_path = det.setup_path


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _usable_tree(root: Path) -> bool:
    files = list_files(root, limit=5)
    # A Windows ISO read through its ISO9660 layer only shows README.TXT.
    return len(files) > 1


_LINK_REFUSED = "Dangerous link path was ignored"


def _only_refused_links(stderr: str) -> bool:
    """True when 7-Zip's only errors are symbolic links it refused to create.

    7-Zip refuses every link whose target has a ``.`` or ``..`` part, even
    when it stays inside the image, and then exits with 2 (Proxmox installer
    ISOs link ``dists/.../x.deb`` to ``../../../../proxmox/packages/x.deb``).
    """
    lines = [line for line in (stderr or "").splitlines() if line.strip()]
    return bool(lines) and all(_LINK_REFUSED in line for line in lines)


def _listed_links(tool: str, iso_path: Path, fmt: str | None) -> list[tuple[str, str]]:
    """``(path, target)`` of every symbolic link in the image."""
    cmd = [tool, "l", "-slt", "-ba"]
    if fmt:
        cmd.append(f"-t{fmt}")
    cmd.append(str(iso_path))
    res = subprocess.run(cmd, capture_output=True, text=True)
    links: list[tuple[str, str]] = []
    path = None
    for line in res.stdout.splitlines():
        if line.startswith("Path = "):
            path = line[len("Path = "):]
        elif line.startswith("Symbolic Link = ") and path:
            target = line[len("Symbolic Link = "):]
            if target:
                links.append((path, target))
            path = None
    return links


def _restore_links(tool: str, iso_path: Path, fmt: str | None, root: Path) -> tuple[int, int]:
    """Create the links 7-Zip refused, when they stay inside ``root``.

    7-Zip leaves an empty file where each refused link belongs. That file is
    replaced by the link if the target exists inside the image, and removed
    otherwise. A directory link to one of its own parents (``debian -> .``)
    is left out too: it holds nothing new and would stop relays caching the
    image. Returns ``(restored, left out)``.
    """
    real_root = root.resolve()
    pending: list[tuple[Path, str]] = []
    for rel, target in _listed_links(tool, iso_path, fmt):
        parts = PurePosixPath(rel).parts
        if not parts or PurePosixPath(rel).is_absolute() or ".." in parts:
            continue
        link = root.joinpath(*parts)
        try:
            link.parent.resolve().relative_to(real_root)
        except ValueError:
            continue
        if link.is_symlink():
            continue  # 7-Zip made this one itself
        if link.exists():
            if not link.is_file() or link.stat().st_size:
                continue  # real content, not a placeholder
            link.unlink()
        pending.append((link, target))

    restored = 0
    progress = True
    while pending and progress:  # a link's target can be another link
        progress = False
        for item in list(pending):
            link, target = item
            if target.startswith("/") or not link.parent.is_dir():
                continue
            resolved = Path(os.path.realpath(link.parent / target))
            if not resolved.exists():
                continue
            try:
                resolved.relative_to(real_root)
            except ValueError:
                continue
            parent = link.parent.resolve()
            if resolved.is_dir() and (resolved == parent or resolved in parent.parents):
                continue
            os.symlink(target, link)
            pending.remove(item)
            restored += 1
            progress = True
    return restored, len(pending)


def extract_iso(iso_path: Path, dest: Path) -> int:
    """Extract ``iso_path`` into ``dest`` (replacing it). Returns file count."""
    tool = seven_zip()
    if not tool:
        raise ExtractionError("7-Zip (7zz/7z) is not installed")
    tmp = dest.with_name(dest.name + ".tmp")
    errors: list[str] = []
    for fmt in ("udf", "iso", None):
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        cmd = [tool, "x", "-y", "-bd", "-bb0", f"-o{tmp}"]
        if fmt:
            cmd.append(f"-t{fmt}")
        cmd.append(str(iso_path))
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode in (0, 1) and _usable_tree(tmp):
            break
        if res.returncode == 2 and _only_refused_links(res.stderr) and _usable_tree(tmp):
            # Every file was extracted; 7-Zip only refused symbolic links.
            restored, dropped = _restore_links(tool, iso_path, fmt, tmp)
            log.info("%s: restored %d symbolic links, left out %d", iso_path.name, restored, dropped)
            break
        errors.append(f"[{fmt or 'auto'}] exit {res.returncode}: {(res.stderr or res.stdout).strip()[-300:]}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ExtractionError("could not extract image: " + " | ".join(errors))
    # 7-Zip may create read-only files; make the tree readable (Samba/HTTP) and removable.
    for dirpath, dirs, files in os.walk(tmp):
        for d in dirs:
            os.chmod(os.path.join(dirpath, d), 0o755)
        for f in files:
            p = os.path.join(dirpath, f)
            if not os.path.islink(p):
                os.chmod(p, 0o644)
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return len(list_files(dest))


class ExtractionManager:
    """Runs extractions in background threads, one per ISO."""

    def __init__(self, settings: Settings, session_factory: sessionmaker[Session]):
        self.settings = settings
        self.session_factory = session_factory
        self._lock = threading.Lock()
        self._running: set[int] = set()

    def is_running(self, iso_id: int) -> bool:
        with self._lock:
            return iso_id in self._running

    def start(self, iso_id: int) -> bool:
        with self._lock:
            if iso_id in self._running:
                return False
            self._running.add(iso_id)
        t = threading.Thread(target=self._run, args=(iso_id,), name=f"extract-{iso_id}", daemon=True)
        t.start()
        return True

    def run_sync(self, iso_id: int) -> None:
        with self._lock:
            self._running.add(iso_id)
        self._run(iso_id)

    def _run(self, iso_id: int) -> None:
        try:
            with self.session_factory() as db:
                iso = db.get(IsoImage, iso_id)
                if iso is None:
                    return
                iso.status = ISO_EXTRACTING
                iso.status_message = "extracting..."
                db.commit()
                src = self.settings.isos_dir / iso.filename
                dest = self.settings.extracted_dir / str(iso.id)
            try:
                count = extract_iso(src, dest)
                det = detect_layout(dest)
            except Exception as exc:  # noqa: BLE001
                log.exception("extraction of ISO %s failed", iso_id)
                with self.session_factory() as db:
                    iso = db.get(IsoImage, iso_id)
                    if iso:
                        iso.status = ISO_ERROR
                        iso.status_message = str(exc)[:2000]
                        db.commit()
                return
            with self.session_factory() as db:
                iso = db.get(IsoImage, iso_id)
                if iso is None:
                    shutil.rmtree(dest, ignore_errors=True)
                    return
                apply_detection(iso, det)
                iso.status = ISO_READY
                iso.status_message = f"{count} files extracted; detected {det.detected_os}"
                db.commit()
                log.info("ISO %s (%s) ready: %s", iso.id, iso.filename, det.detected_os)
        finally:
            with self._lock:
                self._running.discard(iso_id)


def rescan(db: Session, settings: Settings, manager: ExtractionManager) -> dict[str, list[str]]:
    """Register ISOs copied into the isos dir out-of-band; flag missing ones."""
    added: list[str] = []
    missing: list[str] = []
    known = {iso.filename: iso for iso in db.query(IsoImage).all()}
    on_disk = {
        p.name: p
        for p in settings.isos_dir.iterdir()
        if p.is_file() and p.name.lower().endswith(ALLOWED_EXTENSIONS)
    } if settings.isos_dir.is_dir() else {}
    new_ids: list[int] = []
    for name, path in sorted(on_disk.items()):
        iso = known.get(name)
        if iso is None:
            iso = IsoImage(
                name=Path(name).stem, filename=name, size=path.stat().st_size, status=ISO_EXTRACTING
            )
            db.add(iso)
            db.flush()
            new_ids.append(iso.id)
            added.append(name)
        elif iso.status == ISO_MISSING:
            iso.status = ISO_EXTRACTING
            iso.size = path.stat().st_size
            new_ids.append(iso.id)
    for name, iso in known.items():
        if name not in on_disk and iso.status not in (ISO_MISSING, ISO_UPLOADING):
            iso.status = ISO_MISSING
            iso.status_message = "file no longer present in the isos directory"
            missing.append(name)
    db.commit()
    for iso_id in new_ids:
        manager.start(iso_id)
    return {"added": added, "missing": missing}


def recover_on_startup(db: Session, settings: Settings, manager: ExtractionManager) -> None:
    """Resume extractions interrupted by a restart; fail dead uploads."""
    for iso in db.query(IsoImage).filter(IsoImage.status.in_([ISO_EXTRACTING, ISO_UPLOADING])).all():
        path = settings.isos_dir / iso.filename
        if iso.status == ISO_UPLOADING or not path.exists():
            iso.status = ISO_ERROR
            iso.status_message = "upload or extraction was interrupted by a restart"
            (settings.isos_dir / (iso.filename + ".part")).unlink(missing_ok=True)
            continue
        db.commit()
        manager.start(iso.id)
    db.commit()
