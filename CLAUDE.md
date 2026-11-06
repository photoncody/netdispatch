# CLAUDE.md

The project brief for agents lives in **AGENTS.md** and is imported below. It covers architecture, build and test commands, core invariants, and gotchas. Edit AGENTS.md, not this file, so the two never drift apart.

@AGENTS.md

## Claude Code specifics

- Run the test suite after any change under `server/`:
  ```bash
  docker build --target test -t netdispatch-test server/ && docker run --rm netdispatch-test
  ```
- Changes to the iPXE boot chain must be made together in three files (see invariant 2 in AGENTS.md):
  - `server/app/pxe_engine.py`
  - `relay/entrypoint.sh`
  - `relay/ipxe/embed.ipxe`

  Verify them with the QEMU harness in `tests/e2e/`. Unit tests can't catch firmware behaviour.
- Pass long commit messages and PR bodies through a file (`git commit -F`, `gh pr edit --body-file`). Apostrophes inside a quoted `ssh '…'` command break the quoting.
