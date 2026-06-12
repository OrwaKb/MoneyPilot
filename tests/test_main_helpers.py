import datetime as dt

from app import db
from app.__main__ import data_dir
from scripts.dev_seed import seed


def test_data_dir_is_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert data_dir() == tmp_path / "MoneyPilot"

def test_seed_populates(tmp_path):
    conn = db.connect(tmp_path / "dev.db")
    db.init_db(conn)
    seed(conn, today=dt.date(2026, 6, 11))
    assert db.get_setting(conn, "salary_day") == "10"
    assert len(db.list_transactions(conn)) > 30
    assert len(db.list_goals(conn)) == 2


def test_suppress_child_consoles_patches_popen(monkeypatch):
    import subprocess
    import sys
    from app.__main__ import _suppress_child_consoles

    orig = subprocess.Popen.__init__
    try:
        # _suppress_child_consoles must change __init__ (patchedness check)
        _suppress_child_consoles()
        assert subprocess.Popen.__init__ is not orig, \
            "_suppress_child_consoles did not patch subprocess.Popen.__init__"

        # Verify a child spawned after patching completes successfully
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        assert p.returncode == 0, "child process failed after patching"

        # Verify CREATE_NO_WINDOW is injected: place a spy as the new "orig"
        # then re-apply the patch so call order is: patched -> spy -> (nothing)
        seen: dict = {}

        def spy(self, *a, **kw):
            seen.update(kw)
            # don't actually spawn — just record flags

        subprocess.Popen.__init__ = spy
        _suppress_child_consoles()  # wraps spy; order: outer_patched -> spy

        flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if sys.platform == "win32":
            # Directly invoke the patched __init__ with no creationflags
            # to see what flags get injected (using object.__new__ to avoid
            # a real spawn)
            dummy = object.__new__(subprocess.Popen)
            try:
                subprocess.Popen.__init__(dummy, [sys.executable, "-c", "pass"])
            except Exception:
                pass  # spy doesn't do real init; OSError/TypeError expected
            assert seen.get("creationflags", 0) & flag, \
                "CREATE_NO_WINDOW was not injected into creationflags"
    finally:
        subprocess.Popen.__init__ = orig
