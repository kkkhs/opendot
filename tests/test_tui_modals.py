"""Regression tests for TUI modal event handling.

The key one: a secret typed into a modal's input (e.g. the Composio API key)
must NEVER bubble up and get submitted as a chat message. This reproduces and
guards the leak where pressing Enter in ApiKeyModal sent the key to the agent.
"""

from opendot.agent.events import Event
from opendot.tui import ApiKeyModal, McpAddModal, OpendotTUI
from opendot.tui.modals import _split_header


class _FakeUsage:
    total_tokens = 0
    cost_usd = 0.0


class _FakeRev:
    def history(self):
        return []


class _FakeConfig:
    model = "gpt-5.1"
    workdir = "/tmp/ws"


class _FakeTB:
    _confirm = None


class _FakeAgent:
    def __init__(self):
        self.usage = _FakeUsage()
        self.reversibility = _FakeRev()
        self.config = _FakeConfig()
        self.toolbox = _FakeTB()
        self.mcp = None
        self.ran = []

    def reset(self):
        pass

    async def run(self, msg):
        self.ran.append(msg)
        yield Event(type="text", text="x")


def test_mcp_header_split_preserves_equals_in_colon_value():
    assert _split_header("Authorization: Bearer abc=def") == (
        "Authorization",
        "Bearer abc=def",
    )
    assert _split_header("X-Api-Key: k==") == ("X-Api-Key", "k==")


def test_mcp_header_split_supports_both_input_forms():
    assert _split_header("Key: Value") == ("Key", "Value")
    assert _split_header("Authorization=Bearer abc=def") == (
        "Authorization",
        "Bearer abc=def",
    )


def test_mcp_header_split_rejects_malformed_headers():
    # An empty key or a value with no delimiter must not produce an invalid
    # {"": ...} / keyless header dict; the caller skips ("", "").
    assert _split_header("=abc") == ("", "")
    assert _split_header(":abc") == ("", "")
    assert _split_header("no-delimiter-here") == ("", "")
    assert _split_header("   ") == ("", "")


async def test_mcp_add_modal_preserves_equals_in_authorization_value(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))
    app = OpendotTUI(_FakeAgent())
    submitted = []

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(McpAddModal(), submitted.append)
        await pilot.pause()
        app.screen.query_one("#name").value = "example"
        app.screen.query_one("#target").value = "https://example.com/mcp"
        app.screen.query_one("#header").value = "Authorization: Bearer abc=def"
        await pilot.press("enter")
        await pilot.pause()

    assert submitted == [
        {
            "name": "example",
            "spec": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer abc=def"},
            },
        }
    ]


async def test_apikey_modal_enter_does_not_leak_to_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))
    app = OpendotTUI(_FakeAgent())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # Open the key modal directly (equivalent to running /provider or the
        # first /composio step) and submit a secret with Enter.
        app.push_screen(ApiKeyModal("Composio", "COMPOSIO_API_KEY"))
        await pilot.pause()
        app.screen.query_one("#key").value = "ak_SECRET"
        await pilot.press("enter")
        for _ in range(4):
            await pilot.pause()
        # The secret must not have been submitted to the agent as a message.
        assert "ak_SECRET" not in app.agent.ran


async def test_slash_autocomplete_enter_runs_command(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))
    app = OpendotTUI(_FakeAgent())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        for ch in ["slash", "c", "l", "e", "a", "r"]:
            await pilot.press(ch)
        await pilot.pause()
        await pilot.press("enter")  # popup open → runs /clear immediately
        await pilot.pause()
        # /clear ran (no chat turn), input cleared, popup closed.
        assert app.agent.ran == []
        assert app.query_one("#input").value == ""
        assert app.query_one("#cmdpopup").display is False
