"""Filesystem tool tests — covers the unit fs functions and their
registration as agent tools.

Regression target: prior to v4.0.8, ``default_tools`` exposed only
``run_bash``, forcing the model to write source files via shell
heredocs. Quoting failures corrupted the file or silently produced
nothing. These tests guarantee write_file/read_file/list_dir/edit_file
are first-class agent tools, schema-validated, and behaviour-correct.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phantom.agent import (
    AgentSession,
    ProviderResponse,
    ScriptedProvider,
    ToolCall,
    default_tools,
)
from phantom.tools.fs import edit_file, list_dir, read_file, write_file


# ─── Unit tests for fs.py ────────────────────────────────────────────────


def test_write_file_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "hello.py"
    out = write_file(
        path=str(target),
        text="print('hi')\n",
        allowlist=(str(tmp_path),),
    )
    assert out["ok"] is True
    assert target.read_text() == "print('hi')\n"
    assert out["bytes_written"] == len("print('hi')\n")


def test_write_file_outside_allowlist_rejected(tmp_path: Path) -> None:
    out = write_file(
        path="/etc/passwd_phantom_test",
        text="x",
        allowlist=(str(tmp_path),),
    )
    assert out["ok"] is False
    assert "allowlist" in out["error"].lower()


def test_write_file_handles_unicode_and_special_chars(tmp_path: Path) -> None:
    """The original heredoc bug ate quotes/backticks. write_file must not."""
    target = tmp_path / "tricky.py"
    payload = (
        "name = \"O'Brien\"\n"
        "shell = `echo $HOME`  # backticks\n"
        "emoji = '🚀'\n"
        "multi = \"\"\"line1\nline2\"\"\"\n"
    )
    out = write_file(path=str(target), text=payload,
                     allowlist=(str(tmp_path),))
    assert out["ok"] is True
    assert target.read_text(encoding="utf-8") == payload


def test_read_file_returns_text(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    target.write_text("alpha\nbeta\n")
    out = read_file(path=str(target), allowlist=(str(tmp_path),))
    assert out["ok"] is True
    assert out["text"] == "alpha\nbeta\n"


def test_read_file_missing_returns_error(tmp_path: Path) -> None:
    out = read_file(
        path=str(tmp_path / "nope.txt"),
        allowlist=(str(tmp_path),),
    )
    assert out["ok"] is False
    assert "not found" in out["error"]


def test_list_dir_returns_entries(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "sub").mkdir()
    out = list_dir(path=str(tmp_path), allowlist=(str(tmp_path),))
    assert out["ok"] is True
    names = {e["name"]: e["kind"] for e in out["entries"]}
    assert names == {"a.txt": "file", "sub": "dir"}


# ─── edit_file unit tests ────────────────────────────────────────────────


def test_edit_file_replaces_unique_string(tmp_path: Path) -> None:
    target = tmp_path / "f.py"
    target.write_text("x = 1\ny = 2\n")
    out = edit_file(
        path=str(target),
        old_string="y = 2",
        new_string="y = 99",
        allowlist=(str(tmp_path),),
    )
    assert out["ok"] is True
    assert out["replacements"] == 1
    assert target.read_text() == "x = 1\ny = 99\n"


def test_edit_file_rejects_non_unique_without_replace_all(tmp_path: Path) -> None:
    target = tmp_path / "f.py"
    target.write_text("a\na\na\n")
    out = edit_file(
        path=str(target),
        old_string="a",
        new_string="b",
        allowlist=(str(tmp_path),),
    )
    assert out["ok"] is False
    assert "not unique" in out["error"]
    assert target.read_text() == "a\na\na\n"  # unchanged


def test_edit_file_replace_all(tmp_path: Path) -> None:
    target = tmp_path / "f.py"
    target.write_text("a\na\na\n")
    out = edit_file(
        path=str(target),
        old_string="a",
        new_string="b",
        allowlist=(str(tmp_path),),
        replace_all=True,
    )
    assert out["ok"] is True
    assert out["replacements"] == 3
    assert target.read_text() == "b\nb\nb\n"


def test_edit_file_old_string_not_found(tmp_path: Path) -> None:
    target = tmp_path / "f.py"
    target.write_text("hello\n")
    out = edit_file(
        path=str(target),
        old_string="goodbye",
        new_string="world",
        allowlist=(str(tmp_path),),
    )
    assert out["ok"] is False
    assert "not found" in out["error"]


def test_edit_file_identical_strings_rejected(tmp_path: Path) -> None:
    target = tmp_path / "f.py"
    target.write_text("hello\n")
    out = edit_file(
        path=str(target),
        old_string="hello",
        new_string="hello",
        allowlist=(str(tmp_path),),
    )
    assert out["ok"] is False
    assert "identical" in out["error"]


def test_edit_file_outside_allowlist_rejected(tmp_path: Path) -> None:
    out = edit_file(
        path="/etc/hosts",
        old_string="x",
        new_string="y",
        allowlist=(str(tmp_path),),
    )
    assert out["ok"] is False
    assert "allowlist" in out["error"].lower()


def test_edit_file_missing_file(tmp_path: Path) -> None:
    out = edit_file(
        path=str(tmp_path / "ghost.py"),
        old_string="x",
        new_string="y",
        allowlist=(str(tmp_path),),
    )
    assert out["ok"] is False
    assert "not found" in out["error"]


# ─── default_tools registration ──────────────────────────────────────────


def test_default_tools_registers_fs_tools(tmp_path: Path) -> None:
    """Regression: prior to v4.0.8 only run_bash was registered."""
    tools = default_tools(workdir=str(tmp_path))
    names = {t.name for t in tools}
    assert {"run_bash", "write_file", "read_file",
            "edit_file", "list_dir"}.issubset(names)


def test_default_tools_schemas_are_valid_json_schema(tmp_path: Path) -> None:
    tools = default_tools(workdir=str(tmp_path))
    for t in tools:
        schema = t.input_schema
        assert schema["type"] == "object"
        assert "properties" in schema
        # Round-trip through provider dict to catch shape errors
        provider_dict = t.to_provider_dict()
        assert provider_dict["type"] == "function"
        assert provider_dict["function"]["name"] == t.name
        assert "parameters" in provider_dict["function"]


def test_default_tools_write_handler_creates_file(tmp_path: Path) -> None:
    tools = default_tools(workdir=str(tmp_path))
    write_tool = next(t for t in tools if t.name == "write_file")
    result_json = write_tool.handler({
        "path": str(tmp_path / "agent_made.py"),
        "text": "def main():\n    print('hello from agent')\n",
    })
    result = json.loads(result_json)
    assert result["ok"] is True
    assert (tmp_path / "agent_made.py").read_text() == (
        "def main():\n    print('hello from agent')\n"
    )


def test_default_tools_write_handler_rejects_bad_args(tmp_path: Path) -> None:
    """Bad args used to raise PhantomError, killing the agent's turn.
    Since v1.1.10 the handler returns a JSON error blob with a `hint` field
    so the model can recover and retry on the next round."""
    tools = default_tools(workdir=str(tmp_path))
    write_tool = next(t for t in tools if t.name == "write_file")
    result = write_tool.handler({"path": "", "text": "x"})
    parsed = json.loads(result)
    assert "error" in parsed
    assert "hint" in parsed


def test_default_tools_read_handler_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "data.txt"
    target.write_text("payload\n")
    tools = default_tools(workdir=str(tmp_path))
    read_tool = next(t for t in tools if t.name == "read_file")
    result = json.loads(read_tool.handler({"path": str(target)}))
    assert result["ok"] is True
    assert result["text"] == "payload\n"


def test_default_tools_edit_handler_works(tmp_path: Path) -> None:
    target = tmp_path / "e.py"
    target.write_text("answer = 41\n")
    tools = default_tools(workdir=str(tmp_path))
    edit_tool = next(t for t in tools if t.name == "edit_file")
    result = json.loads(edit_tool.handler({
        "path": str(target),
        "old_string": "answer = 41",
        "new_string": "answer = 42",
    }))
    assert result["ok"] is True
    assert target.read_text() == "answer = 42\n"


def test_default_tools_list_dir_handler(tmp_path: Path) -> None:
    (tmp_path / "f1").write_text("a")
    (tmp_path / "d1").mkdir()
    tools = default_tools(workdir=str(tmp_path))
    list_tool = next(t for t in tools if t.name == "list_dir")
    result = json.loads(list_tool.handler({"path": str(tmp_path)}))
    assert result["ok"] is True
    assert {e["name"] for e in result["entries"]} == {"f1", "d1"}


def test_default_tools_extra_writable_paths_extends_allowlist(
    tmp_path: Path,
) -> None:
    extra = tmp_path / "outside"
    extra.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    tools = default_tools(
        workdir=str(workdir),
        extra_writable_paths=(str(extra),),
    )
    write_tool = next(t for t in tools if t.name == "write_file")
    result = json.loads(write_tool.handler({
        "path": str(extra / "ok.txt"),
        "text": "yes",
    }))
    assert result["ok"] is True
    assert (extra / "ok.txt").read_text() == "yes"


def test_default_tools_blocks_writes_outside_allowlist(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    tools = default_tools(workdir=str(workdir))
    write_tool = next(t for t in tools if t.name == "write_file")
    result = json.loads(write_tool.handler({
        "path": str(other / "evil.txt"),
        "text": "no",
    }))
    assert result["ok"] is False
    assert "allowlist" in result["error"].lower()


# ─── End-to-end: simulated model uses write_file then read_file ──────────


def test_agent_session_uses_write_file_tool_end_to_end(tmp_path: Path) -> None:
    """Simulate a model that calls write_file then summarises.

    This is the regression scenario: the user said "create a python
    file" and the agent failed because write_file wasn't a tool. After
    the fix, the model can pick it directly and the file lands on disk.
    """
    target = tmp_path / "generated.py"

    provider = ScriptedProvider.from_responses([
        ProviderResponse(
            text="",
            tool_calls=(ToolCall(
                id="call-1",
                name="write_file",
                arguments={
                    "path": str(target),
                    "text": "x = 42\n",
                },
            ),),
            finish_reason="tool_calls",
        ),
        ProviderResponse(text="Wrote generated.py", finish_reason="stop"),
    ])

    session = AgentSession(
        provider=provider,
        tools=default_tools(workdir=str(tmp_path)),
    )
    final = session.respond_to("create a python file with x=42")

    assert target.exists()
    assert target.read_text() == "x = 42\n"
    assert "Wrote" in final


def test_agent_session_write_then_edit_then_read(tmp_path: Path) -> None:
    """Multi-round flow: write → edit → read. Validates round budget
    is enough and tool results feed back correctly."""
    target = tmp_path / "ml.py"

    provider = ScriptedProvider.from_responses([
        ProviderResponse(
            tool_calls=(ToolCall(
                id="t1", name="write_file",
                arguments={"path": str(target),
                           "text": "epochs = 10\nlr = 0.001\n"},
            ),),
            finish_reason="tool_calls",
        ),
        ProviderResponse(
            tool_calls=(ToolCall(
                id="t2", name="edit_file",
                arguments={"path": str(target),
                           "old_string": "epochs = 10",
                           "new_string": "epochs = 50"},
            ),),
            finish_reason="tool_calls",
        ),
        ProviderResponse(
            tool_calls=(ToolCall(
                id="t3", name="read_file",
                arguments={"path": str(target)},
            ),),
            finish_reason="tool_calls",
        ),
        ProviderResponse(text="done", finish_reason="stop"),
    ])

    session = AgentSession(
        provider=provider,
        tools=default_tools(workdir=str(tmp_path)),
    )
    final = session.respond_to("write ml.py with epochs=10, then bump to 50")

    assert target.read_text() == "epochs = 50\nlr = 0.001\n"
    assert final == "done"

    # The third tool result (read_file) should have arrived as a tool
    # message in history with the file's current contents.
    tool_messages = [m for m in session.history if m.role == "tool"]
    assert len(tool_messages) == 3
    last_read = json.loads(tool_messages[-1].content)
    assert last_read["ok"] is True
    assert "epochs = 50" in last_read["text"]


def test_agent_session_default_max_tool_rounds_bumped() -> None:
    """v1.1.12: lowered 25 → 12 because longer turns were almost always
    a model stuck in a loop (14m silent loops on kimi-k2.6). 12 still
    fits typical multi-step coding tasks; the wall-clock budget catches
    the rest."""
    session = AgentSession(provider=ScriptedProvider())
    assert session.max_tool_rounds == 12
