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


def test_login_throttle_unit():
    from web.server import LoginThrottle
    clock = {"t": 0.0}
    th = LoginThrottle(max_fails=3, window_s=100, now_fn=lambda: clock["t"])
    for _ in range(3):
        assert not th.blocked("1.2.3.4")
        th.record_fail("1.2.3.4")
    assert th.blocked("1.2.3.4")
    clock["t"] = 101.0                     # window elapsed
    assert not th.blocked("1.2.3.4")


def test_login_throttle_no_lockout_of_valid_credentials(tmp_path):
    # Regression for the global-lockout DoS: a flood of wrong passwords flags
    # throttling on the FAILED attempts, but a correct password is NEVER refused.
    with TestClient(_make_app(tmp_path)) as c:
        last_bad = None
        for _ in range(6):
            last_bad = _login(c, "alice", "WRONG")
        assert "throttled" in last_bad.headers["location"]   # failures flagged
        good = _login(c, "alice", "pw1")                     # ...valid still works
        assert good.status_code == 303 and good.headers["location"].endswith("/")
        assert c.post("/api/get_overview", json=[]).status_code == 200


def test_login_throttle_is_per_username(tmp_path):
    # alice being hammered must not lock out bob.
    with TestClient(_make_app(tmp_path)) as c:
        for _ in range(6):
            _login(c, "alice", "WRONG")
        good = _login(c, "bob", "pw2")
        assert good.status_code == 303 and good.headers["location"].endswith("/")


def test_dispatch_lock_is_per_user(tmp_path):
    reg = Registry(tmp_path)
    assert reg.dispatch_lock("alice") is reg.dispatch_lock("alice")
    assert reg.dispatch_lock("alice") is not reg.dispatch_lock("bob")


def test_concurrent_same_user_requests_do_not_error(tmp_path, monkeypatch):
    # Regression: one cached Api per user holds ONE sqlite connection, and
    # FastAPI runs sync methods in a threadpool. Without per-user serialization,
    # concurrent same-user reads+writes race the connection and raise
    # transaction-state errors. The dispatch lock must keep them clean.
    monkeypatch.setattr(ai_client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ai_client.AIUnavailable("offline")))
    import threading
    app = _make_app(tmp_path)
    errors = []

    def worker(kind):
        try:
            with TestClient(app) as c:
                _login(c, "alice", "pw1")
                for _ in range(10):
                    if kind == "w":
                        r = c.post("/api/add_entry", json=["10 coffee"])
                    else:
                        r = c.post("/api/get_overview", json=[])
                    if r.status_code != 200 or r.json().get("ok") is False:
                        errors.append((kind, r.status_code, r.json().get("error")))
        except Exception as e:                       # record any thread crash
            errors.append((kind, "exc", repr(e)))

    threads = [threading.Thread(target=worker, args=(k,))
               for k in ("w", "r", "w", "r")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:5]
