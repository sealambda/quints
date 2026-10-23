"""Agent backends: the write boundary, timeouts, and the SDK wiring.

The Claude SDK itself can't run here (it spawns the CLI and calls the API),
so ``query`` is replaced by a fake; everything up to that call is real.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, HookContext, PreToolUseHookInput

from quints_mailroom import agent as agent_mod
from quints_mailroom.agent import AgentError, ClaudeAgentRunner, CommandAgentRunner, write_denial
from quints_mailroom.config import AgentConfig


@pytest.fixture
def books(tmp_path: Path) -> Path:
    (tmp_path / "staging").mkdir()
    (tmp_path / "inbox").mkdir()
    (tmp_path / "books").mkdir()
    return tmp_path


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Write", {"file_path": "staging/acme.beancount"}),
        ("Edit", {"file_path": "inbox/notes.txt"}),
        ("MultiEdit", {"file_path": "staging/deep/new/file.beancount"}),
        ("NotebookEdit", {"notebook_path": "staging/x.ipynb"}),
        ("Read", {"file_path": "/etc/passwd"}),  # not a write: not this guard's call
        ("Bash", {"command": "quints check"}),
    ],
)
def test_writes_inside_staging_and_inbox_are_allowed(
    books: Path, tool: str, tool_input: dict[str, Any]
) -> None:
    assert write_denial(books, tool, tool_input) is None


def test_absolute_path_inside_staging_is_allowed(books: Path) -> None:
    assert (
        write_denial(books, "Write", {"file_path": str(books / "staging" / "a.beancount")}) is None
    )


@pytest.mark.parametrize(
    "tool_input",
    [
        {"file_path": "books/2026.beancount"},
        {"file_path": "quints.toml"},
        {"file_path": "staging/../books/2026.beancount"},
        {"file_path": "stagingfoo/x"},
        {"file_path": "/etc/passwd"},
        {"file_path": ".claude/settings.json"},
        {"file_path": ""},
        {},
    ],
)
def test_writes_elsewhere_are_denied(books: Path, tool_input: dict[str, Any]) -> None:
    reason = write_denial(books, "Write", tool_input)
    assert reason is not None


def test_symlink_out_of_staging_is_denied(books: Path) -> None:
    (books / "staging" / "escape").symlink_to(books / "books")
    reason = write_denial(books, "Edit", {"file_path": "staging/escape/2026.beancount"})
    assert reason is not None and "staging/ or inbox/" in reason


def test_command_backend_timeout_is_an_agent_error(tmp_path: Path) -> None:
    hang = CommandAgentRunner((sys.executable, "-c", "while True: pass"), timeout=0.2)
    with pytest.raises(AgentError, match=r"timed out after 0\.2s"):
        hang.run("prompt", cwd=tmp_path)


def fake_query(gen: AsyncIterator[object], seen: list[ClaudeAgentOptions]) -> Any:
    def query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[object]:
        seen.append(options)
        return gen

    return query


async def _never() -> AsyncIterator[object]:
    await asyncio.Event().wait()
    yield None  # pragma: no cover — unreachable, makes this an async generator


def test_claude_backend_timeout_is_an_agent_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[ClaudeAgentOptions] = []
    monkeypatch.setattr(agent_mod, "query", fake_query(_never(), seen))
    runner = ClaudeAgentRunner(AgentConfig(timeout_seconds=0.05))
    with pytest.raises(AgentError, match=r"timed out after 0\.05s"):
        runner.run("prompt", cwd=tmp_path)
    assert seen and seen[0].max_turns == agent_mod.MAX_TURNS


def test_claude_backend_hook_denies_writes_outside_staging(books: Path) -> None:
    options = ClaudeAgentRunner(AgentConfig()).options(books)
    assert options.allowed_tools == list(AgentConfig().allowed_tools)
    assert options.hooks is not None
    [matcher] = options.hooks["PreToolUse"]
    assert matcher.matcher is not None and set(matcher.matcher.split("|")) >= {"Edit", "Write"}
    [hook] = matcher.hooks

    def call(path: str) -> dict[str, Any]:
        hook_input: PreToolUseHookInput = {
            "session_id": "s",
            "transcript_path": "t",
            "cwd": str(books),
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x"},
            "tool_use_id": "u1",
        }
        context: HookContext = {"signal": None}

        async def run() -> dict[str, Any]:
            return dict(await hook(hook_input, "u1", context))

        return asyncio.run(run())

    assert call("staging/ok.beancount") == {}
    denied = call("books/2026.beancount")["hookSpecificOutput"]
    assert denied["permissionDecision"] == "deny"
    assert "staging/" in denied["permissionDecisionReason"]
