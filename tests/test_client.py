import json
import subprocess

import pytest

from app.ai import client


def test_extract_json_plain():
    assert client.extract_json('[{"a": 1}]') == [{"a": 1}]

def test_extract_json_with_fences_and_prose():
    text = 'Sure! Here it is:\n```json\n[{"a": "br]acket"}]\n```\nDone.'
    assert client.extract_json(text) == [{"a": "br]acket"}]

def test_extract_json_object():
    assert client.extract_json('noise {"x": 2} noise', opener="{") == {"x": 2}

def test_extract_json_missing_raises():
    with pytest.raises(ValueError):
        client.extract_json("no json here")

def test_cli_transport_parses_result(monkeypatch):
    monkeypatch.setattr(client.shutil, "which", lambda _: r"C:\fake\claude.exe")
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"result": "hello"}), stderr="")
    monkeypatch.setattr(client.subprocess, "run", fake_run)
    assert client._via_cli("hi", None, 5) == "hello"

def test_cli_transport_missing_exe_raises(monkeypatch):
    monkeypatch.setattr(client.shutil, "which", lambda _: None)
    with pytest.raises(client.AIUnavailable):
        client._via_cli("hi", None, 5)


def test_via_cli_gives_model_no_tools(monkeypatch):
    # The advisor answers from FACTS + general knowledge only; it must NEVER be
    # handed tools. With tools available + --max-turns 1, an internet-ish question
    # makes the model burn its one turn on a WebSearch call and die on
    # error_max_turns. `--tools ""` empties the base toolset so it can't.
    monkeypatch.setattr(client.shutil, "which", lambda _: r"C:\fake\claude.exe")
    seen = {}
    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"result": "ok"}), stderr="")
    monkeypatch.setattr(client.subprocess, "run", fake_run)
    assert client._via_cli("hi", None, 5) == "ok"
    cmd = seen["cmd"]
    assert "--tools" in cmd, "CLI fallback must pass --tools to disable tools"
    assert cmd[cmd.index("--tools") + 1] == "", "--tools value must be empty"


def test_via_sdk_gives_model_no_tools(monkeypatch):
    # Same contract for the primary (Agent SDK) transport: pass tools=[] so the
    # CLI it drives gets `--tools ""`. allowed_tools=[] is a silent no-op (an
    # empty allow-list is falsy, so the SDK never emits --allowedTools and the
    # full default toolset, incl. WebSearch, stays live).
    import claude_agent_sdk as sdk
    captured = {}
    class FakeOpts:
        def __init__(self, **kw):
            captured.update(kw)
    class FakeResult:
        is_error = False
        result = "ok"
    async def fake_query(prompt, options):
        yield FakeResult()
    monkeypatch.setattr(sdk, "ClaudeAgentOptions", FakeOpts)
    monkeypatch.setattr(sdk, "query", fake_query)
    assert client._via_sdk("hi", "sys", 5) == "ok"
    assert captured.get("tools") == [], "SDK must give the model an empty toolset"
    assert captured.get("max_turns") == 1

def test_cli_transport_launch_oserror_raises_unavailable(monkeypatch):
    # the exe shutil.which found can still fail to launch (OSError); that must
    # surface as AIUnavailable so the advisor's graceful-offline path catches it
    monkeypatch.setattr(client.shutil, "which", lambda _: r"C:\fake\claude.exe")
    def boom(*a, **k):
        raise OSError("exec format error")
    monkeypatch.setattr(client.subprocess, "run", boom)
    with pytest.raises(client.AIUnavailable):
        client._via_cli("hi", None, 5)


def test_ask_claude_falls_back_to_cli(monkeypatch):
    monkeypatch.setattr(client, "_via_sdk",
                        lambda *a: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(client, "_via_cli", lambda *a: "from-cli")
    assert client.ask_claude("hi") == "from-cli"

def test_ask_claude_raises_when_both_fail(monkeypatch):
    monkeypatch.setattr(client, "_via_sdk",
                        lambda *a: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(client, "_via_cli",
                        lambda *a: (_ for _ in ()).throw(client.AIUnavailable("x")))
    with pytest.raises(client.AIUnavailable):
        client.ask_claude("hi")

def test_ask_claude_error_carries_both_reasons(monkeypatch):
    monkeypatch.setattr(client, "_via_sdk",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(client, "_via_cli",
                        lambda *a: (_ for _ in ()).throw(client.AIUnavailable("nope")))
    with pytest.raises(client.AIUnavailable) as ei:
        client.ask_claude("hi")
    assert "boom" in str(ei.value) and "nope" in str(ei.value)

def test_ask_claude_logs_when_both_fail(monkeypatch, caplog):
    import logging
    monkeypatch.setattr(client, "_via_sdk",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(client, "_via_cli",
                        lambda *a: (_ for _ in ()).throw(client.AIUnavailable("nope")))
    with caplog.at_level(logging.WARNING, logger="moneypilot.ai"):
        with pytest.raises(client.AIUnavailable):
            client.ask_claude("hi")
    assert any(r.name == "moneypilot.ai" for r in caplog.records)
