from pathlib import Path

from app import paths


def test_resource_path_joins_under_root():
    p = paths.resource_path("app", "ui", "index.html")
    assert p == paths.RESOURCE_ROOT / "app" / "ui" / "index.html"


def test_resource_root_is_repo_when_not_frozen():
    # from source, resources resolve against the repo root (parent of app/)
    assert not paths.FROZEN
    assert (paths.RESOURCE_ROOT / "app" / "paths.py").exists()


def test_data_dir_uses_localappdata(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"X:\Local")
    assert paths.data_dir() == Path(r"X:\Local") / "MoneyPilot"


def test_data_dir_falls_back_without_localappdata(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    # never raises; lands somewhere under the user's home
    d = paths.data_dir()
    assert d.name == "MoneyPilot"
