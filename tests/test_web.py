# tests/test_web.py
from web import auth


def test_hash_roundtrip():
    rec = auth.hash_password("hunter2")
    assert auth.verify_password("hunter2", rec)
    assert not auth.verify_password("wrong", rec)


def test_hash_uses_random_salt():
    a = auth.hash_password("same")
    b = auth.hash_password("same")
    assert a["salt"] != b["salt"]
    assert a["hash"] != b["hash"]


def test_user_store_add_verify_list_remove(tmp_path):
    store = auth.UserStore(tmp_path / "users.json")
    assert store.list() == []
    store.add("alice", "pw1")
    assert store.exists("alice")
    assert store.verify("alice", "pw1")
    assert not store.verify("alice", "nope")
    assert not store.verify("ghost", "x")        # unknown user, no crash
    assert store.list() == ["alice"]
    store.remove("alice")
    assert not store.exists("alice")


import pytest
from web.registry import Registry


def test_registry_creates_isolated_ledgers(tmp_path):
    reg = Registry(tmp_path)
    a = reg.get_api("alice")
    b = reg.get_api("bob")
    assert a is not b
    assert (tmp_path / "alice" / "ledger.db").exists()
    assert (tmp_path / "bob" / "ledger.db").exists()


def test_registry_caches_same_instance(tmp_path):
    reg = Registry(tmp_path)
    assert reg.get_api("alice") is reg.get_api("alice")


def test_registry_rejects_unsafe_username(tmp_path):
    reg = Registry(tmp_path)
    for bad in ["../escape", "a/b", "", "x" * 33, "Bad Name"]:
        with pytest.raises(ValueError):
            reg.get_api(bad)


from fastapi.testclient import TestClient as _FastApiTestClient
from app.ai import client as ai_client


class TestClient(_FastApiTestClient):
    """TestClient that talks to the app over https://testserver so the
    session cookie (set Secure by https_only=True, matching the real
    Cloudflare HTTPS tunnel) is sent back on follow-up requests."""

    def __init__(self, app, *args, **kwargs):
        kwargs.setdefault("base_url", "https://testserver")
        super().__init__(app, *args, **kwargs)


def _make_app(tmp_path):
    from web.server import create_app
    store = auth.UserStore(tmp_path / "users.json")
    store.add("alice", "pw1")
    store.add("bob", "pw2")
    return create_app(base_dir=tmp_path, users_path=tmp_path / "users.json",
                      secret_key="test-secret")


def _login(c, user, pw):
    return c.post("/login", data={"username": user, "password": pw},
                  follow_redirects=False)


def test_dispatch_requires_auth(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        r = c.post("/api/get_overview", json=[])
        assert r.status_code == 401


def test_login_then_overview(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        assert _login(c, "alice", "pw1").status_code == 303
        r = c.post("/api/get_overview", json=[])
        assert r.status_code == 200 and r.json()["ok"] is True


def test_bad_login_rejected(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        r = _login(c, "alice", "WRONG")
        assert r.status_code == 303 and "error" in r.headers["location"]
        assert c.post("/api/get_overview", json=[]).status_code == 401


def test_non_allowlisted_method_forbidden(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        _login(c, "alice", "pw1")
        # a real Api attribute that is NOT in the allowlist
        assert c.post("/api/export_csv", json=["2026-06"]).status_code != 200
        assert c.post("/api/__init__", json=[]).status_code == 403


def test_root_redirects_when_logged_out(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith("/login")


def test_two_users_isolated(tmp_path, monkeypatch):
    # force the offline regex parser so add_entry is deterministic + network-free
    monkeypatch.setattr(ai_client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ai_client.AIUnavailable("offline")))
    app = _make_app(tmp_path)
    with TestClient(app) as ca, TestClient(app) as cb:
        _login(ca, "alice", "pw1")
        _login(cb, "bob", "pw2")
        ca.post("/api/add_entry", json=["45 coffee"])
        a_recent = ca.post("/api/get_overview", json=[]).json()["recent"]
        b_recent = cb.post("/api/get_overview", json=[]).json()["recent"]
        assert len(a_recent) == 1 and len(b_recent) == 0


from web import users as users_cli


def test_cli_add_and_list(tmp_path, monkeypatch, capsys):
    upath = tmp_path / "users.json"
    monkeypatch.setattr(users_cli.getpass, "getpass", lambda *a, **k: "secret")
    assert users_cli.main(["add", "alice", "--users", str(upath)]) == 0
    store = auth.UserStore(upath)
    assert store.verify("alice", "secret")
    assert users_cli.main(["list", "--users", str(upath)]) == 0
    assert "alice" in capsys.readouterr().out


def test_cli_remove(tmp_path, monkeypatch):
    upath = tmp_path / "users.json"
    monkeypatch.setattr(users_cli.getpass, "getpass", lambda *a, **k: "secret")
    users_cli.main(["add", "bob", "--users", str(upath)])
    assert users_cli.main(["remove", "bob", "--users", str(upath)]) == 0
    assert not auth.UserStore(upath).exists("bob")


def test_export_csv_downloads(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        _login(c, "alice", "pw1")
        r = c.get("/api/export_csv?month=2026-06")
        assert r.status_code == 200
        assert "attachment" in r.headers["content-disposition"]
        # CSV is written utf-8-sig (Excel-friendly BOM); strip it before checking.
        assert r.text.lstrip("﻿").splitlines()[0].startswith("date,amount_ils")
        # the file was written under alice's dir, not the shared repo exports/
        assert (tmp_path / "alice" / "exports" / "moneypilot-2026-06.csv").exists()


def test_export_csv_requires_auth(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        r = c.get("/api/export_csv?month=2026-06", follow_redirects=False)
        assert r.status_code == 303
