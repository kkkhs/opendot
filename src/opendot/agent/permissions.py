"""Permission policy for confirming (or auto-approving) risky actions.

opendot gates every irreversible / workspace-escaping action behind a single
``confirm(prompt) -> bool`` callback. This module turns that gate into a small,
configurable policy so a run can be driven unattended and a project can pin its
own rules:

- ``--yes`` auto-approves what would otherwise be an interactive prompt.
- ``--allow PATTERN`` / ``--deny PATTERN`` match the action's confirm prompt
  (which contains the command or path) on word boundaries, or as an ``fnmatch``
  glob when the pattern contains ``*``/``?``/``[`` (e.g. ``git push*``). ``deny``
  always wins, so you can auto-approve broadly with ``--yes`` yet still hard-block
  specifics.
- An ``OPENDOT.md`` ``opendot`` block carries the same allow/deny lists for the
  project, so the rules travel with the repo.

Precedence, most-specific first: deny > allow > (--yes ? approve : ask). A denied
action is refused without prompting; an allowed one runs without prompting;
anything else falls back to the interactive prompt (or is auto-denied in a
non-interactive run with no ``--yes``).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

_BLOCK_RE = re.compile(r"```opendot\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _split_list(value: str) -> list[str]:
    # Split on commas only — a pattern may contain spaces (e.g. "git push"), so
    # whitespace must not be a separator.
    return [p.strip() for p in value.split(",") if p.strip()]


@dataclass
class Policy:
    """Allow/deny patterns plus an auto-approve flag.

    Patterns match case-insensitively against the confirm prompt (which embeds
    the command or path). Two forms, so a pattern can't accidentally match a
    fragment of a longer word (``rm`` must not match "refo**rm**at"):

    - A pattern with glob metacharacters (``*``, ``?``, ``[``) is matched as an
      ``fnmatch`` glob against the whole prompt (or a whole line of it), e.g.
      ``git push*`` or ``*.env``. Wrap it in ``*...*`` to match regardless of
      surrounding context (``*rm -rf*``).
    - Any other pattern is matched on **word boundaries**, so ``rm`` matches the
      ``rm`` token but not "reformat", and ``git push`` matches that sequence.

    ``deny`` is checked before ``allow``.
    """

    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    auto_approve: bool = False

    @staticmethod
    def _pattern_matches(pattern: str, prompt: str) -> bool:
        low = prompt.lower()
        pat = pattern.lower()
        if any(ch in pat for ch in "*?["):
            # Glob: match against the whole prompt, and also against each line so
            # a leading/trailing anchor isn't needed for a prompt with context.
            return fnmatch.fnmatch(low, pat) or any(
                fnmatch.fnmatch(line, pat) for line in low.splitlines()
            )
        # Word-boundary substring: the pattern must appear as whole token(s).
        return re.search(rf"(?<!\w){re.escape(pat)}(?!\w)", low) is not None

    def _matches(self, patterns: list[str], prompt: str) -> bool:
        return any(self._pattern_matches(p, prompt) for p in patterns)

    def decide(self, prompt: str) -> str:
        """Return 'deny', 'allow', or 'ask' for an action's confirm prompt."""
        if self._matches(self.deny, prompt):
            return "deny"
        if self._matches(self.allow, prompt):
            return "allow"
        return "allow" if self.auto_approve else "ask"

    def merged_with(self, other: "Policy") -> "Policy":
        """Combine two policies (e.g. OPENDOT.md rules + CLI flags). Allow/deny
        lists union; auto_approve is OR."""
        return Policy(
            allow=[*self.allow, *other.allow],
            deny=[*self.deny, *other.deny],
            auto_approve=self.auto_approve or other.auto_approve,
        )

    def make_confirm(self, ask):
        """Wrap an interactive ``ask(prompt) -> bool`` with this policy. Returns a
        ``confirm(prompt) -> bool`` suitable for the Toolbox."""

        def confirm(prompt: str) -> bool:
            decision = self.decide(prompt)
            if decision == "deny":
                return False
            if decision == "allow":
                return True
            return ask(prompt)

        return confirm


def parse_policy_text(text: str) -> Policy:
    """Read allow:/deny: lists from an OPENDOT.md ``opendot`` block."""
    policy = Policy()
    m = _BLOCK_RE.search(text)
    if not m:
        return policy
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        items = _split_list(value)
        if key == "allow":
            policy.allow.extend(items)
        elif key == "deny":
            policy.deny.extend(items)
    return policy


def load_policy(workdir: str | Path) -> Policy:
    """Load the project policy from OPENDOT.md, or an empty policy if absent."""
    p = Path(workdir) / "OPENDOT.md"
    if not p.exists():
        return Policy()
    try:
        return parse_policy_text(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - never let a bad file break the agent
        return Policy()
