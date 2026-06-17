import datetime as dt
import json
import urllib.error
import urllib.request

import pytest

from app import db, sync_server
from app.ai import parser
from app.api import Api

TODAY = dt.date(2026, 6, 11)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "_ai_parse",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    api = Api(tmp_path / "ledger.db", backup_dir=tmp_path / "b", today_fn=lambda: TODAY)
    httpd = sync_server.start(api, "secret-token", port=0)
    port = httpd.server_address[1]
    yield {"api": api, "token": "secret-token", "port": port}
    httpd.shutdown()


def _req(port, method="POST", body=None, token=None, path="/pocket/sync"):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None), r.headers
    except urllib.error.HTTPError as e:
        return e.code, None, e.headers


def test_sync_ingests_with_valid_token(server):
    code, data, _ = _req(server["port"], body={"entries": [
        {"uuid": "u1", "raw_text": "45 falafel", "created_at": "2026-06-11T10:00:00"}]},
        token=server["token"])
    assert code == 200 and data["synced"] == ["u1"]
    assert len(db.list_transactions(server["api"].conn)) == 1


def test_sync_accepts_query_token_no_auth_header(server):
    # the phone's no-preflight "simple request" puts the token in ?t= with no
    # Authorization header; the server must accept it.
    port, tok = server["port"], server["token"]
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/pocket/sync?t={tok}",
        data=json.dumps({"entries": []}).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200 and json.loads(r.read())["synced"] == []


def test_sync_rejects_bad_query_token(server):
    port = server["port"]
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/pocket/sync?t=wrong",
        data=b'{"entries":[]}', method="POST")
    try:
        urllib.request.urlopen(req, timeout=5); code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 401


def test_sync_rejects_bad_token(server):
    code, _, _ = _req(server["port"], body={"entries": []}, token="wrong")
    assert code == 401


def test_sync_rejects_missing_token(server):
    code, _, _ = _req(server["port"], body={"entries": []}, token=None)
    assert code == 401


def test_sync_bad_json_is_400(server):
    req = urllib.request.Request(f"http://127.0.0.1:{server['port']}/pocket/sync",
                                 data=b"not json", method="POST",
                                 headers={"Authorization": "Bearer " + server["token"]})
    try:
        urllib.request.urlopen(req, timeout=5)
        code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 400


def test_ping_is_open(server):
    code, data, _ = _req(server["port"], method="GET", path="/pocket/ping")
    assert code == 200 and data["ok"] is True


def test_options_preflight_sets_cors(server):
    code, _, headers = _req(server["port"], method="OPTIONS")
    assert code == 204
    assert headers.get("Access-Control-Allow-Origin") == "*"
    assert "authorization" in headers.get("Access-Control-Allow-Headers", "").lower()
    # Private Network Access opt-in (Tailscale IPs are private; the PWA is public)
    assert headers.get("Access-Control-Allow-Private-Network") == "true"
