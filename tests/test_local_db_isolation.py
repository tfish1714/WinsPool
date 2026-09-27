"""Tests for the .local_db redirect helper and the real-folder guard."""
import pathlib
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _local_db_guard as guard  # noqa: E402

from services import local_paths  # noqa: E402


@pytest.fixture
def fake_real(monkeypatch, tmp_path):
    """A hermetic stand-in for the repo's real .local_db, plus a redirect dir."""
    root = tmp_path / "repo"
    real = root / ".local_db"
    real.mkdir(parents=True)
    (real / "seed.txt").write_text("x")
    redirect = tmp_path / "copy"
    redirect.mkdir()
    monkeypatch.setattr(local_paths, "REAL_LOCAL_DB", real)
    return root, real, redirect


def test_no_redirect_var_returns_relative(monkeypatch):
    monkeypatch.delenv(local_paths.REDIRECT_ENV, raising=False)
    assert local_paths.local_db_dir() == pathlib.Path(".local_db")


def test_redirect_when_cwd_is_repo_root(monkeypatch, fake_real):
    root, _real, redirect = fake_real
    monkeypatch.setenv(local_paths.REDIRECT_ENV, str(redirect))
    monkeypatch.chdir(root)
    assert local_paths.local_db_dir() == redirect


def test_no_redirect_when_cwd_is_elsewhere(monkeypatch, fake_real, tmp_path):
    _root, _real, redirect = fake_real
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv(local_paths.REDIRECT_ENV, str(redirect))
    monkeypatch.chdir(other)
    assert local_paths.local_db_dir() == pathlib.Path(".local_db")


def test_db_service_writers_use_redirect_not_real(monkeypatch, fake_real):
    from services import db_service
    root, real, redirect = fake_real
    monkeypatch.setenv(local_paths.REDIRECT_ENV, str(redirect))
    monkeypatch.setenv("USE_LOCAL_DATA", "true")
    monkeypatch.chdir(root)
    before = guard.fingerprint(real)

    db_service._save_df_to_local("players", pd.DataFrame([{"playerId": 1}]))
    monkeypatch.setattr(db_service, "get_db", lambda: None)
    db_service.set_config_settings({"draft_active": True})
    db_service.save_metadata("m1", {"a": 1})

    assert (redirect / "players.pkl").exists()
    assert (redirect / "config_settings.json").exists()
    assert (redirect / "metadata.pkl").exists()
    assert guard.diff(before, guard.fingerprint(real)) == []


def test_cache_service_default_dir_follows_redirect(tmp_path):
    """_GAME_PRED_DIR is computed at import; a fresh interpreter under a redirect uses it."""
    import subprocess, os
    redirect = tmp_path / "copy"
    redirect.mkdir()
    repo = pathlib.Path(__file__).resolve().parent.parent
    env = dict(os.environ, USE_LOCAL_DATA="true")
    env[local_paths.REDIRECT_ENV] = str(redirect)
    code = "from services import cache_service as c; print(c._GAME_PRED_DIR)"
    out = subprocess.run([sys.executable, "-c", code], cwd=str(repo), env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert pathlib.Path(out.stdout.strip().splitlines()[-1]) == redirect


def test_guard_unchanged_tree_reports_nothing(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    fp = guard.fingerprint(tmp_path)
    assert guard.diff(fp, guard.fingerprint(tmp_path)) == []


def test_guard_detects_modified_new_deleted(tmp_path):
    (tmp_path / "keep.txt").write_text("1")
    (tmp_path / "gone.txt").write_text("1")
    (tmp_path / "chg.txt").write_text("1")
    before = guard.fingerprint(tmp_path)
    (tmp_path / "chg.txt").write_text("22222")
    (tmp_path / "gone.txt").unlink()
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "new.txt").write_text("n")
    problems = guard.diff(before, guard.fingerprint(tmp_path))
    assert "modified file: chg.txt" in problems
    assert "deleted file: gone.txt" in problems
    assert "new file: sub/new.txt" in problems
    assert not any("keep.txt" in p for p in problems)


def test_guard_detects_modified_same_size(tmp_path):
    import os
    p = tmp_path / "f.txt"
    p.write_text("a")
    before = guard.fingerprint(tmp_path)
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert guard.diff(before, guard.fingerprint(tmp_path)) == ["modified file: f.txt"]


def test_guard_detects_created_directory(tmp_path):
    target = tmp_path / ".local_db"
    before = guard.fingerprint(target)
    assert before == {"exists": False, "files": {}}
    target.mkdir()
    problems = guard.diff(before, guard.fingerprint(target))
    assert len(problems) == 1 and "CREATED" in problems[0]


def test_escape_hatch_downgrades_to_warning():
    msg_err = guard.report(["new file: x"], allow=False)
    msg_warn = guard.report(["new file: x"], allow=True)
    assert msg_err.startswith("ERROR") and "new file: x" in msg_err
    assert msg_warn.startswith("WARNING") and "WINSPOOL_ALLOW_LOCAL_DB_WRITES=1" in msg_warn


def test_guard_verdict_respects_escape_hatch(monkeypatch):
    fail = guard.should_fail(["new file: x"], env={})
    assert fail is True
    assert guard.should_fail(["new file: x"], env={"WINSPOOL_ALLOW_LOCAL_DB_WRITES": "1"}) is False
    assert guard.should_fail([], env={}) is False


def test_remove_tree_deletes_readonly_files_and_dirs(tmp_path):
    import os, stat
    root = tmp_path / "victim"
    sub_dir = root / "analytics"
    sub_dir.mkdir(parents=True)
    f = sub_dir / "ro.json"
    f.write_text("{}")
    os.chmod(f, stat.S_IREAD)
    os.chmod(sub_dir, stat.S_IREAD | stat.S_IEXEC)
    guard.remove_tree(root)
    assert not root.exists()


def test_copy_tree_copies_files_and_nested_dirs(tmp_path):
    src = tmp_path / "src"
    (src / "analytics").mkdir(parents=True)
    (src / "a.pkl").write_text("1")
    (src / "analytics" / "b.json").write_text("2")
    dst = tmp_path / "dst"
    dst.mkdir()
    guard.copy_tree(src, dst)
    assert (dst / "a.pkl").read_text() == "1"
    assert (dst / "analytics" / "b.json").read_text() == "2"
