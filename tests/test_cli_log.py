"""`opendot log` timeline rendering: the undo/redo cursor and undone actions."""

from __future__ import annotations

import os

from opendot import cli
from opendot.reversibility import ledger, redo
from opendot.reversibility.ledger import LedgerEntry
from opendot.reversibility.snapshots import project_id_for


def _seed(workdir: str, n: int) -> str:
    pid = project_id_for(workdir)
    for i in range(1, n + 1):
        ledger.append(
            pid,
            LedgerEntry(
                id=f"{i:06d}",
                kind="write",
                detail=f"file{i}.py",
                snapshot_before=f"s{i}",
                reversible=True,
            ),
        )
    return pid


def _log_output(workdir: str) -> str:
    with cli.console.capture() as cap:
        cli._cmd_log(workdir)
    return cap.get()


def test_log_cursor_at_head_when_nothing_undone(tmp_path):
    _seed(str(tmp_path), 3)
    out = _log_output(str(tmp_path))
    # Cursor sits after the last (newest) action, and nothing is marked undone.
    assert "you are here" in out
    assert "undone" not in out
    assert out.index("file3.py") < out.index("you are here")


def test_log_marks_undone_actions_below_cursor(tmp_path):
    pid = _seed(str(tmp_path), 3)
    # Simulate one undo: the newest action is reverted (redoable).
    state = redo.read(pid, 3)
    state.undone = 1
    state.head_snapshot = "h"
    redo.write(pid, state)

    out = _log_output(str(tmp_path))
    assert "↶ undone" in out
    # The cursor is above the undone action, which is the newest one.
    assert out.index("you are here") < out.index("file3.py")
    assert "re-apply the next of 1 undone action(s)" in out


def test_log_everything_undone(tmp_path):
    pid = _seed(str(tmp_path), 2)
    state = redo.read(pid, 2)
    state.undone = 2
    state.head_snapshot = "h"
    redo.write(pid, state)

    out = _log_output(str(tmp_path))
    assert "everything undone" in out


def test_log_empty(tmp_path):
    # A fresh project with no actions.
    os.chdir(tmp_path)
    out = _log_output(str(tmp_path))
    assert "no actions recorded yet" in out
