"""The TUI sidebar's reversibility cursor, including when it sits above the
visible 16-row window (regression: it was mislabeled "all undone")."""

from __future__ import annotations

from opendot.agent.config import AgentConfig
from opendot.agent.loop import Agent
from opendot.reversibility import ledger, redo
from opendot.reversibility.ledger import LedgerEntry
from opendot.reversibility.snapshots import project_id_for
from opendot.tui.sidebar import Sidebar


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


def _render(workdir: str) -> str:
    agent = Agent(AgentConfig(model="m", workdir=workdir))
    return Sidebar(agent).render().plain


def test_cursor_at_head_no_all_undone_label(tmp_path):
    _seed(str(tmp_path), 3)
    out = _render(str(tmp_path))
    assert "here" in out
    assert "all undone" not in out


def test_all_undone_only_when_cursor_at_zero(tmp_path):
    pid = _seed(str(tmp_path), 3)
    state = redo.read(pid, 3)
    state.undone = 3  # everything reverted
    state.head_snapshot = "h"
    redo.write(pid, state)
    out = _render(str(tmp_path))
    assert "all undone" in out


def test_cursor_above_window_is_not_all_undone(tmp_path):
    # 20 actions with 18 undone -> cursor_after == 2, which is above the 16-row
    # window (base == 4). The cursor must render up top WITHOUT "(all undone)",
    # because actions 0 and 1 are still applied.
    pid = _seed(str(tmp_path), 20)
    state = redo.read(pid, 20)
    state.undone = 18
    state.head_snapshot = "h"
    redo.write(pid, state)
    out = _render(str(tmp_path))
    assert "here" in out
    assert "all undone" not in out
