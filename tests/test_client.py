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
