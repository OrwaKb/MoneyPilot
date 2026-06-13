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
    a = reg.fresh_api("alice")
    b = reg.fresh_api("bob")
    assert a is not b
    assert (tmp_path / "alice" / "ledger.db").exists()
    assert (tmp_path / "bob" / "ledger.db").exists()


def test_fresh_api_gives_distinct_connections(tmp_path):
    # per-request connections: every call is its own Api on its own connection,
    # so a slow request can't hold a shared connection hostage
    reg = Registry(tmp_path)
    a, b = reg.fresh_api("alice"), reg.fresh_api("alice")
    assert a is not b and a.conn is not b.conn


def test_registry_rejects_unsafe_username(tmp_path):
    reg = Registry(tmp_path)
    for bad in ["../escape", "a/b", "", "x" * 33, "Bad Name"]:
        with pytest.raises(ValueError):
            reg.fresh_api(bad)


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
    # 0 brute-force delay so the throttle integration tests stay fast
    return create_app(base_dir=tmp_path, users_path=tmp_path / "users.json",
                      secret_key="test-secret", login_block_delay_s=0.0)


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


def test_cli_add_rejects_invalid_username(tmp_path, monkeypatch):
    # A name that can't map to a safe users/<name>/ dir must be rejected at
    # creation — otherwise it could log in but error on every request.
    upath = tmp_path / "users.json"
    monkeypatch.setattr(users_cli.getpass, "getpass", lambda *a, **k: "secret")
    for bad in ["Bad Name", "../escape", "CAPS", "a" * 33]:
        with pytest.raises(SystemExit):           # argparse .error() exits
            users_cli.main(["add", bad, "--users", str(upath)])
        assert not auth.UserStore(upath).exists(bad)


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


def test_throttle_penalty_sleeps_when_configured():
    # once over the limit, a wrong guess pays a delay so brute force can't run at
    # full speed; with no delay configured it is a no-op
    import asyncio
    from web.server import LoginThrottle
    calls = []

    async def fake_sleep(s):
        calls.append(s)

    th = LoginThrottle(block_delay_s=0.7, sleep_fn=fake_sleep)
    asyncio.run(th.penalty())
    assert calls == [0.7]
    th0 = LoginThrottle(block_delay_s=0.0, sleep_fn=fake_sleep)
    calls.clear()
    asyncio.run(th0.penalty())
    assert calls == []


def test_internal_error_sanitized_on_web(tmp_path, monkeypatch):
    # an unexpected internal error must NOT leak its message to the remote client
    from app.engine import insights
    monkeypatch.setattr(insights, "fact_pack",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("boom /secret/path/ledger.db")))
    with TestClient(_make_app(tmp_path)) as c:
        _login(c, "alice", "pw1")
        body = c.post("/api/get_overview", json=[]).json()
        assert body["ok"] is False
        assert "secret" not in (body.get("error") or "")
        assert "ledger.db" not in (body.get("error") or "")


def test_user_error_passes_through_on_web(tmp_path):
    # a deliberate validation error is still shown verbatim (it's meant for them)
    with TestClient(_make_app(tmp_path)) as c:
        _login(c, "alice", "pw1")
        body = c.post("/api/save_goal", json=[{"name": "", "target_ils": 100}]).json()
        assert body["ok"] is False and "name" in body["error"].lower()


def test_invalid_username_account_cannot_get_a_session(tmp_path):
    # a name that slipped into users.json (hand-edited / pre-validation) must NOT
    # get a working session: fresh_api would reject it and 500 every /api call.
    from web.server import create_app
    store = auth.UserStore(tmp_path / "users.json")
    store.add("Bad Name", "pw")            # bypasses the CLI's valid_username gate
    app = create_app(base_dir=tmp_path, users_path=tmp_path / "users.json",
                     secret_key="test-secret", login_block_delay_s=0.0)
    with TestClient(app) as c:
        r = c.post("/login", data={"username": "Bad Name", "password": "pw"},
                   follow_redirects=False)
        assert r.status_code == 303 and "error" in r.headers["location"]
        # never reaches fresh_api -> no 500; just unauthenticated
        assert c.post("/api/get_overview", json=[]).status_code == 401


def test_valid_username_still_logs_in(tmp_path):
    # the guard must not refuse a normal lowercase account
    with TestClient(_make_app(tmp_path)) as c:
        assert _login(c, "alice", "pw1").status_code == 303
        assert c.post("/api/get_overview", json=[]).status_code == 200


def test_unknown_user_verify_single_pbkdf2(tmp_path, monkeypatch):
    # unknown and known usernames must do the SAME pbkdf2 work (no timing oracle)
    import web.auth as auth_mod
    store = auth_mod.UserStore(tmp_path / "u.json")
    store.add("alice", "pw")
    calls = []
    real = auth_mod.hashlib.pbkdf2_hmac
    monkeypatch.setattr(auth_mod.hashlib, "pbkdf2_hmac",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    store.verify("ghost", "x")
    unknown = len(calls)
    calls.clear()
    store.verify("alice", "wrong")
    known = len(calls)
    assert unknown == known == 1


def test_login_throttle_is_per_username(tmp_path):
    # alice being hammered must not lock out bob.
    with TestClient(_make_app(tmp_path)) as c:
        for _ in range(6):
            _login(c, "alice", "WRONG")
        good = _login(c, "bob", "pw2")
        assert good.status_code == 303 and good.headers["location"].endswith("/")


def test_slow_ai_call_does_not_block_other_requests(tmp_path, monkeypatch):
    # Regression for the Cloudflare "context canceled" storm: an AI-bound
    # request (here add_entry, which calls ask_claude) must NOT freeze this
    # user's other requests. A slow parse and a concurrent get_overview run
    # against their OWN connections, so the overview returns promptly.
    import threading
    import time
    entered = threading.Event()      # set once the slow AI call is in flight
    release = threading.Event()      # lets the slow call finish

    def slow_ask(*a, **k):
        entered.set()
        release.wait(timeout=5)
        raise ai_client.AIUnavailable("slow")   # -> fast regex fallback stores it

    monkeypatch.setattr(ai_client, "ask_claude", slow_ask)
    app = _make_app(tmp_path)

    def slow_request():
        with TestClient(app) as c:
            _login(c, "alice", "pw1")
            c.post("/api/add_entry", json=["45 coffee"])

    ta = threading.Thread(target=slow_request)
    ta.start()
    assert entered.wait(2), "slow request never reached the AI call"

    # safety net so a serialized (buggy) server can't hang the test forever
    release_timer = threading.Timer(2.0, release.set)
    release_timer.start()
    with TestClient(app) as c2:
        _login(c2, "alice", "pw1")
        t0 = time.monotonic()
        r = c2.post("/api/get_overview", json=[])
        elapsed = time.monotonic() - t0
    release.set()
    release_timer.cancel()
    ta.join(5)

    assert r.status_code == 200 and r.json()["ok"] is True
    assert elapsed < 1.5, f"get_overview blocked {elapsed:.2f}s behind a slow AI call"


def test_concurrent_same_user_requests_do_not_error(tmp_path, monkeypatch):
    # Regression: FastAPI runs sync methods in a threadpool. Each request now
    # gets its OWN connection (WAL + busy_timeout), so concurrent same-user
    # reads+writes don't race a shared connection or raise transaction-state
    # errors — and no serialization lock is needed to keep them clean.
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
