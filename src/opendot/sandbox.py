"""Container sandbox for unattended runs (``--sandbox``).

The classifier decides *when to ask*; it is not a security boundary (see #130).
For an unattended run there is no human to ask, so ``--sandbox`` moves the
guarantee to a **kernel-enforced** boundary: run the agent inside a container
against a *copy* of the workspace, and commit only the resulting workspace diff
back to the real tree on success. Whatever the agent does — opaque subprocess,
symlink race, ``rm -rf`` — is contained by the container, not by parsing shell.

Design (Phase 1 of #130):

- **Opt-in, runtime-gated, fails closed.** ``--sandbox`` needs docker or podman;
  if neither is present it errors, it never silently falls back to running
  directly (that would imply an isolation the run doesn't have).
- **Copy-in / diff-out.** The workspace is copied to a temp dir, that copy is
  mounted into the container, and after the run the copy is reconciled back to
  the real workspace through the reversibility engine — so the commit-back is
  itself snapshotted and undoable.
- **Only for one-shot runs.** An interactive TUI/REPL inside a headless
  container isn't useful; ``--sandbox`` applies to ``opendot -p`` (and CI).

NOTE: the *live in-container execution* is validated on a docker/podman host; the
host-side plumbing here (runtime detection, gating, copy-in, commit-back) is
unit-tested with the runtime mocked.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from opendot.reversibility.rules import load_rules
from opendot.reversibility.snapshots import IgnoreRules, _iter_files

# Container runtimes we support, in preference order. podman first: rootless by
# default, so the contained process can't trivially act as host root.
_RUNTIMES = ("podman", "docker")


class SandboxError(Exception):
    """Raised when a sandbox run can't be set up (no runtime, copy failure, …).

    ``--sandbox`` fails closed: this is surfaced to the user, never swallowed
    into a silent direct run."""


def detect_runtime() -> str | None:
    """Return the first available container runtime on PATH, or None."""
    for rt in _RUNTIMES:
        if shutil.which(rt):
            return rt
    return None


def require_runtime() -> str:
    """Return an available runtime, or raise SandboxError (fail closed)."""
    rt = detect_runtime()
    if rt is None:
        raise SandboxError(
            "--sandbox needs a container runtime, but neither podman nor docker "
            "was found on PATH. Install one, or drop --sandbox to run directly "
            "(no kernel isolation)."
        )
    return rt


def _copy_workspace(workdir: Path, dest: Path, rules: IgnoreRules) -> None:
    """Copy the (non-ignored) workspace tree into ``dest``.

    Ignored trees (.git, node_modules, venvs, …) are skipped, matching what the
    snapshot engine captures — the sandbox operates on the same view the
    reversibility engine can reconcile."""
    for f in _iter_files(workdir, rules):
        rel = f.relative_to(workdir)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)


def _hash(path: Path) -> str | None:
    import hashlib

    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def commit_back(sandbox_dir: Path, workdir: Path, rules: IgnoreRules) -> list[str]:
    """Reconcile the post-run sandbox copy back into the real workspace.

    Applies files that were created or changed in the sandbox, and removes
    (non-ignored) files the sandbox deleted, so the real workspace ends up
    matching the sandbox's non-ignored view. Ignored trees are never touched.
    Returns the list of relative paths that changed (for the caller to report).

    The caller snapshots the real workspace *before* calling this (via the
    reversibility engine), so the whole commit-back is itself undoable.
    """
    changed: list[str] = []

    sandbox_files = {
        f.relative_to(sandbox_dir).as_posix(): f for f in _iter_files(sandbox_dir, rules)
    }
    real_files = {f.relative_to(workdir).as_posix(): f for f in _iter_files(workdir, rules)}

    # 1. Create / update files present in the sandbox (new, or content changed).
    for rel, src in sandbox_files.items():
        dst = workdir / rel
        if rel not in real_files or _hash(src) != _hash(dst):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            changed.append(rel)

    # 2. Remove files the sandbox deleted (present in real, absent in sandbox).
    for rel, dst in real_files.items():
        if rel not in sandbox_files:
            try:
                dst.unlink()
                changed.append(rel)
            except OSError:
                pass

    return sorted(set(changed))


def build_run_command(
    runtime: str,
    image: str,
    sandbox_dir: Path,
    prompt: str,
    model: str,
    *,
    network: bool,
    env_keys: list[str],
) -> list[str]:
    """Build the ``docker/podman run`` argv that runs opendot one-shot inside the
    container against the mounted sandbox workspace.

    - the sandbox copy is mounted at /work and used as the working dir
    - network is off by default (``--network none``) unless explicitly allowed
    - only the named API-key env vars are forwarded (``-e KEY``), nothing else
    - runs ``opendot -p <prompt> --yes`` inside (already isolated, so auto-approve)
    """
    argv = [runtime, "run", "--rm", "-v", f"{sandbox_dir}:/work", "-w", "/work"]
    if not network:
        argv += ["--network", "none"]
    for key in env_keys:
        argv += ["-e", key]  # forward the value from the host env by name
    argv += [image, "opendot", "-p", prompt, "--model", model, "--yes"]
    return argv


def run_sandboxed(
    workdir: str,
    prompt: str,
    model: str,
    *,
    image: str,
    network: bool = False,
    env_keys: list[str] | None = None,
    runner=subprocess.run,
) -> dict:
    """Run one opendot turn inside a container against a copy of ``workdir``, then
    commit the resulting diff back to the real workspace (snapshotted first).

    Returns ``{"runtime", "changed", "returncode"}``. Raises SandboxError if no
    runtime is available or the workspace can't be staged. ``runner`` is injected
    for testing (defaults to subprocess.run).
    """
    import tempfile

    runtime = require_runtime()
    wd = Path(workdir).resolve()
    rules = load_rules(str(wd))
    env_keys = env_keys or []

    staging = Path(tempfile.mkdtemp(prefix="opendot-sbx-"))
    sandbox_dir = staging / "work"
    sandbox_dir.mkdir()
    try:
        _copy_workspace(wd, sandbox_dir, rules)

        argv = build_run_command(
            runtime, image, sandbox_dir, prompt, model, network=network, env_keys=env_keys
        )
        proc = runner(argv)
        returncode = getattr(proc, "returncode", 0)

        changed: list[str] = []
        if returncode == 0:
            # Snapshot the real workspace first so the commit-back is undoable,
            # then reconcile the sandbox result into it.
            from opendot.reversibility.engine import Reversibility

            rev = Reversibility(workdir=str(wd), rules=rules)
            rev.before_action(
                "write", "sandbox commit", reversible=True, note="sandbox --sandbox run"
            )
            changed = commit_back(sandbox_dir, wd, rules)
        return {"runtime": runtime, "changed": changed, "returncode": returncode}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
