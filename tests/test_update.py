import pytest

from app import update, version


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload, self.status = payload, status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests_exc(f"HTTP {self.status}")

    def json(self):
        return self._payload


def requests_exc(msg):
    import requests
    return requests.RequestException(msg)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(version, "GITHUB_REPO", "alice/MoneyPilot")
    monkeypatch.setattr(version, "UPDATE_API_URL",
                        "https://api.github.com/repos/alice/MoneyPilot/releases/latest")


def test_parse_version():
    assert update._parse_version("v1.2.3") == (1, 2, 3)
    assert update._parse_version("1.2") == (1, 2)
    assert update._parse_version("2.0.0-beta1") == (2, 0, 0)
    assert update._parse_version("nightly") == ()


def test_is_newer():
    assert update._is_newer("v1.1.0", "1.0.0")
    assert update._is_newer("1.0.1", "1.0.0")
    assert update._is_newer("1.1", "1.0.5")
    assert not update._is_newer("1.0.0", "1.0.0")
    assert not update._is_newer("1.0.0", "1.1.0")
    assert not update._is_newer("garbage", "1.0.0")     # bad remote -> no prompt


def test_check_update_available(configured, monkeypatch):
    payload = {"tag_name": "v1.2.0", "body": "Faster startup.\nBug fixes.",
               "html_url": "https://github.com/alice/MoneyPilot/releases/tag/v1.2.0",
               "assets": [{"name": "MoneyPilot-windows.zip",
                           "browser_download_url":
                           "https://github.com/alice/MoneyPilot/releases/download/v1.2.0/MoneyPilot-windows.zip"}]}
    monkeypatch.setattr(update.requests, "get", lambda *a, **k: FakeResp(payload))
    out = update.check_for_update(current="1.0.0")
    assert out["update_available"] is True
    assert out["version"] == "1.2.0"
    # download points at the release PAGE (installer + zip + notes), not an asset
    assert out["url"] == "https://github.com/alice/MoneyPilot/releases/tag/v1.2.0"
    assert out["notes"].startswith("Faster startup.")


def test_check_update_falls_back_to_release_page(configured, monkeypatch):
    payload = {"tag_name": "v1.2.0", "body": "",
               "html_url": "https://github.com/alice/MoneyPilot/releases/tag/v1.2.0",
               "assets": []}
    monkeypatch.setattr(update.requests, "get", lambda *a, **k: FakeResp(payload))
    out = update.check_for_update(current="1.0.0")
    assert out["update_available"] is True and out["url"].endswith("/v1.2.0")


def test_check_update_same_version(configured, monkeypatch):
    monkeypatch.setattr(update.requests, "get",
                        lambda *a, **k: FakeResp({"tag_name": "v1.0.0"}))
    assert update.check_for_update(current="1.0.0")["update_available"] is False


def test_check_update_network_error_is_silent(configured, monkeypatch):
    def boom(*a, **k):
        raise requests_exc("offline")
    monkeypatch.setattr(update.requests, "get", boom)
    assert update.check_for_update(current="1.0.0") == {"update_available": False}


def test_check_update_dormant_when_repo_unset(monkeypatch):
    # an unconfigured/placeholder repo must NOT touch the network at all
    monkeypatch.setattr(version, "GITHUB_REPO", "YOUR_GITHUB_USERNAME/MoneyPilot")
    hits = {"n": 0}
    def counted(*a, **k):
        hits["n"] += 1
        return FakeResp({"tag_name": "v9.9.9"})
    monkeypatch.setattr(update.requests, "get", counted)
    assert update.check_for_update(current="1.0.0")["update_available"] is False
    assert hits["n"] == 0


def test_repo_configured_true_for_real_default():
    # the shipped default is now a real repo, so the feature is live
    assert update._repo_configured() is True
