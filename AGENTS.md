# AGENTS.md: netdispatch for coding agents

This is the working brief for AI agents and contributors: what each part does, how to build and test it, and the traps already hit. [README.md](README.md) is the end-user guide. Keep the two in step when behaviour changes.

## What it is

PXE boot coordination, hub-and-spoke.

- **Hub (`server/`)** is FastAPI with SQLite. It serves:
  - the dashboard (Jinja + Alpine.js + Tailwind compiled at build time)
  - OIDC or local-admin auth
  - `/boot`, which returns iPXE scripts
  - `/files/*`, which serves boot payloads
  - the admin JSON API under `/api/*`
- **Samba sidecar (`samba/`)** is a read-only SMB share of `data/extracted/`. WinPE runs Windows Setup from it.
- **Relay (`relay/`)** is Alpine + dnsmasq in **ProxyDHCP + TFTP** mode, plus a curl heartbeat. It is stateless and runs with host networking. Its iPXE binaries are **compiled from source** (pinned `v2.0.0`) with an embedded script.
  - For `SECURE_BOOT=true` it also ships the iPXE project's **prebuilt signed** binaries in `/tftp/secureboot/`, downloaded at build time and pinned by SHA-256 (see "Secure Boot" below).
  - With `RELAY_CACHE=true` it is no longer stateless: `cache-sync.sh` mirrors the ISOs an admin assigned to it into a `/cache` volume and serves them on the LAN with lighttpd (HTTP) and smbd (Windows Setup). The hub decides per deployment whether the payload URLs point at the relay or at itself (see "Relay cache" below).

Boot chain:
1. Firmware PXE → relay ProxyDHCP → TFTP `ipxe.efi` / `undionly.kpxe`.
   - With `SECURE_BOOT=true`, UEFI gets `secureboot/ipxe-shimx64.efi` (Microsoft-signed shim), which TFTP-loads the signed `secureboot/ipxe.efi`.
2. The **embedded script** runs `dhcp`, then TFTP-fetches `netdispatch.ipxe` from the relay. The relay's entrypoint generates that file at start-up.
   - The signed iPXE can't embed a script. It fetches `secureboot/autoexec.ipxe` instead, which is `embed.ipxe` copied in at build time, so both paths run the same script.
3. `netdispatch.ipxe` chains `CENTRAL_SERVER_URL/boot?mac=…&relay=…`.
4. The hub returns either a wait-loop script, a payload script, or a local-boot script.

## Layout

| Path | Contents |
|---|---|
| `server/app/main.py` | `create_app(settings)` factory: middleware order, routers, `asset_version` |
| `server/app/config.py` | `Settings` (pydantic-settings). All config comes from env vars, nothing else. |
| `server/app/models.py` | `Relay`, `Device`, `IsoImage`, `UnattendConfig`, `UnattendGenerated`, `RelayCache`, `RelayCacheItem`, plus state/method constants |
| `server/app/auth.py` | pure-ASGI `AuthMiddleware` (exemptions + CSRF header), OIDC routes, local login, rate limit |
| `server/app/pxe_engine.py` | **all iPXE script text**: wait, local, payload (wimboot/linux/sanboot/memdisk) |
| `server/app/routers/boot.py` | `/boot` state machine |
| `server/app/routers/files.py` | `/files/boot`, `/files/iso/{id}/raw`, `/files/iso/{id}/tree`, `/files/d/{token}` |
| `server/app/iso_tools.py` | 7-Zip extraction (background threads), layout detection, `resolve_ci()` safe paths |
| `server/app/relay_cache.py` | relay cache: per-image manifests + etags (`ManifestCache`), `usable_cache()` routing decision, heartbeat cache reports |
| `server/app/winpe.py` | `winpeshl.ini` + `netdispatch.cmd` (net use + `setup.exe /unattend`) |
| `server/app/usb_scripts.py` | iPXE text for boot media: the media script (stands in for the relay's `netdispatch.ipxe`) and `/usb/boot`'s hand-off to `/boot` |
| `server/app/usb_media.py` | builds and caches the online-only boot ISO (xorriso + mtools, no root) |
| `server/app/routers/usb.py` | `/usb/boot` (unauthenticated: picks the client's relay), `/api/usb/info`, `/api/usb/online.iso` |
| `server/app/usb_sticks.py` | USB sticks: pairing and keys, per-stick manifests (`StickManifests`), the layout of hub-rendered files, offline unattend parts, the FAT32 boot template (`BootTemplate`) |
| `server/app/routers/usb_sticks.py` | admin: `/api/usb/profiles`, `/api/usb/creators`, `/api/usb/pairings`, `/api/usb/sticks`; USB Creator and stick writers: `/api/usb/app/*` (`X-Creator-Key` / `X-Stick-Key`) |
| `server/app/unattend.py` | template files in `data/configs`, sandboxed Jinja rendering (CRLF kept), XML validation, `computer_name()` for `{{ hostname }}` |
| `server/app/unattend_generator.py` | runs `netdispatch-unattendgen`, escapes its XML for Jinja, swaps `__ND_*__` placeholders for template variables, `NEW_TEMPLATE_SETTINGS` |
| `server/app/templates/unattend/` | the generator form: `_generator.html`, one `_sec_*.html` per section, `_macros.html` for the controls |
| `server/app/static/unattend-generator.js` | the generator's Alpine state and methods, spread into `netdispatch()`; form state ↔ settings JSON |
| `server/unattendgen/` | .NET 10 wrapper around the upstream unattend-generator library: `src/Core` (settings model `HostConfig`, mapper, validator, `CliCommands`), `src/Cli` (`netdispatch-unattendgen`), `tests/Tests` (xUnit, golden files), `upstream.lock` + `fetch-upstream.sh` (pinned upstream) |
| `server/app/templates/` | `index.html` (whole SPA), `login.html` |
| `server/tests/` | pytest (unit + TestClient integration) |
| `relay/entrypoint.sh` | env validation, interface/subnet detection, writes `dnsmasq.conf` + `/tftp/netdispatch.ipxe` (+ lighttpd config with `RELAY_CACHE=true`) |
| `relay/heartbeat.sh` | heartbeat loop; with `RELAY_CACHE=true` also reports cache status and records the hub's `cache_manifest_etag` |
| `relay/cache-sync.sh` | `RELAY_CACHE=true`: manifest sync (resumable curl, rate limit, sync window), deletes unassigned files, writes `smb.conf`, supervises lighttpd + smbd |
| `relay/ipxe/embed.ipxe`, `relay/ipxe/general.h` | compiled into the iPXE binaries. `embed.ipxe` is also shipped as `secureboot/autoexec.ipxe`. |
| `relay/Dockerfile` | `ipxe` stage (compile), `ipxe-signed` stage (download signed shim + iPXE, SHA-pinned), runtime |
| `server/usbcreator/` | USB Creator, the Windows stick writer (.NET 10, Avalonia): `src/Core` (hub client, `SyncEngine`, stick files and sizing; no OS calls), `src/Windows` (`WindowsDisks`: Storage cmdlets + raw volume writes; DPAPI `CreatorSettings`; `StickWriter`), `src/App` (window built in code, `--cli`), `src/Updater` (`netdispatch-stick-update`, the in-boot updater: linux-musl-x64, rewrites NDBOOT through `/sys/class/block` + the block device), `tests/Tests` (xUnit against a `FakeHub` and a temp folder) |
| `server/usbupdater/` | the stick updater's Linux image: `init` (PID 1: modules, find the stick by label, DHCP, run `netdispatch-stick-update`, restart), `modules.txt`, `build.sh` (Alpine x86_64 kernel + xz initramfs) |
| `tests/e2e/` | QEMU end-to-end harness (isolated bridge, fake router DHCP, BIOS/UEFI/Windows VMs); `make-stick.py` writes a USB stick image through the stick API, like USB Creator |
| `examples/unattend/` | tested sample templates |

## Build and test

```bash
# Unit/integration tests (no local Python needed)
docker build --target test -t netdispatch-test server/ && docker run --rm netdispatch-test

# Strict mode: fail on deprecation warnings too (used during development)
docker run --rm netdispatch-test python -m pytest -q -W error::DeprecationWarning tests

# Build everything from this checkout (needs buildx >= 0.17; distro packages are often older)
docker compose -f docker-compose.yml -f docker-compose.build.yml --profile local-relay build
```

`docker-compose.yml` and `docker-compose.relay.yml` are the user-facing files: they run the published `ghcr.io/photoncody/netdispatch-*:latest` images and hold every setting inline, so users download just the file without cloning. `docker-compose.build.yml` is the development override that swaps in local builds tagged `netdispatch-<name>:local`. Don't add `build:` back to the user-facing files: it breaks them for anyone without a checkout.

The server Dockerfile's stages:
- `assets`: Tailwind CLI, Alpine.js and wimboot, each SHA-pinned.
- `unattendgen-base` / `unattendgen`: the .NET SDK (pinned by digest) fetches the upstream library and publishes `netdispatch-unattendgen` as one self-contained file for the target architecture. It cross-compiles on the build platform, so arm64 needs no emulation.
- `unattendgen-test`: runs the generator's .NET tests. CI builds it; the runtime image does not depend on it.
- `ipxe-signed` (the signed shim, `ipxe.efi` and `snponly.efi`, same pins as `relay/Dockerfile`: keep them in step) and `ipxe-media` (`ipxe.lkrn` and `ipxe-native.efi` from source) supply the boot media's files in `/opt/netdispatch/usb/`, beside ISOLINUX and SYSLINUX's `mbr.bin` from Debian. The `syslinux` package's installer (mtools build) makes the stick boot template boot on BIOS. Debian builds it for x86 only, so the arm64 image leaves it out and makes UEFI-only sticks (`template_bios()`, `X-ND-Bios: 0`).
- `usbcreator-base` / `usbcreator` / `usbcreator-test`: USB Creator, published as one self-contained `win-x64` .exe (same pinned SDK as `unattendgen-base`: keep the digests in step). The runtime image serves it from `/opt/netdispatch/usb/` at `/api/usb/creator`. Avalonia builds on Linux, so no Windows runner is needed.
- `stick-updater-app` / `stick-updater`: the in-boot stick updater. `netdispatch-stick-update` is published self-contained for `linux-musl-x64` (not trimmed: System.Text.Json runs on reflection), and `usbupdater/build.sh` unpacks Alpine's x86_64 `linux-lts`, busybox and .NET's native dependencies with `apk --root --arch x86_64` on the build platform, so arm64 needs no emulation. Only the modules in `modules.txt` and their dependencies go in, uncompressed, with `depmod` run on the build side. The runtime image gets `ndusb-vmlinuz` (about 14 MB) and `ndusb-initramfs` (about 34 MB) in `/opt/netdispatch/usb/`, served at `/files/boot/`.
- `test` and `runtime`. `runtime` is the last stage and therefore the default target.

The generator's .NET tests on their own: `docker build --target unattendgen-test server/`. On a machine with the .NET 10 SDK: `server/unattendgen/fetch-upstream.sh && dotnet test --solution server/unattendgen/Unattendgen.slnx`. The relay build compiles iPXE (about 60 s). Any change to `relay/ipxe/*` needs a relay rebuild **and** a restart of running relays.

### CI (GitHub Actions, target under 5 minutes per run)

- **`.github/workflows/ci.yml`**
  - `shellcheck` on every shell script, and `docker compose config` on both compose files.
  - pytest inside the Dockerfile's `test` stage, with the GHA layer cache, run with `-W error::DeprecationWarning`.
  - The unattend generator's .NET tests: the Dockerfile's `unattendgen-test` stage, which fails the build on a failing test.
  - USB Creator's .NET tests: the `usbcreator-test` stage.
- **`.github/workflows/images.yml`** builds `server`, `relay` and `samba` for amd64 and arm64.
  - Each architecture builds on **native** runners (`ubuntu-24.04` / `ubuntu-24.04-arm`). There's no QEMU emulation, so it stays fast.
  - Each build job pushes a per-architecture digest. The `Publish to GHCR` job then merges them into multi-arch tags on `ghcr.io/<owner>/netdispatch-<image>`:

    | Trigger | Tags |
    |---|---|
    | Pull request from this repo | `pr-<number>`, `sha-…` |
    | `main` | `latest`, `main`, `sha-…` |
    | `v*` tag | `X.Y.Z`, `X.Y`, `sha-…` |

    To test a PR on real hardware before merging, point a hub or relay at the `pr-<N>` tag. The run summary lists the pull commands.
  - **Fork PRs only build.** They get no token that can push packages, so `Publish to GHCR` is skipped for them. A skip anywhere else is a bug.
  - `pr-*` tags aren't cleaned up automatically. Prune old ones in the GitHub package settings if they pile up.
  - On arm64 runners the relay's iPXE builder runs as arm64 and **cross-compiles** the x86 boot binaries (`CROSS=x86_64-linux-gnu-`). Keep that branch of `relay/Dockerfile` working.
- **The QEMU end-to-end test is deliberately not in CI.** It needs root, KVM, an ISO download and minutes of VM boot time. Run it by hand (below) for changes to the boot chain, relay or WinPE flow.

### End-to-end test (Linux host, root, KVM recommended)

You need these packages: `qemu-system-x86 ovmf dnsmasq-base`.

1. Bring up the stack and the test LAN:
   ```bash
   # In docker-compose.yml set BOOT_BASE_URL: http://10.77.0.1:8000, SMB_HOST: 10.77.0.1,
   # SMB_PASSWORD (x-smb block) and FALLBACK_ADMIN_PASSWORD. Don't commit those edits.
   docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
   sudo tests/e2e/net-up.sh
   ```
2. Create a relay with `POST /api/relays` (or in the UI), then run it on the test bridge:
   ```bash
   docker run -d --name nd-relay-test --network host --cap-add NET_ADMIN \
     -e CENTRAL_SERVER_URL=http://10.77.0.1:8000 -e RELAY_ID=lab -e RELAY_API_KEY=<key> \
     -e RELAY_INTERFACE=ndtest0 netdispatch-relay:local
   ```
3. Boot a client:
   ```bash
   sudo tests/e2e/vm.sh uefi
   ```
   The other modes are `bios` and `win new`.
   - **Relay cache:** give `ndtest0` a second address (`sudo ip addr add 10.77.0.2/24 dev ndtest0`), publish the hub's samba on `10.77.0.1:445` only (a compose override with `ports: !override`), and run the relay with `-e RELAY_CACHE=true -e CACHE_BIND=10.77.0.2 -e CACHE_URL=http://10.77.0.2:8080 -v /var/tmp/nd-relay-cache:/cache`. Both smbds can then listen on port 445 on one host. Assign images with `PUT /api/relays/<id>/cache`. Relay downloads show as `[netdispatch-cache] http …` lines in `docker logs`; `smbstatus -s /etc/samba/netdispatch.conf` inside the relay lists WinPE's session.
   - `uefi-sb` and `win-sb new` run OVMF with Secure Boot enforced and only Microsoft's keys enrolled (`OVMF_CODE_4M.secboot.fd` + `OVMF_VARS_4M.ms.fd`, SMM on).
   - They need a relay started with `-e SECURE_BOOT=true`. Without it, OVMF refuses our `ipxe.efi` with *Access Denied*. That's a useful negative check.
4. Inspect what happened:
   ```bash
   sudo python3 tests/e2e/screenshot.py uefi /tmp/u.png
   docker logs nd-relay-test
   docker compose logs server
   ```
   Also check `/tmp/ndrouter-dhcp.log` and `/tmp/ndrouter.leases`. Windows registers its computer name there, which is an easy way to verify `{{ hostname }}`.
5. Clean up:
   ```bash
   sudo tests/e2e/net-down.sh
   docker rm -f nd-relay-test
   docker compose down
   ```

Verified with this harness:
- UEFI native PXE and BIOS (iPXE ROM chainload) reach the hold loop.
- Alpine boots on both.
- Windows 11 installs unattended to the desktop from `examples/unattend/win11-uefi-basic.xml`.
- With the install share down (samba stopped), WinPE keeps retrying with diagnostics: 47 attempts over 12 minutes, no reboot. It starts Setup on the first attempt after the share is back.
- With Secure Boot enforced (`uefi-sb`, `win-sb`, relay `SECURE_BOOT=true`):
  - the hold loop and local boot (the firmware moves on to the next entry);
  - the DHCP fallback when `autoexec.ipxe` is missing;
  - the Windows 11 unattended install;
  - BIOS and non-Secure Boot UEFI clients, unchanged, on the same relay.
- The online-only boot ISO (`vm.sh iso-uefi`, `iso-uefi-sb`, `iso-bios`, `iso-uefi-native`, with `/api/usb/online.iso` saved to `/var/tmp/netdispatch-online.iso`):
  - all four reach the hold loop, recorded under the relay that answered by ProxyDHCP;
  - local boot: UEFI exits 1 and the firmware moves on to its next entry; BIOS runs `sanboot` on drive 0x80;
  - `iso-uefi-native` (no firmware driver for the NIC) chains the native-driver iPXE; the same machine with Secure Boot enforced is refused and shows the explanation.
- USB sticks (`make-stick.py` image, `vm.sh usb-uefi`, `usb-uefi-sb`, `usb-bios`, `usb-win`):
  - online, all three reach the hold loop under the relay that answered; the hold loop offers L on UEFI;
  - L in the hold loop: the child exits 1 and the parent shows the stick's menu; *Boot from local disk* exits 1 to the firmware's next entry; the hub's local boot exits 0 from the child and 1 from the parent;
  - BIOS local boot reaches a marker disk on drive 0x81;
  - after a network attempt, OVMF has lost the stick: choosing its image shows the "restart and press L" message;
  - `usb-win` (no hub reachable): L at the first prompt, the Windows entry, the name prompt (default `ND-AA000D` from the MAC; `bad name` and `12345` refused), Setup from the stick's exFAT partition, the reboots back through the stick's menu to the disk, and the desktop with `hostname` = the typed name. About 40 minutes, most of it Setup reading from QEMU's emulated USB stick;
  - `make-stick.py` downloaded the image from the relay cache.
- USB Creator on Windows 11 against a 16 GB VHDX (`--cli`, `--allow-virtual-disks`): pairing, an online-only stick (create, then update), converted to raw and booted in QEMU on UEFI and BIOS to the hold loop. The window itself hasn't been driven by a test.
- The in-boot updater (a `make-stick.py` stick behind a qcow2 overlay, `ND_STICK=….qcow2`; one image file touched on the hub to make the copy outdated):
  - `usb-uefi`: the prompt; its timeout boots as usual; Y starts the updater, which rewrote NDBOOT (the hub image had been rebuilt) and recopied the boot files, then only the changed file. After the restart the stick reported the new etag and wasn't prompted again;
  - `usb-uefi-sb`: Y, the firmware refuses the kernel, the message, then the hold loop;
  - `usb-bios`: Y, the updater from `ipxe.lkrn`, only the changed file (0 MB), no rewrite.
- With a caching relay (`RELAY_CACHE=true`):
  - the sync of Windows 11 and Debian netinst, with every cached file byte-identical to the hub's;
  - a re-check after restart without re-downloading, and a resync of only the changed file set after re-extraction;
  - UEFI `linux` (kernel + initrd) and `sanboot` (HTTP Range) from the relay, with clients fetching only wimboot and `/files/d/*` from the hub;
  - Windows Setup over the relay's SMB share, unattended through to the registered hostname;
  - the fallback to the hub while an image is re-extracted;
  - WinPE's switch from an unreachable relay share to the hub's (`:use_hub`). The relay's port 445 was rejected (`iptables -I INPUT -d 10.77.0.2 -p tcp --dport 445 -j REJECT --reject-with tcp-reset`) and the client was `win-sb`. WinPE tried the relay 3 times, printed diagnostics, switched to the hub's share and started Setup. Before the copy fix (see "`netdispatch.cmd` runs from a copy" under Gotchas), WinPE rebooted before attempt 3.
- With a generated template (the generator's new-template settings, image index 1 for the LTSC evaluation ISO):
  - `win new` from the hub, and `win-sb new` through a caching relay (boot files over its HTTP cache, `install.wim` over its SMB share). In both, the generated `pe.cmd` wiped and partitioned disk 0, applied the image and rebooted; the device went `local` on its next `/boot`, and Windows reached the desktop with auto-logon as `Admin`.
  - The sanitised hostname registered in `/tmp/ndrouter.leases`: label `Gen Test 01` became `Gen-Test-01`, `SB_Test.Relay` became `SB-Test-Relay`.
  - The WinPE stage took about 15 minutes in the first run and about 25 in the second, most of the difference between `pe.cmd` printing "Computer will now reboot" and the actual reset. The cause isn't known. It isn't the relay share alone: the run below used the relay share without Secure Boot and its WinPE stage took under 9 minutes.
  - Custom scripts and Windows settings (`win new`, relay cache): one script per phase, each requesting a marker URL from the hub so that its access log shows the script ran. All four ran: `System` in the specialize pass (with `__ND_HOSTNAME__` replaced), `FirstLogon` (with `__ND_MAC_PLAIN__` replaced, and seeing the folder the `System` script made), and `UserOnce`, which read back the value the `DefaultUser` .reg file wrote. The desktop showed `LeftTaskbar`, `HideTaskViewButton` and `taskbarSearch: Hide` applied. Not checked inside the guest: `EnableRemoteDesktop`, `ShowFileExtensions`, and app removal (the LTSC image has none of those apps).
  - Appearance, layout and advanced settings (`win new`, relay cache), about 16 minutes from power-on to the desktop. A `FirstLogon` and a `UserOnce` script read the values back and reported them through a marker URL. Applied: Remote Desktop on through a custom `Microsoft-Windows-TerminalServices-LocalSessionManager` component, Num Lock on and Caps Lock ignored (`Scancode Map`), process auditing with command lines, an audit-only AppLocker policy (one rule collection, `AppIDSvc` running), Sticky Keys flags, visual effects `BestPerformance`, the dark theme for Windows and apps, and the two Start folders. The desktop showed the plain-colour wallpaper, the four chosen desktop icons, the accent colour on Start and taskbar, no pinned apps in Start and no pinned taskbar icons. Not run: a Wi-Fi profile (the VM has no Wi-Fi adapter), the wallpaper and lock screen scripts, custom pinned-app, tile and taskbar layouts, and a `windowsPE` component.

## Core invariants (don't break these)

1. **`/boot` never re-serves a payload.**
   - The states are `waiting` → `deploying` (dispatched) → `booting` (payload served) → `local` or `waiting`, depending on `after_deploy`.
   - A device in `booting` that hits `/boot` again has rebooted, for example Windows Setup rebooting. Re-serving the payload there would loop the install forever.
   - `result=failed` (sent by the payload script's `:failed` path) returns the device to `waiting`.
2. **Local boot = exit with a failure status, end to end.**
   - UEFI firmware only tries the next boot entry when a boot option *fails*. A successful return drops EDK2 into its setup menu. The original bug was exactly this: it worked on SeaBIOS and broke on OVMF.
   - The mechanism: `render_local()` does `set nd-local 1` and `exit 0`. The relay's `netdispatch.ipxe` and `embed.ipxe` both check `isset ${nd-local}` and `exit 1`. Any other return means "hub unreachable", so they retry.
   - **This logic spans three files** (`pxe_engine.py`, `relay/entrypoint.sh` heredoc, `relay/ipxe/embed.ipxe`), plus the boot-media scripts in `usb_scripts.py`. Change them together.
   - A USB stick adds one level: its network script runs in a child iPXE, which exits **0** for a local boot and 1 to go back to the stick; the stick's own iPXE then exits 1 to the firmware (`render_stick_parent`).
   - Boot media follow the same rule on UEFI (`exit 1`). The BIOS media script was loaded by ISOLINUX as a kernel and has nothing to return to, so it runs `sanboot --no-describe --drive 0x80` instead.
   - The Secure Boot path uses the same `embed.ipxe` (as `autoexec.ipxe`). Its `exit 1` makes the signed iPXE fail, the shim passes the failure on, and the firmware tries the next entry. This was verified on OVMF with Secure Boot enforced.
3. **The wait loop uses `chain --replace --autofree`.**
   - Without `--replace`, every poll nests a script and iPXE eventually runs out of memory.
   - `--replace` keeps the call stack, so the replaced script's `exit` still returns to the relay script.
4. **Absolute URLs come only from `Settings.boot_url`** (`BOOT_BASE_URL`, falling back to `PUBLIC_URL`). Never use the request `Host` or `X-Forwarded-Host`. The OIDC redirect URI is always `PUBLIC_URL/auth/oidc/callback`.
   - The one exception is the `{iso_url}` / `{files_url}` cmdline placeholders. They use `Settings.effective_linux_fetch_url` (`LINUX_FETCH_URL`, falling back to the boot URL) through `BootContext.fetch_url`, because casper and live-boot download with busybox `wget`, which has no TLS.
   - iPXE's own `kernel`/`initrd` lines and every `/files/d/{token}` URL stay on `boot_url`, so secrets never go over the plain-HTTP fetch URL.
   - The second exception is the relay cache. When `relay_cache.usable_cache()` says the device's relay holds a current copy, `BootContext.cache_base_url` replaces the hub for the image's own files: the tree, the raw ISO, and both placeholders. That URL comes only from the relay's key-authenticated heartbeat and is checked by `valid_cache_url()` (`http://host[:port]`, nothing else), never from the request. wimboot/memdisk and `/files/d/{token}` stay on `boot_url`.
5. **Every variable string echoed into an iPXE script goes through `ipxe_text()`.**
   - It strips `$ { } & | # " '` and similar characters. Labels and relay names are user-controlled, and `${…}` would be expanded and `&&`/`||` parsed by iPXE.
   - Relay IDs go through `safe_slug()`.
   - The kernel cmdline is admin-controlled and is intentionally not sanitised.
6. **Secrets are only served under `/files/d/{token}/`.**
   - These are the unattend XML and the WinPE `.cmd` that carries SMB credentials.
   - Each deployment gets a new token. It is valid only while the device is `deploying` or `booting`, and is cleared on reset/local/return.
   - `/files/iso/*` is deliberately unauthenticated (boot clients can't log in), so never put secrets there.
   - The one other place the SMB password leaves the hub is the relay cache manifest (`GET /api/relays/cache/manifest`, `X-Relay-Key` only), so a caching relay can serve the install share. It's only included when a `windows_setup` image is assigned.
7. **Auth exemptions are an explicit allow-list** in `auth.py` (`EXEMPT_EXACT`, `EXEMPT_PREFIXES`).
   - `/api/relays/heartbeat` and `/api/relays/cache/manifest` are exempt and authenticated by `X-Relay-Key`. `heartbeat` and `cache` are reserved relay IDs.
   - Relay *management* under `/api/relays` is not exempt.
   - Mutating `/api/*` calls require `X-Requested-With: netdispatch` (the CSRF guard). The frontend's `api()` helper and the upload XHR send it.

## Gotchas (each of these has bitten us)

**iPXE / relay**
- **Locating the relay from iPXE:**
  - Use `${proxydhcp/dhcp-server}` (option 54 of the relay's ProxyDHCP offer).
  - `${proxydhcp/next-server}` is **empty** in dnsmasq proxy offers.
  - Plain `${next-server}` is usually the *router* (dnsmasq and many routers set siaddr to themselves). The BIOS client tried to TFTP from the router until this was fixed.
- **`IPXE_CLIENTS=chainload` (the default) gives every client our iPXE build.**
  - That includes NIC-ROM and VM-firmware iPXE.
  - It can't loop, because the embedded script ignores the DHCP boot filename.
  - `direct` uses dnsmasq user-class tags to send existing-iPXE clients straight to `netdispatch.ipxe`.
- **iPXE ANSI:** `set esc:hex 1b`, then `${esc:string}[2J`. A plain `${esc}` prints the hex text "1b".
- **Trailing `||` in iPXE:** use explicit `|| goto label` rather than a trailing `||`.
- **iPXE EFI looks for `autoexec.ipxe` before running the embedded script.**
  - It looks in its own TFTP directory first, then in the TFTP root. The "not found" TFTP log lines are harmless.
  - Don't add `autoexec.ipxe` to the TFTP root: it would run *instead of* the embedded script, before `dhcp`.
  - `secureboot/autoexec.ipxe` is deliberate. The signed iPXE has no embedded script, and only clients loaded from `secureboot/` find that file.
- **Secure Boot (`SECURE_BOOT=true`):**
  - Sources: `ipxeboot.tar.gz` from the ipxe/ipxe release (`x86_64-sb/ipxe.efi`, `snponly.efi`, signed by the iPXE CA), plus `ipxe-shimx64.efi` from the ipxe/shim release. The release shim is dual-signed with the Microsoft UEFI CA 2011 and 2023, while the tarball's `shimx64.efi` has only the 2011 signature. Neither project publishes checksums: the pins in `relay/Dockerfile` are our own, and a re-uploaded asset fails the build on purpose.
  - The shim loads the iPXE named after itself (`<name>-shimx64.efi` → `<name>.efi`, same TFTP directory). That's why `snponly-shimx64.efi` is a copy of the same shim, and why the files live in a subdirectory: the signed `ipxe.efi` would clash with ours.
  - TFTP log lines about missing `revocations_*.efi` / `shim_certificate_0.efi` come from the shim probing for optional files. They're harmless.
  - **Loop guard:** if `autoexec.ipxe` can't be fetched, the signed iPXE falls back to DHCP autoboot. Handing it the shim again would nest forever. So with `SECURE_BOOT=true`, UEFI clients with user class `iPXE` always get `netdispatch.ipxe`, as in `IPXE_CLIENTS=direct`.
  - The signed iPXE is prebuilt. It ignores `relay/certs/` (no private CA) and `general.h`.
  - Payloads under Secure Boot: wimboot (the pinned release is signed) and Windows are signed. A `linux` kernel loaded directly is refused. `sanboot` works when the ISO's own loader is signed. Distribution kernels could also boot through iPXE's `shim` command with the ISO's `BOOTX64.EFI`; that isn't implemented.
- **dnsmasq:**
  - `pxe-service` basenames that contain a dot are used verbatim, while names without one get `.0` appended.
  - `port=0` disables DNS. Keep it, since relays must not answer DNS.
- **Relays need `network_mode: host` + `NET_ADMIN`.** Docker Desktop can't receive DHCP broadcasts.

**Boot media**
- **How a media client finds its relay.** The media script sends `relay_ip=${proxydhcp/dhcp-server}` to `/usb/boot`: iPXE's DHCP request carries `PXEClient`, so a relay on the subnet answers with ProxyDHCP and option 54 is its interface address, which its heartbeat reports as `ip_address`. Failing that, the client IP is matched against the relays' reported `subnet` (`relay_cache.relay_for_client()`: most specific wins, `/0` and unparsable values never match). `/usb/boot` then chains `/boot?…&relay=<id>`, or `relay=-` when there's none: `-` clears a relay recorded by an earlier PXE boot, so a stale relay's cache isn't offered to a client that can't reach it. No relay change is needed.
- **UEFI media boot the signed `ipxe.efi`, never `snponly.efi`.** `snponly.efi` only drives the NIC it was loaded from; loaded from a CD or stick it has no network. The signed `ipxe.efi` drives NICs through the firmware's SNP/NII and has native drivers for only a few Realtek chips (plus USB host and `usbblk`). The shim is `EFI/BOOT/BOOTX64.EFI` and loads `ipxe.efi` from the same folder.
- **No firmware network device → `EFI/BOOT/native/ipxe.efi`**, built from source with all native drivers, unsigned. The script `imgfetch`es `native/autoexec.ipxe` under the name `autoexec.ipxe` before `chain`ing it, so the chained iPXE finds its script in our virtual filesystem. Under Secure Boot the firmware refuses it; the script says so and exits 1.
- **OVMF only connects the devices in QEMU's boot order.** A NIC without `bootindex` gets no SNP when booting from CD, and the media find no network. `vm.sh`'s `iso-*` modes give the NIC `bootindex=2`. `iso-uefi-native` uses a `pcnet` NIC without an option ROM: OVMF has an e1000 driver, so an e1000 doesn't exercise the fallback.
- **El Torito EFI image under 32 MB, exact size.** El Torito stores the size in 512-byte sectors in 16 bits; xorriso writes 0 for a larger image, which EDK2 can't read. The ISO's FAT image is 8 MB.
- **No ISO9660 on USB sticks.** With an El Torito catalog present, EDK2 treats a USB disk as a CD and hides its MBR partitions. The online ISO is therefore not isohybrid.
- **The online ISO is cached** in `<data_dir>/usb-cache/online-<hash>.iso`, keyed on the rendered scripts (hence the boot URL) and the boot files' size and mtime. Older builds are deleted.

**USB sticks**
- **Two iPXE instances, because the network iPXE takes the stick away.** The signed full `ipxe.efi` drives the USB controllers itself (xHCI, `usbblk`) once it starts, so its `file:` reads fail (0x7f4de08e). Reading the stick through iPXE's own USB driver (`sanhook usb:<pci>-<port>-1.0`) worked only sometimes in QEMU, after minutes of SCSI timeouts. So the stick's `EFI/BOOT/ipxe.efi` is the signed **`snponly.efi`** (the parent): it drives nothing, reads the stick through the firmware, and has no network. For the network it chains the full `ipxe.efi` (the child) through the `shim` command. `autoexec.ipxe` is loaded before iPXE connects its drivers, which is why it can be read at all.
- **The firmware may lose the stick once the child has run.** On exit the child reconnects the firmware's drivers, and in QEMU/OVMF exactly one read from the stick works before it disappears for good; reconnecting again (another iPXE's exit) doesn't bring it back. So the parent loads everything the network needs (child, its script, the native fallback, the shim) into memory first: *Use the network* never needs the stick. A stick image chosen after a network attempt may fail to load: the script says to restart and press L, which reaches the menu without touching the network. Not seen on real hardware yet.
- **The child reads the parent's memory.** A chained iPXE's `file:/` is its parent's virtual filesystem: the parent `imgfetch`es `net.ipxe` as `autoexec.ipxe` (the name iPXE looks for at start-up), and the child finds `native.ipxe`/`native.efi` there.
- **`shim` only registers the shim when it's needed:** with an image selected that the firmware can load directly (Secure Boot off), `shim <image>` unregisters instead. The parent runs `shim` with no argument after the child returns, before booting wimboot.
- **`imgfree` before booting a stick image.** wimboot injects every image iPXE holds into WinPE, including the child and native iPXE the parent keeps in memory.
- **Offline computer name.** iPXE can't write files and can't validate input, so `netdispatch.cmd` asks for the name in WinPE (`set /p`), validates it there (strip every allowed character; anything left is invalid), and joins the answer file's parts around it with `copy /b`. The parts are the template rendered with `{{ hostname }}` as a marker (`usb_sticks.offline_unattend_parts`); a filter on `hostname` hides the marker, and the name is then not used.
- **The default name comes from `ipconfig /all`** ("Physical Address", English WinPE only), else `ND-<random>`.
- **Boot template.** The hub builds the FAT32 boot partition (mkfs.fat with the partition offset as hidden sectors, mtools, `syslinux --install`) and sends only its head up to the last used cluster (`fat_used_bytes`), gzipped; a writer writes it at the partition start and leaves the rest of the partition as it is. SYSLINUX writes its boot code to the boot sector only, so the template copies it over the FAT32 backup boot sector. Cached per size and offset in `<data_dir>/usb-cache`, keyed on the boot files (`boot_files_version`).
- **BIOS stick local boot is drive 0x81**: the stick itself is 0x80 (`sanboot --drive 0x81`), unlike the online ISO's 0x80.
- **The update prompt** is part of `/usb/boot`'s hand-off (`render_media_handoff(update=…)`, `usb_sticks.stick_updates`). It compares the stick's `have` (iso id → 8 hex digits of the etag) with the hub's images and offers images that differ or are new to the profile; never for a stick with no images, a revoked stick, an image the stick reported as `error`, or a device in `deploying`/`booting`. Y loads `ndusb-vmlinuz` with `nd.hub=` and `nd.stick=`: the stick's own key (`netdispatch/stick.key`) authenticates the updater, so nothing secret is on the kernel command line or under `/files/boot/`.
- **The updater is unsigned.** Under Secure Boot the child iPXE's `boot` fails with *Security Policy Violation* (0x7f04819a); the script frees both images, explains, and chains `/boot` as usual.
- **The updater's kernel needs `af_packet`** for udhcpc (it's a module in Alpine's `linux-lts`). Its init loads every module in `modules.txt` at start-up rather than probing hardware.
- **A boot-partition rewrite drops every image from the stick's state** (`SyncEngine.ForgetImages`) before the template is written: the template empties NDBOOT, so each image comes back only once its boot files are copied again. A rebuilt hub image changes `boot_files_version` (it keys on the boot files' size and mtime), so the first update after a hub upgrade rewrites NDBOOT and recopies the images' boot files (about 540 MB for Windows 11).
- **FAT keeps local time.** A stick written on Windows has NDBOOT times in the writer's zone, which the updater (UTC) reads shifted by that zone's offset, and back. `SyncEngine.SameMtime` accepts a shift by whole quarter hours up to 14 hours, plus FAT's 2 seconds, so neither side re-downloads the boot files.
- **Stick keys** (`nds_…`) open one stick's manifest, layout and report; creator keys (`ndc_…`) open every stick's. Pairing hands out the creator key once, to the poll token's holder, after an admin approved the code. `/api/usb/app/` is an exempt prefix in `auth.py`.
- **USB Creator owns the stick layout.** It partitions with the Storage cmdlets (`Clear-Disk`, `Initialize-Disk -PartitionStyle MBR`, `New-Partition -MbrType IFS` for NDDATA, `-MbrType FAT32 -IsActive` for NDBOOT at the end), formats NDDATA exFAT, then writes the boot template's head raw to NDBOOT's volume (`FSCTL_LOCK_VOLUME` + `FSCTL_DISMOUNT_VOLUME` first) and `mbr.bin`'s 440 bytes to the disk. Volumes are opened by `\\?\Volume{GUID}\` path, so no drive letters are needed. `SyncEngine` then copies the files the way `make-stick.py` does: `.nd-<size>-<mtime>.part` with resume, size and mtime (±2 s, exFAT/FAT) checks, a layout without the changing images while they copy.
- **PowerShell from USB Creator goes in as `-EncodedCommand`.** With `-Command -` PowerShell runs standard input line by line, so multi-line blocks never ran and the disk list came back empty. Only the exit code means failure: standard error also carries CLIXML progress records while the Storage module loads.
- **USB Creator never lists the system or boot disk**, only `BusType` USB (and `File Backed Virtual` with the hidden `--allow-virtual-disks`, for VHDX tests on Windows: `New-VHD` + `Mount-VHD`, then `qemu-img convert -O raw` to boot it in QEMU).
- **A stick's manifest never contains the SMB password**: offline Windows Setup reads its files from the stick. Its `cache_url` is the relay cache of the *caller's* subnet (`relay_for_client` on the request address) when that relay holds a current copy.

**Relay cache**
- **The relay stores every path in ASCII lower case** (`relay_cache.ascii_lower()` writes the manifest's `dest`). lighttpd runs with `force-lowercase-filenames` and Samba with `case sensitive = yes` + `default case = lower` + `preserve case = no`. Together these give the relay the same case-insensitive lookups as the hub's `resolve_ci()`, including for paths an admin typed after `{files_url}`. That's also why `CACHE_DIR` must be lower case. An image with two paths that are equal in lower case (`kernel` and `KERNEL`, or a file `a` beside a directory `A/`) can't be stored that way: `build_manifest()` sends it with an `error` and no files, the relay drops its copy and reports the error, and clients always use the hub for it.
- **Readiness is an exact etag match.** An image's etag hashes `(dest, size, mtime)` of its files only, so renames and boot-setting edits don't force a re-download, but re-extraction or a different file set does. `usable_cache()` also requires the relay online and enabled, `http_ok`, status `ready`, and `bytes_done` covering the manifest total. Anything else falls back to the hub. `smb_ok` only decides whether `netdispatch.cmd` maps the relay's share (boot files still come from the relay without it). Never make a deployment wait on the cache.
- **Images that aren't `ready` on the hub** (being re-extracted) appear in the manifest as `{"id": N, "keep": true}`. The relay keeps its files but isn't used until it reports the new etag.
- **Manifests hold hub-relative `src` paths.** The relay prefixes its own `CENTRAL_SERVER_URL`: the address it already reaches the hub at, which can differ from `BOOT_BASE_URL` (e.g. `127.0.0.1` for the hub-local relay).
- **`ManifestCache` memoises** per image on `(updated_at, status, boot_method, cmdline, filename)` with a 5-minute TTL. Heartbeats every 30 s per relay and `/boot` would otherwise walk a Windows tree each time. Call `forget()` when an image is deleted.
- **New tables, not new columns** (`relay_caches`, `relay_cache_items`), because there are no migrations. Rows are deleted explicitly in `delete_iso` and `delete_relay`.
- **Downloads are `<file>.nd-<size>-<mtime>.part`, then renamed.** A partial file from an older version of the image never resumes into the new one. lighttpd denies `.part`; Samba vetoes it.
- **WinPE tries the relay share 3 times, then the hub's** (`winpe.RELAY_TRIES`). `:diag` pings `%ND_HOST%`, whichever host it is currently trying. An unreachable relay share costs each install about a minute (3 attempts) before the switch.
- **Don't enable the cache on the hub-local relay:** smbd on the host network clashes with the samba container's published port 445.

**Windows / SMB**
- **Hiren's PE 1.0.8 / newer Q35 / 4 GiB:** Secure Boot can fail at `cdrom.sys` with `0xc0000428` even with byte-identical official ISO, WIM, BCD, SDI and wimboot files. Reproduced in an isolated nested VM with Proxmox QEMU 11.1.3, OVMF 4.2026.08-1 and `pc-q35-11.1`, using a copy of the failing VM's EFI variables and its CPU feature set. The same QEMU build boots with `pc-q35-10.0` at 4 GiB (also confirmed to the desktop by the user), or `pc-q35-11.1` at 6 GiB (boot-driver stage passed). Changing only `mch.extended-tseg-mbytes` from the new 64 MiB default to 16 MiB also clears it in the lab; QEMU's `pc_compat_10_1` retains that older reservation. The user also confirmed `pc-q35-10.2` fails. The internal Windows/wimboot failure is not traced, so do not claim invalid signatures or demonstrated memory corruption, or bypass signature verification as a fix. See the README's known issue for supported workarounds.
- **Samba must allow execute.** Extracted files are 0644, so the share sets `acl allow execute always = yes`. Without it, WinPE says *Access is denied* on `setup.exe`.
- **Windows 11 24H2+ WinPE refuses guest SMB and requires signing.** That's why the share requires a user and SMB2+.
- **wimboot injects files into `X:\Windows\System32`.**
  - `winpeshl.ini` uses the `[LaunchApps]` / `"netdispatch.cmd"` format.
  - Initrd naming follows the canonical wimboot form: `initrd <url> <name>`.
- **`netdispatch.cmd` is winpeshl's only app.** Shift+F10 doesn't work there, and when the script ends WinPE reboots. So the share retry loop prints `ipconfig`/`ping` diagnostics (`:diag`) and offers a prompt through `choice /c RC /t 10`. If `choice` is missing or has no console it returns 255 or more, and the loop falls back to a `ping` wait.
- **`netdispatch.cmd` runs from a copy, `X:\netdispatch.cmd`.**
  - cmd.exe re-reads a batch file from disk before every line. Read from the wimboot-injected file in `X:\Windows\System32`, the script went wrong a few seconds into WinPE. A line came back cut short, then the file ended early, usually just after a long command (`wpeinit`, `net use`, `ping`) returned. The script stopped and WinPE rebooted, so with the share down it never got past attempt 1 or 2.
  - `type` showed the file intact afterwards, so only cmd's line-by-line reads were affected. The copy, made by the script's first lines and started without `call`, fixed it in the QEMU harness. Keep those lines first.
  - Traced in the harness with a second `[LaunchApps]` entry (it only runs when the first app exits) and markers written to `X:\`. Keep that in mind for anything else long-running that WinPE reads from wimboot's injected files.
- **Windows Setup's boot.wim lacks `findstr`, `choice`, `timeout`, `tasklist` and `fc`.** It has `find`, `ping`, `ipconfig`, `net`, `reg`, `more`, `xcopy`, `robocopy` and `netsh`. A missing command fails with errorlevel 9009, which also triggers a `||` fallback, so `:diag` prints raw `ipconfig`/`ping` output and only uses `find` for its hints.
- **Answer-file scripts look for the answer file themselves.** For example, the schneegans.de generator's `pe.cmd` checks `HKLM\System\Setup!UnattendFile` and `<drive>:\autounattend.xml`, never `X:\Windows\System32` where wimboot puts it. So `netdispatch.cmd` sets that registry value and copies the file to `X:\autounattend.xml` before it starts Setup.
- **Batch escaping in `netdispatch.cmd`:** `%` becomes `%%`, and double quotes are rejected in `SMB_PASSWORD`. `SMB_USERNAME` is restricted to `[A-Za-z0-9._-]`.
- **WinPE only has inbox drivers.** Test VMs must use **e1000e + NVMe/AHCI**: virtio-net means no network in WinPE, and virtio-blk means Setup sees no disk.
- **7-Zip refuses symbolic links whose target has a `.` or `..` part** ("Dangerous link path was ignored") and exits with 2, even when the link stays inside the image. It leaves an empty file in the link's place. Proxmox ISOs have about a hundred such links, so every extraction failed. `extract_iso()` accepts exit 2 when those are the only errors, and `_restore_links()` then replaces each placeholder with the link if its target exists inside the tree, or removes it. A directory link to its own parent (`debian -> .`) is left out, because it would stop relays caching the image.
- **Proxmox installers only find their ISO on a local disk or at `/proxmox.iso` in the initramfs.** An initrd entry `{iso} <name>` (`pxe_engine.raw_iso_initrd_name()`) renders `initrd <raw ISO URL> <name>`: iPXE wraps a named initrd in a cpio header, so the kernel unpacks the ISO as that file. The detected profile adds `rootfstype=ramfs`. Without it the kernel unpacked into a tmpfs root capped at about 1.2 GB on a 6 GB client and printed *Initramfs unpacking failed: write error*. The relay cache keeps the raw ISO for such an image (`_needs_raw()`).
- **7-Zip:** try `-tudf` first. A Windows ISO's ISO9660 layer only contains `README.TXT`, so `_usable_tree()` requires more than one file. Linux hybrid ISOs fall back to `-tiso`. Expect a harmless `[BOOT]` directory.

**Backend**
- **There are no migrations.** `init_db()` runs `Base.metadata.create_all()` only, so adding or renaming a column won't touch an existing `data/netdispatch.db`. Before changing models after a release, add a migration step: Alembic, or explicit `ALTER TABLE` at startup.
- **SQLAlchemy Python-side `default=` applies only at flush.** A fresh `Relay(...)` has `enabled is None` until committed, which caused a 403 on the first heartbeat. Set fields explicitly when you read them before the commit.
- **Use `create_app(settings)` and `request.app.state.*`** (`settings`, `sessionmaker`, `extractor`, `oauth`, `templates`). Avoid module-level globals: tests build isolated apps with `Settings(_env_file=None, data_dir=tmp)`.
- **Tests use `TestClient(app, base_url="https://testserver")`.** `PUBLIC_URL` is https in tests, so session cookies are `Secure` and would be dropped over `http://`.
- **Use `httpx2`, not `httpx`.** Starlette's TestClient and Authlib deprecate `httpx`, and the test command fails on deprecation warnings.
- **`AuthMiddleware` is pure ASGI on purpose.** `BaseHTTPMiddleware` would buffer multi-GB uploads and downloads. It must sit *inside* `SessionMiddleware`: add it first in `create_app`.
- **Uploads stream `request.stream()` to `<name>.part`** with threadpool writes, then rename. Don't switch to `UploadFile`, which spools to a temp file and doubles disk writes. Use `curl --upload-file -X POST` in scripts, because `--data-binary @file` loads the whole file into RAM.
- **`/files/*` downloads rely on Starlette `FileResponse` Range support.** iPXE `sanboot` over HTTP needs it. Routes are registered for `GET` and `HEAD`.

**Frontend**
- **`index.html` is a Jinja template.** The Alpine component lives inside `{% raw %}…{% endraw %}`, so any `{{ … }}` written in JS (for example unattend sample text) is kept literally. Outside that block, escape it as `{{ '{{ x }}' }}`.
- **Tailwind is compiled at image build** from `app/templates/**/*.html`. Class names must appear literally in templates; build them as complete strings, never by concatenation. There's no runtime CDN, so the hub works offline.
- **Static URLs carry `?v={{ asset_version }}`** (the CSS mtime) to bust browser caches after rebuilds.
- **Tabs follow `location.hash`**, and there's a `hashchange` listener.
- **The generator form is in `templates/unattend/`, included outside the raw block.** Its partials must not contain `{{ … }}` other than the macros' own: Alpine needs no moustaches. Its JS is `static/unattend-generator.js`, which Tailwind does not scan, so it must not build class names.
- **Alpine evaluates expressions inside `x-show` even while hidden.** Anything reading `gen.f` sits inside `<template x-if="gen.f">`.

**Unattend generator**
- **Where it runs.** `netdispatch-unattendgen` runs only on preview and save. `/files/d/{token}/autounattend.xml` serves the saved file and never starts it, so deployments don't depend on the program.
- **The settings format is ours (`HostConfig`), not upstream's `Configuration`.** It is mapped by hand in `ConfigurationMapper`, so an upstream rename is a compile error at the pin bump rather than a silent change to stored settings. The roughly 40 plain on/off settings are the exception: they go by upstream's own names through `HostConfig.Flags` (`UpstreamFlags`, by reflection over the constructor), so new upstream switches work, and show in the form under "More Windows settings", without code changes.
- **Form labels for the on/off settings** are the `genFlagGroups` table in `static/unattend-generator.js`; the group titles are in `templates/unattend/_sec_switches.html`. A flag the program lists that has no label shows under "Other" by its upstream name, so a pin bump can't hide a new one. `test_form_labels_name_real_flags` fails when a label names a flag upstream removed.
- **Upstream settings that aren't on/off get their own `HostConfig` property** with our own value names, mapped in `ConfigurationMapper`: `hiddenFiles` (upstream `HideFiles`) and `taskbarSearch`. A new optional property with a default needs no `schemaVersion` bump: stored settings without it stay valid.
- **Settings that take upstream's own names** (`visualEffects.effects`, `stickyKeys.flags`, a component's `pass`) go through `UpstreamNames`, which matches upstream's enum names exactly and refuses numbers. The schema lists them (`visualEffects`, `stickyKeys`, `components[].passes`), so a name upstream adds works without a change. Their form labels are `genEffectLabels` and `genStickyLabels`; a name without a label shows by its own name.
- **"Leave alone" is null or absent, not a mode,** for the settings that are one optional value: `lockKeys`, `colors`, `desktopIcons`, `startFolders`, `appLockerPolicy`, `lockScreenScript`. The form keeps an on/off box for each (`gen.f.<name>.on`) and `genSettings()` leaves the key out when it is off. `startFolders: []` is not the same as absent: it shows no folders.
- **Catalog ids are matched without regard to case by the generator, exactly by the form.** `genForm()` rewrites stored ids (apps, desktop icons, Start folders, components) to the spelling the schema lists, so settings made through the API with another capitalisation reopen with the same selection. Upstream's own names (`UpstreamNames`) are exact on both sides.
- **The form can't show a third state.** The generator leaves alone a visual effect or desktop icon that custom settings don't list, but the form sends every one. Settings made through the API with a partial list reopen with the missing effects ticked and the missing icons as Windows has them (only the Recycle Bin shown).
- **Upstream's own checks are not always enough.** It reads the Start pins JSON token by token, so `{` alone passed. `ValidateStartPinsJson` parses it properly and wants an object. Check what upstream really rejects before relying on it for a new free-text setting.
- **Custom components replace upstream's.** A component in `components` empties whatever the library wrote for the same component and pass (for example the computer name in `Microsoft-Windows-Shell-Setup`/`specialize`). The library refuses `windowsPE` components unless Windows Setup runs the PE stage (`setup.mode: Standard`).
- **Duplicate (`POST /api/unattend/{id}/duplicate`) copies the file and the settings row as they are.** It does not run the generator, so it works without the program, and a copy of a template from an older generator is flagged `stale` like its source.
- **Inactive modes are rejected.** The validator rejects any field of a mode that isn't selected, for example `timeZone.id` with `mode: Implicit`. `genSettings()` in the JS sends only the selected modes' fields, while the form keeps the rest.
- **Two upstream build details matter.**
  - The assembly name must stay `UnattendGenerator`: `Bloatware.json` names types by assembly, and resources are embedded.
  - The commit reaches `BuildInfo` through the `SourceRevisionId` that `fetch-upstream.sh` writes into upstream's folder, because the archive has no `.git`.
- **The library writes CRLF.** Templates with CRLF are read with `newline=""` and rendered with `newline_sequence="\r\n"`, so the `.cmd` files embedded in the XML keep CRLF. A raw edit from the browser (LF) to a file that had CRLF is converted back.
- **Jinja escaping.** `to_template()` escapes every `{` that opens a Jinja tag (`{{`, `{%`, `{#`) as `{{ "{" }}`, then swaps the placeholders. Placeholders are at least as long as their longest value, because of the library's length-limited commands. `__ND_HOSTNAME__` is exactly 15 characters, so it passes upstream's computer-name check.
- **`InvariantGlobalization`** is on because `python:3.12-slim` has no ICU. Not trimmed, because Newtonsoft creates types by name.
- **Lock files.** `EnableSingleFileAnalyzer` is off, so `packages.lock.json` doesn't gain an implicit ILLink reference when publishing single-file. The CLI lists both `RuntimeIdentifiers`, and Docker restores without `-r` so the lock file matches.
- **Pin bump:**
  1. Set `UPSTREAM_COMMIT`, `UPSTREAM_DATE` and `UPSTREAM_SHA256` in `server/unattendgen/upstream.lock`. Get the hash with `curl -fsSL https://github.com/cschneegans/unattend-generator/archive/<commit>.tar.gz | sha256sum`.
  2. Run the .NET tests. A golden-file failure means the output changed: rewrite the baselines with `UNATTENDHOST_UPDATE_BASELINES=1`, review `git diff` of `tests/Tests/fixtures/baselines/`, and commit them with the bump.
  3. Run the pytest suite.
  4. If `modifier/Disk.cs` changed, run the QEMU `win new` test with a generated template.

  Templates saved with an older commit keep their file and are flagged `stale` until saved again.
- **Stored settings.** They hold account passwords and a Wi-Fi password in plain text, the same exposure as the XML in `data/configs`. Rows are deleted explicitly with their template.

**Ops / tooling**
- **Shell scripts must be LF** (`.gitattributes`). A CRLF entrypoint fails with a confusing `not found` error.
- **Never `pkill -f` with a pattern that also appears in your own command line**, for example `pkill -f "qemu.*ndtap"` inside an `ssh '…'` command: it kills the shell running it. `tests/e2e/` uses pidfiles.
- **Debian OVMF (edk2 ≥ 2025.02) disables network boot without an RNG device.** `vm.sh` adds `virtio-rng-pci`. Without it, OVMF silently boots the EFI shell.
- **QEMU NIC option ROMs are iPXE.** To test *native* UEFI PXE, keep the default efi ROM: it only provides the SNP driver, and EDK2 does the PXE.
- **Docker's `FORWARD DROP` + `br_netfilter` drops bridged frames between test VMs and the fake router.** `net-up.sh` adds a `DOCKER-USER` accept rule.

## Conventions

- **Python 3.12 and SQLAlchemy 2 typed models.** Times are naive UTC (`models.utcnow()`) and serialised with a trailing `Z` (`models.iso()`).
- **New config is an env var on `Settings`.** Add it to the server's `environment:` in `docker-compose.yml` with a comment (commented out with its default if it's rarely changed), and to README.md if users need it. There is no `.env` file: settings live in the compose files.
- **Pin new dependencies** in `server/requirements*.txt`. Downloaded build assets get a version and a SHA-256 in the Dockerfile.
- **Keep user-visible docs in README.md** and internals here. Update both when behaviour changes. `CLAUDE.md` and `CHATGPT.md` point at this file, so don't copy content into them.
- **Never commit `.env`, `data/`, or `relay/certs/*.pem|*.crt`** (they're git-ignored).
