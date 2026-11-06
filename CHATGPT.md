# CHATGPT.md

**Read [AGENTS.md](AGENTS.md) first.** It is the single source of truth for working on netdispatch. It covers:
- architecture and the PXE boot chain
- build and test commands
- core invariants
- known gotchas

OpenAI Codex picks it up automatically. If you are ChatGPT working from this file or a pasted copy, load AGENTS.md before making changes.

The five things most likely to cause damage:

1. **`/boot` must never re-serve a payload to a device in `booting` state.** That device just rebooted, and serving the payload again would reinstall it in an endless loop.
2. **Local boot must exit iPXE with a *failure* status** (`nd-local` → `exit 1`), or UEFI firmware stops in its setup menu instead of booting the disk. This logic spans `server/app/pxe_engine.py`, `relay/entrypoint.sh` and `relay/ipxe/embed.ipxe`.
3. **Pass any user-supplied text through `ipxe_text()`** before it goes into an iPXE script. **Build URLs only from `Settings.boot_url`**, never from request headers.
4. **Secrets** (unattend XML, SMB credentials) are only served through per-deployment token URLs under `/files/d/{token}/`.
5. **There are no database migrations yet.** Changing a model won't alter an existing `netdispatch.db`.

Run the tests with:

```bash
docker build --target test -t netdispatch-test server/ && docker run --rm netdispatch-test
```

Keep end-user documentation in README.md. Keep internals in AGENTS.md, not here.
