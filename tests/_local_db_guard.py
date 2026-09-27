"""Fingerprint + diff helpers for the real-.local_db guard (see conftest.py).

fingerprint(path) records whether the directory exists plus the relative path,
size and mtime_ns of every file under it. diff(before, after) turns two
fingerprints into a list of human-readable difference lines (empty == unchanged).
"""
import os
import pathlib
import shutil
import stat


def fingerprint(path) -> dict:
    root = pathlib.Path(path)
    if not root.is_dir():
        return {"exists": False, "files": {}}
    files = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            p = pathlib.Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            files[p.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
    return {"exists": True, "files": files}


def diff(before: dict, after: dict) -> list:
    problems = []
    if not before["exists"] and after["exists"]:
        problems.append("directory was CREATED (it did not exist at session start)")
    if before["exists"] and not after["exists"]:
        problems.append("directory was DELETED")
    b, a = before["files"], after["files"]
    for rel in sorted(set(a) - set(b)):
        problems.append(f"new file: {rel}")
    for rel in sorted(set(b) - set(a)):
        problems.append(f"deleted file: {rel}")
    for rel in sorted(set(a) & set(b)):
        if a[rel] != b[rel]:
            problems.append(f"modified file: {rel}")
    return problems


def report(problems: list, allow: bool) -> str:
    """Multi-line message for a non-empty problem list."""
    head = ("WARNING" if allow else "ERROR") + (
        ": the test run changed the developer's real .local_db "
        "(tests must only touch the per-run copy)."
    )
    lines = [head] + [f"  - {p}" for p in problems]
    if allow:
        lines.append("  (downgraded to a warning by WINSPOOL_ALLOW_LOCAL_DB_WRITES=1)")
    else:
        lines.append("  Set WINSPOOL_ALLOW_LOCAL_DB_WRITES=1 only for intentional dev flows.")
    return "\n".join(lines)


def should_fail(problems: list, env=None) -> bool:
    """True when the run must be failed: problems exist and no escape hatch."""
    env = os.environ if env is None else env
    allow = str(env.get("WINSPOOL_ALLOW_LOCAL_DB_WRITES", "")).lower() in ("1", "true", "yes")
    return bool(problems) and not allow


def _make_writable_and_retry(func, path, *_exc):
    """rmtree error handler (works for onerror and onexc): clear read-only, retry, then give up quietly."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(path)
    except OSError:
        pass


def remove_tree(path) -> None:
    """Remove a directory tree, tolerating Windows read-only attributes."""
    try:
        shutil.rmtree(path, onexc=_make_writable_and_retry)
    except TypeError:  # Python < 3.12 has only onerror
        shutil.rmtree(path, onerror=_make_writable_and_retry)


def copy_tree(src, dst) -> None:
    """Copy a tree without preserving directory attributes/metadata (avoids read-only dirs)."""
    shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=shutil.copyfile)
