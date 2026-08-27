# Design: `--sandbox` (kernel-enforced containment for unattended runs)

Tracks #130. The reversibility guarantee cannot rest on a text classifier
understanding shell — that boundary is beatable (opaque subprocesses, TOCTOU
symlink races, `cd`, sockets, `docker`). The classifier is an *explainer* that
decides when to ask a human. For an unattended run there is no human, so the
guarantee must come from a boundary the kernel enforces.

## Mechanism choice

| Option | Contains | Portability | Verdict |
|---|---|---|---|
| Copied working dir (no container) | in-workspace file mutations only | everywhere | not a boundary — `sudo`/absolute-path/network still escape |
| overlayfs + user/mount namespaces | filesystem (Linux) | Linux + userns | strong native Linux tier; fiddly, env-dependent |
| **Container (docker/podman)** | filesystem, network, processes, privileges | anywhere a runtime exists | **the real boundary — chosen for Phase 1** |

## Phase 1 — shipped (`--sandbox`)

`src/opendot/sandbox.py` + the `--sandbox` CLI flag.

- **Opt-in, runtime-gated, fails closed.** Detects docker/podman (podman
  preferred: rootless). If neither is present, `--sandbox` errors — it never
  degrades to a direct run while implying isolation.
- **Copy-in / diff-out.** The (non-ignored) workspace is copied to a temp dir,
  that copy is mounted into the container, the turn runs there, and on success
  the copy is reconciled back to the real workspace. The commit-back is
  snapshotted first via the reversibility engine, so it is itself undoable
  (subject to the snapshot size limit for very large files); on a non-zero
  container exit, nothing is applied. Files that can't be reconciled (a copy/
  delete that raises) are reported, never silently dropped.
- **Least privilege.** Network off by default (`--network none`, opt in with
  `--sandbox-net`); only the API-key env var the model needs is forwarded.
- **Scope.** One-shot (`-p`) only — a headless-container TUI/REPL isn't useful.
  Direct runs are unchanged when `--sandbox` isn't passed.

Validation note: the host-side plumbing (detection, gating, copy-in, commit-back)
is unit-tested with the runtime mocked; the *live in-container execution* is
validated on a docker/podman host.

Open follow-ups for Phase 1 hardening:
- MCP servers across the boundary (stdio easy; remote/OAuth harder).
- The default image (`python:3.12-slim`) assumes opendot is installed in it;
  document/provide an image or an entrypoint that installs it.
- Bind-mount vs copy-in tradeoff for very large repos (copy cost).

## Phase 2 — optional

An overlayfs tier for Linux users without a container runtime, and open-time
`openat2(RESOLVE_BENEATH)` for the built-in file tools even in direct runs
(already shipped for writes in 0.3.2). These narrow the direct-run gap but the
container remains the guarantee.

## Honesty contract

- `--sandbox` fails closed; it never claims isolation it didn't establish.
- Docs state which boundary each mode provides (direct = classifier/heuristic;
  `--sandbox` = kernel/container), and what each does not.
