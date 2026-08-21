"""Permission policy: --yes auto-approve, allow/deny patterns, OPENDOT.md block."""

from __future__ import annotations

from opendot.agent.permissions import Policy, load_policy, parse_policy_text


def test_default_policy_asks():
    p = Policy()
    assert p.decide("This command may not be undoable: git push") == "ask"


def test_auto_approve_allows_anything_not_denied():
    p = Policy(auto_approve=True)
    assert p.decide("run rm -rf build") == "allow"


def test_deny_beats_auto_approve():
    p = Policy(deny=["git push"], auto_approve=True)
    assert p.decide("This command escapes the workspace: git push origin") == "deny"
    assert p.decide("run pytest") == "allow"  # not denied, auto-approved


def test_allow_pattern_without_auto_approve():
    p = Policy(allow=["pytest"])
    assert p.decide("run: pytest -q") == "allow"
    assert p.decide("run: git push") == "ask"  # not matched, falls back to ask


def test_deny_beats_allow():
    p = Policy(allow=["git"], deny=["git push"])
    assert p.decide("git push origin main") == "deny"
    assert p.decide("git status") == "allow"


def test_matching_is_case_insensitive():
    p = Policy(deny=["GIT PUSH"])
    assert p.decide("running git push now") == "deny"


def test_word_boundary_no_substring_false_match():
    # #129: 'rm' must not match inside 'reformat'; 'test' must not match 'latest'.
    assert Policy(deny=["rm"]).decide("this will reformat the disk") == "ask"
    assert Policy(deny=["rm"]).decide("run: rm -rf build") == "deny"
    assert Policy(allow=["test"]).decide("checkout latest") == "ask"
    assert Policy(allow=["test"]).decide("run test suite") == "allow"


def test_glob_patterns():
    # A pattern with glob metacharacters matches as an fnmatch glob against the
    # whole prompt (or a whole line of it). Use leading/trailing * to match
    # regardless of surrounding context.
    assert Policy(deny=["git push*"]).decide("git push origin main") == "deny"
    assert Policy(deny=["git push*"]).decide("git status") == "ask"
    assert Policy(deny=["*.env"]).decide("write to config/.env") == "deny"
    assert Policy(allow=["*rm -rf*"]).decide("run: rm -rf build") == "allow"


def test_make_confirm_wraps_ask():
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return True

    confirm = Policy(deny=["push"], allow=["pytest"]).make_confirm(ask)
    assert confirm("git push") is False  # denied, ask never called
    assert confirm("run pytest") is True  # allowed, ask never called
    assert asked == []
    assert confirm("delete something") is True  # falls through to ask
    assert asked == ["delete something"]


def test_merged_with_unions_and_ors():
    a = Policy(allow=["a"], deny=["x"], auto_approve=False)
    b = Policy(allow=["b"], deny=["y"], auto_approve=True)
    m = a.merged_with(b)
    assert set(m.allow) == {"a", "b"}
    assert set(m.deny) == {"x", "y"}
    assert m.auto_approve is True


def test_parse_policy_block():
    text = """
    # notes
    ```opendot
    allow: pytest, ruff
    deny: git push, rm -rf
    skip: data
    ```
    """
    p = parse_policy_text(text)
    assert p.allow == ["pytest", "ruff"]
    assert p.deny == ["git push", "rm -rf"]


def test_load_policy_missing_file(tmp_path):
    p = load_policy(str(tmp_path))
    assert p.allow == [] and p.deny == [] and p.auto_approve is False


def test_load_policy_from_opendot_md(tmp_path):
    (tmp_path / "OPENDOT.md").write_text(
        "guidance here\n\n```opendot\nallow: pytest\ndeny: git push\n```\n",
        encoding="utf-8",
    )
    p = load_policy(str(tmp_path))
    assert p.allow == ["pytest"]
    assert p.deny == ["git push"]
