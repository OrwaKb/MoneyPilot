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
