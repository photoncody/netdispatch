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

Boot chain:
1. Firmware PXE → relay ProxyDHCP → TFTP `ipxe.efi` / `undionly.kpxe`.
2. The **embedded script** runs `dhcp`, then TFTP-fetches `netdispatch.ipxe` from the relay. The relay's entrypoint generates that file at start-up.
3. `netdispatch.ipxe` chains `CENTRAL_SERVER_URL/boot?mac=…&relay=…`.
4. The hub returns either a wait-loop script, a payload script, or a local-boot script.

## Layout

| Path | Contents |
|---|---|
| `server/app/main.py` | `create_app(settings)` factory: middleware order, routers, `asset_version` |
| `server/app/config.py` | `Settings` (pydantic-settings). All config comes from env vars, nothing else. |
| `server/app/models.py` | `Relay`, `Device`, `IsoImage`, `UnattendConfig`, plus state/method constants |
| `server/app/auth.py` | pure-ASGI `AuthMiddleware` (exemptions + CSRF header), OIDC routes, local login, rate limit |
| `server/app/pxe_engine.py` | **all iPXE script text**: wait, local, payload (wimboot/linux/sanboot/memdisk) |
| `server/app/routers/boot.py` | `/boot` state machine |
| `server/app/routers/files.py` | `/files/boot`, `/files/iso/{id}/raw`, `/files/iso/{id}/tree`, `/files/d/{token}` |
| `server/app/iso_tools.py` | 7-Zip extraction (background threads), layout detection, `resolve_ci()` safe paths |
| `server/app/winpe.py` | `winpeshl.ini` + `netdispatch.cmd` (net use + `setup.exe /unattend`) |
| `server/app/unattend.py` | template files in `data/configs`, sandboxed Jinja rendering, XML validation |
| `server/app/templates/` | `index.html` (whole SPA), `login.html` |
| `server/tests/` | pytest (unit + TestClient integration) |
| `relay/entrypoint.sh` | env validation, interface/subnet detection, writes `dnsmasq.conf` + `/tftp/netdispatch.ipxe` |
| `relay/ipxe/embed.ipxe`, `relay/ipxe/general.h` | compiled into the iPXE binaries |
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

The server Dockerfile has three stages: `assets` (Tailwind CLI, Alpine.js, wimboot, each SHA-pinned), `test`, and `runtime`. `runtime` is the last stage and therefore the default target. The relay build compiles iPXE (about 60 s). Any change to `relay/ipxe/*` needs a relay rebuild **and** a restart of running relays.

### CI (GitHub Actions, target under 5 minutes per run)

- **`.github/workflows/ci.yml`**
  - `shellcheck` on every shell script, and `docker compose config` on both compose files.
  - pytest inside the Dockerfile's `test` stage, with the GHA layer cache, run with `-W error::DeprecationWarning`.
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

## Core invariants (don't break these)

1. **`/boot` never re-serves a payload.**
   - The states are `waiting` → `deploying` (dispatched) → `booting` (payload served) → `local` or `waiting`, depending on `after_deploy`.
   - A device in `booting` that hits `/boot` again has rebooted, for example Windows Setup rebooting. Re-serving the payload there would loop the install forever.
   - `result=failed` (sent by the payload script's `:failed` path) returns the device to `waiting`.
2. **Local boot = exit with a failure status, end to end.**
   - UEFI firmware only tries the next boot entry when a boot option *fails*. A successful return drops EDK2 into its setup menu. The original bug was exactly this: it worked on SeaBIOS and broke on OVMF.
   - The mechanism: `render_local()` does `set nd-local 1` and `exit 0`. The relay's `netdispatch.ipxe` and `embed.ipxe` both check `isset ${nd-local}` and `exit 1`. Any other return means "hub unreachable", so they retry.
   - **This logic spans three files** (`pxe_engine.py`, `relay/entrypoint.sh` heredoc, `relay/ipxe/embed.ipxe`). Change them together.
3. **The wait loop uses `chain --replace --autofree`.**
   - Without `--replace`, every poll nests a script and iPXE eventually runs out of memory.
   - `--replace` keeps the call stack, so the replaced script's `exit` still returns to the relay script.
4. **Absolute URLs come only from `Settings.boot_url`** (`BOOT_BASE_URL`, falling back to `PUBLIC_URL`). Never use the request `Host` or `X-Forwarded-Host`. The OIDC redirect URI is always `PUBLIC_URL/auth/oidc/callback`.
5. **Every variable string echoed into an iPXE script goes through `ipxe_text()`.**
   - It strips `$ { } & | # " '` and similar characters. Labels and relay names are user-controlled, and `${…}` would be expanded and `&&`/`||` parsed by iPXE.
   - Relay IDs go through `safe_slug()`.
   - The kernel cmdline is admin-controlled and is intentionally not sanitised.
6. **Secrets are only served under `/files/d/{token}/`.**
   - These are the unattend XML and the WinPE `.cmd` that carries SMB credentials.
   - Each deployment gets a new token. It is valid only while the device is `deploying` or `booting`, and is cleared on reset/local/return.
   - `/files/iso/*` is deliberately unauthenticated (boot clients can't log in), so never put secrets there.
7. **Auth exemptions are an explicit allow-list** in `auth.py` (`EXEMPT_EXACT`, `EXEMPT_PREFIXES`).
   - `/api/relays/heartbeat` is exempt and authenticated by `X-Relay-Key`.
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
- **iPXE EFI looks for `autoexec.ipxe` before running the embedded script.** The "not found" TFTP log lines are harmless. Don't add that file: it would run *instead of* the embedded script, before `dhcp`.
- **dnsmasq:**
  - `pxe-service` basenames that contain a dot are used verbatim, while names without one get `.0` appended.
  - `port=0` disables DNS. Keep it, since relays must not answer DNS.
- **Relays need `network_mode: host` + `NET_ADMIN`.** Docker Desktop can't receive DHCP broadcasts.

**Windows / SMB**
- **Samba must allow execute.** Extracted files are 0644, so the share sets `acl allow execute always = yes`. Without it, WinPE says *Access is denied* on `setup.exe`.
- **Windows 11 24H2+ WinPE refuses guest SMB and requires signing.** That's why the share requires a user and SMB2+.
- **wimboot injects files into `X:\Windows\System32`.**
  - `winpeshl.ini` uses the `[LaunchApps]` / `"netdispatch.cmd"` format.
  - Initrd naming follows the canonical wimboot form: `initrd <url> <name>`.
- **Batch escaping in `netdispatch.cmd`:** `%` becomes `%%`, and double quotes are rejected in `SMB_PASSWORD`. `SMB_USERNAME` is restricted to `[A-Za-z0-9._-]`.
- **WinPE only has inbox drivers.** Test VMs must use **e1000e + NVMe/AHCI**: virtio-net means no network in WinPE, and virtio-blk means Setup sees no disk.
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
