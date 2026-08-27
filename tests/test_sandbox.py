"""Tests for the --sandbox container isolation scaffold (#130 Phase 1).

The live in-container run is validated on a docker/podman host; these cover the
host-side plumbing — runtime detection/fail-closed, the run command, and
copy-in/diff-out commit-back — with the runtime mocked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opendot import sandbox
from opendot.reversibility.snapshots import IgnoreRules

# -- runtime detection / fail-closed --


def test_require_runtime_fails_closed_when_none(monkeypatch):
    monkeypatch.setattr(sandbox, "detect_runtime", lambda: None)
    with pytest.raises(sandbox.SandboxError, match="container runtime"):
        sandbox.require_runtime()


def test_require_runtime_returns_detected(monkeypatch):
    monkeypatch.setattr(sandbox, "detect_runtime", lambda: "podman")
    assert sandbox.require_runtime() == "podman"


def test_detect_runtime_prefers_podman(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/" + name)
    assert sandbox.detect_runtime() == "podman"  # first in preference order


# -- run command shape --


def test_build_run_command_network_off_and_scoped_env():
    argv = sandbox.build_run_command(
        "docker",
        "img",
        Path("/tmp/sbx"),
        "do it",
        "gpt-5.1",
        network=False,
        env_keys=["OPENAI_API_KEY"],
    )
    assert argv[:2] == ["docker", "run"]
    assert "--network" in argv and "none" in argv  # isolated by default
    assert "-e" in argv and "OPENAI_API_KEY" in argv  # only the named key
    # runs opendot one-shot, auto-approve (already isolated)
    assert argv[-5:] == ["opendot", "-p", "do it", "--model", "gpt-5.1"] or "--yes" in argv
    assert "--yes" in argv


def test_build_run_command_network_on_when_allowed():
    argv = sandbox.build_run_command(
        "podman", "img", Path("/tmp/sbx"), "x", "m", network=True, env_keys=[]
    )
    assert "--network" not in argv  # network allowed -> no isolation flag


# -- copy-in / commit-back --


def _fake_runner_editing(edits):
    """A runner that applies `edits(sandbox_dir)` and returns rc=0."""

    def run(argv):
        sbx = Path(argv[argv.index("-v") + 1].split(":")[0])
        edits(sbx)

        class P:
            returncode = 0

        return P()

    return run


def test_run_sandboxed_commits_changes_back(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "detect_runtime", lambda: "docker")
    wd = tmp_path / "ws"
    wd.mkdir()
    (wd / "keep.txt").write_text("original")
    (wd / "gone.txt").write_text("bye")

    def edits(sbx: Path):
        (sbx / "keep.txt").write_text("EDITED")  # modify
        (sbx / "new.txt").write_text("created")  # create
        (sbx / "gone.txt").unlink()  # delete

    res = sandbox.run_sandboxed(
        str(wd), "go", "gpt-5.1", image="img", runner=_fake_runner_editing(edits)
    )
    assert res["returncode"] == 0
    assert set(res["changed"]) == {"keep.txt", "new.txt", "gone.txt"}
    assert (wd / "keep.txt").read_text() == "EDITED"
    assert (wd / "new.txt").read_text() == "created"
    assert not (wd / "gone.txt").exists()


def test_run_sandboxed_no_commit_on_container_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "detect_runtime", lambda: "docker")
    wd = tmp_path / "ws"
    wd.mkdir()
    (wd / "keep.txt").write_text("original")

    def run(argv):
        sbx = Path(argv[argv.index("-v") + 1].split(":")[0])
        (sbx / "keep.txt").write_text("half-done")  # container messed with it then failed

        class P:
            returncode = 1

        return P()

    res = sandbox.run_sandboxed(str(wd), "x", "gpt-5.1", image="img", runner=run)
    assert res["returncode"] == 1
    assert res["changed"] == []
    assert (wd / "keep.txt").read_text() == "original"  # workspace untouched on failure


def test_run_sandboxed_fails_closed_without_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "detect_runtime", lambda: None)
    with pytest.raises(sandbox.SandboxError):
        sandbox.run_sandboxed(str(tmp_path), "x", "m", image="img", runner=lambda a: None)


def test_commit_back_leaves_ignored_trees_untouched(tmp_path):
    # A change under an ignored tree in the sandbox must not be copied back.
    wd = tmp_path / "ws"
    wd.mkdir()
    sbx = tmp_path / "sbx"
    (sbx / ".git").mkdir(parents=True)
    (sbx / ".git" / "config").write_text("sneaky")
    (sbx / "real.txt").write_text("ok")

    changed = sandbox.commit_back(sbx, wd, IgnoreRules())
    assert "real.txt" in changed
    assert not (wd / ".git").exists()  # ignored tree not committed back
