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
| `tests/e2e/` | QEMU end-to-end harness (isolated bridge, fake router DHCP, BIOS/UEFI/Windows VMs) |
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
- `test` and `runtime`. `runtime` is the last stage and therefore the default target.

The generator's .NET tests on their own: `docker build --target unattendgen-test server/`. On a machine with the .NET 10 SDK: `server/unattendgen/fetch-upstream.sh && dotnet test --solution server/unattendgen/Unattendgen.slnx`. The relay build compiles iPXE (about 60 s). Any change to `relay/ipxe/*` needs a relay rebuild **and** a restart of running relays.

### CI (GitHub Actions, target under 5 minutes per run)

- **`.github/workflows/ci.yml`**
  - `shellcheck` on every shell script, and `docker compose config` on both compose files.
  - pytest inside the Dockerfile's `test` stage, with the GHA layer cache, run with `-W error::DeprecationWarning`.
  - The unattend generator's .NET tests: the Dockerfile's `unattendgen-test` stage, which fails the build on a failing test.
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

## Core invariants (don't break these)

1. **`/boot` never re-serves a payload.**
   - The states are `waiting` → `deploying` (dispatched) → `booting` (payload served) → `local` or `waiting`, depending on `after_deploy`.
   - A device in `booting` that hits `/boot` again has rebooted, for example Windows Setup rebooting. Re-serving the payload there would loop the install forever.
   - `result=failed` (sent by the payload script's `:failed` path) returns the device to `waiting`.
2. **Local boot = exit with a failure status, end to end.**
   - UEFI firmware only tries the next boot entry when a boot option *fails*. A successful return drops EDK2 into its setup menu. The original bug was exactly this: it worked on SeaBIOS and broke on OVMF.
   - The mechanism: `render_local()` does `set nd-local 1` and `exit 0`. The relay's `netdispatch.ipxe` and `embed.ipxe` both check `isset ${nd-local}` and `exit 1`. Any other return means "hub unreachable", so they retry.
   - **This logic spans three files** (`pxe_engine.py`, `relay/entrypoint.sh` heredoc, `relay/ipxe/embed.ipxe`). Change them together.
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
- **Stored settings.** They hold account passwords in plain text, the same exposure as the XML in `data/configs`. Rows are deleted explicitly with their template.

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
