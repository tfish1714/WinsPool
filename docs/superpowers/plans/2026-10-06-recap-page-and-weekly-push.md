# Recap Page, Publish Flow, and Weekly Push Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Players read every weekly recap in the app and get opt-in pushes when a recap is published and when their weekly standing is ready; the owner publishes recaps by pasting text, with no Gemini call or email unless ticked.

**Architecture:** Recaps move to one `season_recaps/{year}` doc with a `weeks` map (cached reads, legacy fallback, migration script). A render-time sanitizing helper turns stored plain text into safe HTML for the standings card and a new `/recap/{year}/{week}` page. `push_service` gains preference-aware, deep-linking sends plus saved `push_events` records; a new step in the daily sync job sends one personal standings push per completed week.

**Tech Stack:** FastAPI, Jinja2, Firestore (+ local JSON mirror), pywebpush (already in requirements), vanilla ES modules, pytest, node (behavioral JS tests).

**Spec:** `docs/superpowers/specs/2026-10-06-recap-page-and-weekly-push-design.md` (read it first; this plan implements it task by task).

## Global Constraints

- No emojis in code, comments, commit messages, docs.
- Firestore is the source of truth; local files are a mirror rebuilt by `scripts/refresh_local_pkls.py`. All `.local_db` writes go through `services/local_paths.py::local_db_dir()`. Baseline guard: `python -m pytest tests/test_local_db_isolation.py -q` must stay green.
- Routes and services never read `rawdata/`. Scripts that write Firestore call `require_db()` (forces `USE_LOCAL_DATA=False`) before importing db-using services.
- Nav destinations are added to BOTH `static/js/main.js` (`_updateMoreDropdown`) and the `templates/base.html` drawer; `tests_e2e/test_nav_parity.py` must still pass.
- New CSS uses theme tokens only (`tests/test_theme.py`, `tests/test_theme_sweep.py`).
- Do not add generic `try/except Exception -> server_error()` in routes (global handler covers it). Tests asserting a 500 from an unhandled exception use `TestClient(app, raise_server_exceptions=False)`.
- Existing public signatures stay compatible: `db_service.save_weekly_recap(year, week, summary)` (new optional `source` kwarg), `db_service.get_weekly_recap(year, week)`, `push_service.broadcast_push_notification(title, body)` (existing tests monkeypatch it as `lambda t, b:`; new args are keyword-only and the return dict keeps exactly `{total, sent, failed, pruned}`), `push_service.send_push_notification`.
- Push sends are best-effort: a failure logs and never fails the caller or the sync job.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never `git add -A` (an untracked `cleanup-policy.json` and a OneDrive duplicate `tests/test_ios_push_banner_js (1).py` exist and must not be committed).
- `docs/` is gitignored: tracked docs (`docs/api_endpoints.md`, `docs/database.md`) edit normally; new doc files need `git add -f`.

## Rulings (made while planning)

- Cross-instance cache freshness is TTL-only (5 minutes); the recap cache is a private dict, not a `cache_service` domain, so no `signal_data_update` is sent. The web service runs `max-instances=1`, and `tests/test_db_cache_signals.py` only flags functions that call `clear_data_cache`. Spec text about "signal" is satisfied by TTL; cost if wrong: a recap could appear up to 5 minutes late on a second instance.
- The Publish API defaults `send_push` and `send_email` to `false`; the admin UI checks "Send push" by default and leaves "Also email" unchecked.
- Markdown rendering is in-house (no new dependency): escape everything first, then apply a small allowlist of transforms. Raw HTML in pasted text is escaped, never honored.
- `/recap` (latest) checks the active season, then the prior season; it never scans all seasons.
- The vapid public key is rendered by `base.html` through a new Jinja global `push_vapid_key`, guarded so the draft page (`templates/index.html`, which renders its own meta from context var `vapid_public_key`) does not get a duplicate.
- `ties` in the weekly standings push use the standings page's own ordering (`apply_tiebreakers`), so rank is list position.

## Review Focus

1. Plain-text recap containing `<script>`, `&`, `<`, line breaks, a `javascript:` link: renders escaped, no active link (Task 1 tests).
2. Publishing week 6 must not erase week 5; re-publishing the same week replaces only that week (Task 2 tests).
3. An unmigrated season (legacy per-week docs only) still shows its recaps (Task 2 tests).
4. Dead, missing, or opted-out subscriptions never break a send and are counted (Task 5 tests).
5. Notification click opens the intended same-origin page, focuses an existing window, and rejects off-origin URLs (Task 5 node test).
6. Re-running the daily sync after the weekly standings push sends nothing; an unfinished/postponed game in the week blocks the send; week 1 has no prior rank (Task 8 tests).

## File Structure

- Create `services/recap_render.py` (text to safe HTML, Jinja filter).
- Modify `services/db_service.py` (season recap storage, cache, legacy fallback, `source`).
- Create `scripts/migrate_weekly_recaps.py`; modify `scripts/refresh_local_pkls.py`.
- Create `routes/recap_routes.py`, `templates/recap.html`; modify `main.py`, `templates/base.html`, `templates/wins_pool.html`, `static/js/main.js`, `static/style.css`.
- Modify `services/email_service.py` (shared recap email builder), `routes/admin_routes.py`, `routes/models.py`, `scripts/generate_weekly_summary.py`, `templates/admin.html`, `static/js/admin_main.js`, `static/js/api.js`.
- Modify `services/push_service.py`, `static/sw.js`; create `services/push_events.py`.
- Create `static/js/push_client.js`, `static/js/push_card.js`, `static/js/push_nudge.js`; modify `templates/player_profile.html`, `static/js/main.js`, `routes/api_routes.py`.
- Create `services/standings_push_service.py`, `scripts/send_weekly_standings_push.py`; modify `scripts/run_cron.py`.
- Docs: `docs/api_endpoints.md`, `docs/database.md`, `docs/reference_manual.md`, `CLAUDE.md`, `DEPLOY.md`.

---

## Task 0: Branch and baseline

- [ ] **Step 1:** `git switch main && git pull` then `git switch -c feature/recap-page-weekly-push`. Confirm `git status` shows only the two known untracked files.
- [ ] **Step 2:** Run `python -m pytest tests/test_local_db_isolation.py -q` (expect 13 passed).

---

## Task 1: Recap text renderer and Jinja filter

**Files:**
- Create: `services/recap_render.py`
- Modify: `main.py:~109-114` (the `for t in [...]` template-env loop), `templates/wins_pool.html:~194-196`
- Test: `tests/test_recap_render.py`

**Interfaces:**
- Produces: `services.recap_render.render_recap_html(text) -> markupsafe.Markup` (empty `Markup("")` for None/blank); Jinja filter name `recap_html` registered on every template env.

- [ ] **Step 1: Write failing tests** `tests/test_recap_render.py`:

```python
from markupsafe import Markup
from services.recap_render import render_recap_html


def test_blank_returns_empty_markup():
    assert render_recap_html(None) == Markup("")
    assert render_recap_html("  \n ") == Markup("")


def test_escapes_raw_html():
    out = str(render_recap_html("Hi <script>alert(1)</script> & bye"))
    assert "<script>" not in out
    assert "&lt;script&gt;" in out and "&amp;" in out


def test_paragraphs_and_line_breaks():
    out = str(render_recap_html("Line one\nLine two\n\nSecond para"))
    assert out == "<p>Line one<br>Line two</p><p>Second para</p>"


def test_bold_italic_and_bullets():
    out = str(render_recap_html("**Big** win and *close* one\n\n- a\n- b"))
    assert "<strong>Big</strong>" in out and "<em>close</em>" in out
    assert "<ul><li>a</li><li>b</li></ul>" in out


def test_heading_line():
    assert str(render_recap_html("## Top story")) == "<h4>Top story</h4>"


def test_http_and_relative_links_allowed_javascript_not():
    ok = str(render_recap_html("[site](https://example.com/a?b=1&c=2) and [me](/player/3)"))
    assert 'href="https://example.com/a?b=1&amp;c=2"' in ok
    assert 'href="/player/3"' in ok and 'rel="noopener noreferrer"' in ok
    bad = str(render_recap_html("[x](javascript:alert(1))"))
    assert "<a " not in bad


def test_quote_in_text_cannot_break_attribute():
    out = str(render_recap_html('[a](https://e.com/x" onclick="y)'))
    assert "onclick=" not in out or 'onclick=&quot;' in out
    assert '" onclick="' not in out


def test_asterisk_math_not_italic():
    assert "<em>" not in str(render_recap_html("5 * 3 * 2"))
```

- [ ] **Step 2:** Run `python -m pytest tests/test_recap_render.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `services/recap_render.py` (no backslashes inside f-string expressions: the web image runs Python 3.10):

```python
"""services/recap_render.py -- stored recap text -> safe HTML at render time.

Recaps are stored as written (plain text, optionally light markdown). Everything
is HTML-escaped FIRST, then a small allowlist of transforms is applied to the
escaped text, so raw HTML in a recap can never become markup.
"""
import html
import re

from markupsafe import Markup

_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+|/[^\s)]*)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\*\w])")
_BULLET = re.compile(r"^\s*[-*]\s+")
_HEADING = re.compile(r"^#{1,3}\s+")
_BLOCK_SPLIT = re.compile(r"\n\s*\n")


def _inline(escaped: str) -> str:
    s = _LINK.sub(
        lambda m: '<a href="%s" rel="noopener noreferrer">%s</a>' % (m.group(2), m.group(1)),
        escaped,
    )
    s = _BOLD.sub(r"<strong>\1</strong>", s)
    return _ITALIC.sub(r"<em>\1</em>", s)


def _esc_inline(raw: str) -> str:
    return _inline(html.escape(raw))


def render_recap_html(text) -> Markup:
    if text is None or not str(text).strip():
        return Markup("")
    blocks = _BLOCK_SPLIT.split(str(text).replace("\r\n", "\n").strip())
    out = []
    for block in blocks:
        lines = [ln.rstrip() for ln in block.split("\n")]
        if all(_BULLET.match(ln) for ln in lines):
            items = "".join("<li>%s</li>" % _esc_inline(_BULLET.sub("", ln)) for ln in lines)
            out.append("<ul>%s</ul>" % items)
        elif len(lines) == 1 and _HEADING.match(lines[0]):
            out.append("<h4>%s</h4>" % _esc_inline(_HEADING.sub("", lines[0])))
        else:
            out.append("<p>%s</p>" % "<br>".join(_esc_inline(ln) for ln in lines))
    return Markup("".join(out))
```

- [ ] **Step 4:** Run the tests; fix until green. (`test_quote_in_text_cannot_break_attribute`: `html.escape` turns `"` into `&quot;`, so the link regex stops at the escaped text and never emits a raw quote.)
- [ ] **Step 5: Register the filter and use it.** In `main.py` import `from services.recap_render import render_recap_html` and add `t.env.filters['recap_html'] = render_recap_html` inside the existing `for t in [...]` loop. In `templates/wins_pool.html` replace `{{ recap | safe }}` with `{{ recap | recap_html }}`.
- [ ] **Step 6: Filter-registration test** (append to `tests/test_recap_render.py`): import `main`; for each of `main.standings_templates, main.history_templates, main.draft_templates, main.admin_templates, main.mock_draft_templates` assert `'recap_html' in t.env.filters`. Add a standings-card test in the style of `tests/test_standings_routes.py` (see how it patches `sr.db.get_weekly_recap`): return `{"summary": "Hi <b>x</b>\nnext"}`, GET the standings page, assert the body contains `Hi &lt;b&gt;x&lt;/b&gt;<br>next`.
- [ ] **Step 7:** Run `python -m pytest tests/test_recap_render.py tests/test_standings_routes.py tests/test_theme_sweep.py -q`. Commit: `feat: render recap text to sanitized html at render time`.

---

## Task 2: `season_recaps` storage, cache, legacy fallback, migration

**Files:**
- Modify: `services/db_service.py:576-623` (replace `save_weekly_recap`/`get_weekly_recap` bodies; add helpers)
- Create: `scripts/migrate_weekly_recaps.py`
- Modify: `scripts/refresh_local_pkls.py:~43` (and its JSON handling, see Step 7)
- Test: `tests/test_season_recaps.py`, `tests/test_migrate_weekly_recaps.py`

**Interfaces:**
- Produces (all in `services.db_service`):
  - `get_season_recaps(year: int) -> dict[int, dict]` mapping week to `{"summary": str, "timestamp": float|None, "source": str|None}`; `{}` if none. Cached 300 s per year in a module dict `_RECAP_CACHE`.
  - `get_weekly_recap(year, week) -> dict | None` returning `{"year", "week", "summary", "timestamp"}`.
  - `list_recap_weeks(year: int) -> list[int]` sorted ascending.
  - `save_weekly_recap(year: int, week: int, summary: str, source: str | None = None) -> None`.
  - `clear_recap_cache(year: int | None = None) -> None` (tests and callers).
- Firestore doc `season_recaps/{year}`: `{year, weeks: {"<week>": {summary, timestamp, source?}}, updated_at}`. Local file `local_db_dir() / f"season_recaps_{year}.json"` with the same shape.

- [ ] **Step 1: Write failing tests** `tests/test_season_recaps.py`. Use a small in-memory fake Firestore defined in the test file (it is reused by Task 2's migration test, so put it in `tests/fake_firestore.py` and import it):

```python
# tests/fake_firestore.py
class _Snap:
    def __init__(self, data, doc_id):
        self._d, self.id = data, doc_id
        self.exists = data is not None
    def to_dict(self):
        return None if self._d is None else dict(self._d)

def _merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v

class _Doc:
    def __init__(self, store, name, doc_id):
        self.store, self.name, self.id = store, name, doc_id
    def get(self):
        return _Snap(self.store.get(self.name, {}).get(self.id), self.id)
    def set(self, data, merge=False):
        col = self.store.setdefault(self.name, {})
        if merge and self.id in col:
            _merge(col[self.id], data)
        else:
            col[self.id] = _deepcopy(data)
    def update(self, data):
        _merge(self.store.setdefault(self.name, {}).setdefault(self.id, {}), data)

def _deepcopy(d):
    import copy
    return copy.deepcopy(d)

class _Query:
    def __init__(self, store, name, preds):
        self.store, self.name, self.preds = store, name, preds
    def where(self, filter=None, **kw):
        return _Query(self.store, self.name, self.preds + [filter])
    def stream(self):
        for doc_id, data in list(self.store.get(self.name, {}).items()):
            if all(data.get(p.field_path) == p.value for p in self.preds):
                yield _Snap(data, doc_id)

class _Col(_Query):
    def __init__(self, store, name):
        super().__init__(store, name, [])
    def document(self, doc_id):
        return _Doc(self.store, self.name, str(doc_id))

class FakeFirestore:
    def __init__(self):
        self.store = {}
    def collection(self, name):
        return _Col(self.store, name)
```

(`FieldFilter` exposes `field_path` and `value` attributes; if the installed version names them differently, adapt the two attribute reads in `_Query.stream`.)

```python
# tests/test_season_recaps.py
import pytest
import services.db_service as db
from tests.fake_firestore import FakeFirestore


@pytest.fixture
def fs(monkeypatch):
    fake = FakeFirestore()
    monkeypatch.setattr(db, "get_db", lambda: fake)
    monkeypatch.setenv("USE_LOCAL_DATA", "False")
    db.clear_recap_cache()
    yield fake
    db.clear_recap_cache()


def test_save_then_get_roundtrip(fs):
    db.save_weekly_recap(2026, 5, "Week five text")
    got = db.get_weekly_recap(2026, 5)
    assert got["summary"] == "Week five text" and got["week"] == 5 and got["year"] == 2026
    assert db.list_recap_weeks(2026) == [5]


def test_saving_week_6_keeps_week_5_and_resave_replaces_only_that_week(fs):
    db.save_weekly_recap(2026, 5, "five")
    db.save_weekly_recap(2026, 6, "six")
    db.save_weekly_recap(2026, 6, "six v2")
    assert db.get_weekly_recap(2026, 5)["summary"] == "five"
    assert db.get_weekly_recap(2026, 6)["summary"] == "six v2"
    assert fs.store["season_recaps"]["2026"]["weeks"]["5"]["summary"] == "five"


def test_source_is_stored(fs):
    db.save_weekly_recap(2026, 7, "x", source="published")
    assert db.get_season_recaps(2026)[7]["source"] == "published"


def test_missing_week_returns_none_and_empty_list(fs):
    assert db.get_weekly_recap(2026, 3) is None
    assert db.list_recap_weeks(2026) == []


def test_reads_are_cached_until_save(fs, monkeypatch):
    db.save_weekly_recap(2026, 1, "a")
    db.get_season_recaps(2026)
    calls = []
    real = fs.collection
    monkeypatch.setattr(fs, "collection", lambda n: calls.append(n) or real(n))
    db.get_season_recaps(2026)
    assert calls == []                      # served from cache
    db.save_weekly_recap(2026, 2, "b")      # save clears the cache
    calls.clear()
    assert db.get_season_recaps(2026)[2]["summary"] == "b"
    assert calls                            # re-read


def test_legacy_per_week_docs_are_a_fallback_when_no_season_doc(fs):
    fs.collection("weekly_recaps").document("2025_3").set(
        {"year": 2025, "week": 3, "summary": "old three", "timestamp": 1.0})
    fs.collection("weekly_recaps").document("2025_4").set(
        {"year": 2025, "week": 4, "summary": "old four", "timestamp": 2.0})
    assert db.list_recap_weeks(2025) == [3, 4]
    assert db.get_weekly_recap(2025, 4)["summary"] == "old four"


def test_season_doc_wins_over_legacy(fs):
    fs.collection("weekly_recaps").document("2025_3").set(
        {"year": 2025, "week": 3, "summary": "legacy", "timestamp": 1.0})
    db.save_weekly_recap(2025, 9, "new nine")
    db.clear_recap_cache()
    assert db.list_recap_weeks(2025) == [9]


def test_local_mode_json_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "get_db", lambda: None)
    monkeypatch.setenv("USE_LOCAL_DATA", "True")
    monkeypatch.setattr(db, "local_db_dir", lambda: tmp_path)
    db.clear_recap_cache()
    db.save_weekly_recap(2026, 2, "local two")
    db.clear_recap_cache()
    assert (tmp_path / "season_recaps_2026.json").exists()
    assert db.get_weekly_recap(2026, 2)["summary"] == "local two"
    db.save_weekly_recap(2026, 3, "local three")
    assert db.list_recap_weeks(2026) == [2, 3]
```

- [ ] **Step 2:** Run `python -m pytest tests/test_season_recaps.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** in `services/db_service.py` (read the existing functions at ~576-623 and the module's imports first; `time`, `os`, `json`, `pd`, `FieldFilter` are already used there; add `json` import only if missing). Replace the two functions with:

```python
_RECAP_TTL_SECONDS = 300
_RECAP_CACHE: dict = {}   # {year: (monotonic_fetched_at, {week: {...}})}


def clear_recap_cache(year=None) -> None:
    if year is None:
        _RECAP_CACHE.clear()
    else:
        _RECAP_CACHE.pop(int(year), None)


def _recaps_local_path(year: int):
    return local_db_dir() / f"season_recaps_{int(year)}.json"


def _normalize_recap_weeks(raw) -> dict:
    out = {}
    for key, val in (raw or {}).items():
        try:
            week = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(val, dict) and val.get("summary"):
            out[week] = {
                "summary": val["summary"],
                "timestamp": val.get("timestamp"),
                "source": val.get("source"),
            }
    return out


def _legacy_firestore_recaps(db, year: int) -> dict:
    out = {}
    for doc in db.collection("weekly_recaps").where(
            filter=FieldFilter("year", "==", int(year))).stream():
        d = doc.to_dict() or {}
        if d.get("summary") and d.get("week") is not None:
            out[int(d["week"])] = {"summary": d["summary"],
                                    "timestamp": d.get("timestamp"), "source": None}
    return out


def _legacy_local_recaps(year: int) -> dict:
    path = local_db_dir() / "weekly_recaps.pkl"
    if not path.exists():
        return {}
    try:
        df = pd.read_pickle(path)
        df = df[df["year"] == year]
        return {int(r["week"]): {"summary": r["summary"],
                                  "timestamp": r.get("timestamp"), "source": None}
                for _, r in df.iterrows() if r.get("summary")}
    except Exception:
        logger.warning("Failed to read legacy local recaps for %s", year, exc_info=True)
        return {}


def _load_season_recaps(year: int) -> dict:
    db = get_db()
    if db is None:
        path = _recaps_local_path(year)
        if path.exists():
            try:
                with open(path) as f:
                    return _normalize_recap_weeks(json.load(f).get("weeks"))
            except Exception:
                logger.warning("Failed to read %s", path, exc_info=True)
                return {}
        return _legacy_local_recaps(year)
    doc = db.collection("season_recaps").document(str(year)).get()
    if doc.exists:
        return _normalize_recap_weeks((doc.to_dict() or {}).get("weeks"))
    return _legacy_firestore_recaps(db, year)


def get_season_recaps(year: int) -> dict:
    """{week: {summary, timestamp, source}} for one season (one doc read, cached 5 min)."""
    year = int(year)
    hit = _RECAP_CACHE.get(year)
    if hit and (time.monotonic() - hit[0]) < _RECAP_TTL_SECONDS:
        return dict(hit[1])
    weeks = _load_season_recaps(year)
    _RECAP_CACHE[year] = (time.monotonic(), weeks)
    return dict(weeks)


def list_recap_weeks(year: int) -> list:
    return sorted(get_season_recaps(year))


def get_weekly_recap(year: int, week: int):
    """One week's recap as {year, week, summary, timestamp}, or None."""
    entry = get_season_recaps(year).get(int(week))
    if not entry:
        return None
    return {"year": int(year), "week": int(week),
            "summary": entry["summary"], "timestamp": entry.get("timestamp")}


def save_weekly_recap(year: int, week: int, summary: str, source: str = None):
    """Store one week's recap on the season doc (merge: other weeks untouched)."""
    year, week = int(year), int(week)
    entry = {"summary": summary, "timestamp": time.time()}
    if source:
        entry["source"] = source
    db = get_db()
    if db:
        db.collection("season_recaps").document(str(year)).set(
            {"year": year, "weeks": {str(week): entry}, "updated_at": entry["timestamp"]},
            merge=True)
    if os.environ.get("USE_LOCAL_DATA", "False").lower() == "true":
        path = _recaps_local_path(year)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {"year": year, "weeks": {}}
            if path.exists():
                with open(path) as f:
                    data = json.load(f)
            data.setdefault("weeks", {})[str(week)] = entry
            data["updated_at"] = entry["timestamp"]
            with open(path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning("Failed to persist recap locally: %s", e)
    clear_recap_cache(year)
```

- [ ] **Step 4:** Run `python -m pytest tests/test_season_recaps.py tests/test_db_cache_signals.py tests/test_standings_routes.py -q`. Expected: PASS. Fix until green.
- [ ] **Step 5: Migration tests** `tests/test_migrate_weekly_recaps.py`:

```python
from scripts import migrate_weekly_recaps as mig
from tests.fake_firestore import FakeFirestore


def _legacy(fs):
    fs.collection("weekly_recaps").document("2025_3").set(
        {"year": 2025, "week": 3, "summary": "three", "timestamp": 1.0})
    fs.collection("weekly_recaps").document("2025_4").set(
        {"year": 2025, "week": 4, "summary": "four", "timestamp": 2.0})
    fs.collection("weekly_recaps").document("2024_1").set(
        {"year": 2024, "week": 1, "summary": "old", "timestamp": 0.5})


def test_dry_run_writes_nothing():
    fs = FakeFirestore(); _legacy(fs)
    report = mig.migrate(fs, write=False)
    assert report == {2024: [1], 2025: [3, 4]}
    assert "season_recaps" not in fs.store


def test_write_folds_weeks_and_is_idempotent_and_keeps_legacy():
    fs = FakeFirestore(); _legacy(fs)
    mig.migrate(fs, write=True)
    mig.migrate(fs, write=True)
    weeks = fs.store["season_recaps"]["2025"]["weeks"]
    assert set(weeks) == {"3", "4"} and weeks["4"]["summary"] == "four"
    assert len(fs.store["weekly_recaps"]) == 3


def test_does_not_overwrite_a_newer_published_week():
    fs = FakeFirestore(); _legacy(fs)
    fs.collection("season_recaps").document("2025").set(
        {"year": 2025, "weeks": {"3": {"summary": "published", "timestamp": 9.0}}})
    mig.migrate(fs, write=True)
    assert fs.store["season_recaps"]["2025"]["weeks"]["3"]["summary"] == "published"
    assert fs.store["season_recaps"]["2025"]["weeks"]["4"]["summary"] == "four"
```

- [ ] **Step 6: Implement** `scripts/migrate_weekly_recaps.py`:

```python
#!/usr/bin/env python3
"""Fold legacy weekly_recaps/{year}_{week} docs into season_recaps/{year}.

Dry run by default; pass --firestore to write. Idempotent; never deletes the
legacy docs and never overwrites a week that already exists on the season doc.
"""
import argparse
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def migrate(db, write: bool) -> dict:
    by_year = {}
    for doc in db.collection("weekly_recaps").stream():
        d = doc.to_dict() or {}
        if d.get("summary") and d.get("year") is not None and d.get("week") is not None:
            by_year.setdefault(int(d["year"]), {})[int(d["week"])] = d
    report = {y: sorted(w) for y, w in sorted(by_year.items())}
    if not write:
        return report
    for year, weeks in by_year.items():
        ref = db.collection("season_recaps").document(str(year))
        existing = (ref.get().to_dict() or {}).get("weeks", {})
        add = {str(w): {"summary": d["summary"], "timestamp": d.get("timestamp")}
               for w, d in weeks.items() if str(w) not in existing}
        if add:
            ref.set({"year": year, "weeks": add}, merge=True)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--firestore", action="store_true", help="write (default is a dry run)")
    args = ap.parse_args(argv)
    from services.db_service import require_db
    db = require_db()
    report = migrate(db, write=args.firestore)
    for year, weeks in report.items():
        print(f"{year}: weeks {weeks}")
    print("written" if args.firestore else "dry run only (use --firestore to write)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Local mirror.** Read `scripts/refresh_local_pkls.py` to see how JSON-per-season collections (e.g. `game_predictions`, `elo_history`) are mirrored, and add `season_recaps` the same way (writing `season_recaps_{year}.json` through `local_db_dir()` with `{"year", "weeks"}`); keep the `weekly_recaps` pkl entry. Add a test next to the existing refresh tests if one exists (find with `grep -rn refresh_local_pkls tests`), asserting a fake `season_recaps` doc becomes a JSON file.
- [ ] **Step 8:** Run `python -m pytest tests/test_season_recaps.py tests/test_migrate_weekly_recaps.py tests/test_local_db_isolation.py -q` and the refresh test. Commit: `feat: store weekly recaps one doc per season with cached reads and migration`.

---

## Task 3: Recap pages, nav entries, standings link

**Files:**
- Create: `routes/recap_routes.py`, `templates/recap.html`
- Modify: `main.py` (import + `app.include_router`), `templates/base.html:~152` (drawer), `static/js/main.js` (`_updateMoreDropdown`), `templates/wins_pool.html` (card link), `static/style.css`
- Test: `tests/test_recap_routes.py`, extend `tests/test_templates.py`

**Interfaces:**
- Consumes: `db.get_season_recaps`, `db.list_recap_weeks`, `db.get_weekly_recap`, filter `recap_html` (Tasks 1-2).
- Produces: `GET /recap` (redirect or empty state), `GET /recap/{year}/{week}`.

- [ ] **Step 1: Failing tests** `tests/test_recap_routes.py` (TestClient from `main.app`; patch `routes.recap_routes.db` functions and `routes.recap_routes.load_data`/`get_active_season`):

```python
from unittest.mock import patch
from starlette.testclient import TestClient
from main import app

client = TestClient(app)


def _patch(weeks_by_year, active=2026):
    return (
        patch("routes.recap_routes._active_season", return_value=active),
        patch("routes.recap_routes.db.list_recap_weeks", side_effect=lambda y: weeks_by_year.get(y, [])),
        patch("routes.recap_routes.db.get_weekly_recap",
              side_effect=lambda y, w: {"year": y, "week": w, "summary": "Hello <b>x</b>\nnext", "timestamp": 1.0}
              if w in weeks_by_year.get(y, []) else None),
    )


def test_recap_redirects_to_latest_week():
    p1, p2, p3 = _patch({2026: [3, 5]})
    with p1, p2, p3:
        r = client.get("/recap", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/recap/2026/5"


def test_recap_falls_back_to_prior_season():
    p1, p2, p3 = _patch({2025: [18]})
    with p1, p2, p3:
        r = client.get("/recap", follow_redirects=False)
    assert r.headers["location"] == "/recap/2025/18"


def test_recap_empty_state_when_none_exist():
    p1, p2, p3 = _patch({})
    with p1, p2, p3:
        r = client.get("/recap")
    assert r.status_code == 200 and "No recaps" in r.text


def test_recap_page_renders_escaped_body_and_week_links():
    p1, p2, p3 = _patch({2026: [3, 5]})
    with p1, p2, p3:
        r = client.get("/recap/2026/5")
    assert r.status_code == 200
    assert "Hello &lt;b&gt;x&lt;/b&gt;<br>next" in r.text
    assert 'href="/recap/2026/3"' in r.text


def test_unknown_week_is_friendly_not_500():
    p1, p2, p3 = _patch({2026: [3]})
    with p1, p2, p3:
        r = client.get("/recap/2026/9")
    assert r.status_code == 200 and "No recap" in r.text
```

Add to `tests/test_templates.py`: `base.html` source contains `href="/recap"` in the drawer; and `static/js/main.js` source contains `{ href: '/recap', label: 'Recaps' }`.

- [ ] **Step 2:** Run; expected FAIL.
- [ ] **Step 3: Implement** `routes/recap_routes.py`:

```python
"""routes/recap_routes.py -- weekly recap pages (/recap, /recap/{year}/{week})."""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

import services.db_service as db
from services.data_service import load_data, get_active_season, get_available_years
from routes.standings_routes import templates   # shared env: filters/globals already registered

router = APIRouter()


def _active_season() -> int:
    _, _, games, _, _, draft_results, rules = load_data()
    return get_active_season(games, draft_results, rules)


def _available_years(season: int) -> list:
    try:
        _, _, games, _, _, draft_results, rules = load_data()
        years = get_available_years(draft_results, games, rules)
    except Exception:
        years = []
    return sorted(set(list(years) + [season]), reverse=True)


@router.get("/recap")
async def recap_latest(request: Request):
    season = _active_season()
    for year in (season, season - 1):
        weeks = db.list_recap_weeks(year)
        if weeks:
            return RedirectResponse(f"/recap/{year}/{max(weeks)}")
    return templates.TemplateResponse(request, "recap.html", {
        "year": season, "week": None, "weeks": [], "recap": None,
        "available_years": _available_years(season), "current_year": season,
    })


@router.get("/recap/{year}/{week}")
async def recap_page(request: Request, year: int, week: int):
    recap = db.get_weekly_recap(year, week)
    return templates.TemplateResponse(request, "recap.html", {
        "year": year, "week": week, "weeks": db.list_recap_weeks(year),
        "recap": recap, "available_years": _available_years(year), "current_year": year,
    })
```

Check `get_available_years`' real argument order in `services/data_service.py` before relying on the call above (it is used as `get_available_years(all_draft_results, all_games, rules)` in `routes/standings_routes.py`). Register in `main.py`: `from routes.recap_routes import router as recap_router` and `app.include_router(recap_router)`.

- [ ] **Step 4: Template** `templates/recap.html` (read `templates/playoff_race.html` for the surrounding page structure and `templates/_macros.html::year_picker` usage, then):

```html
{% extends "base.html" %}
{% from "_macros.html" import year_picker %}
{% block title %}Recaps - NFL Wins Pool{% endblock %}
{% block content %}
<section class="recap-page">
  <h2>Weekly Recap</h2>
  {{ year_picker(available_years, year, '/recap', '') }}
  {% if weeks %}
  <nav class="recap-weeks" aria-label="Weeks with a recap">
    {% for w in weeks %}
    <a href="/recap/{{ year }}/{{ w }}" class="recap-week-chip{% if w == week %} active{% endif %}">Wk {{ w }}</a>
    {% endfor %}
  </nav>
  {% endif %}
  {% if recap %}
  <div class="recap-card-glass">
    <div class="recap-card-head">
      <span class="mono-pill"><span class="dot"></span>RECAP</span>
      <span class="recap-week-mono">Week {{ week }} · {{ year }}</span>
    </div>
    <div class="recap-content">{{ recap.summary | recap_html }}</div>
  </div>
  {% elif week %}
  <p class="recap-empty">No recap for week {{ week }} of {{ year }} yet.</p>
  {% else %}
  <p class="recap-empty">No recaps have been published yet.</p>
  {% endif %}
</section>
{% endblock %}
```

Confirm how `year_picker` builds URLs (`base_url` + `/{year}` + `url_suffix`) and that `/recap/2025` is not a route: the picker should link to `/recap/{year}` which does not exist. Therefore add `@router.get("/recap/{year}")` that redirects to that year's latest week (or the empty state page for that year). Add a test for it.
- [ ] **Step 5: Nav.** In `static/js/main.js::_updateMoreDropdown` add `{ href: '/recap', label: 'Recaps' },` right after the `Weekly Progress` entry. In `templates/base.html` drawer add `<a href="/recap"><i data-lucide="newspaper"></i> Recaps</a>` after the Weekly Progress link. In `templates/wins_pool.html` inside `.recap-card-glass` after `.recap-content` add `<a class="recap-readmore" href="/recap/{{ year }}/{{ current_week }}">Read full recap</a>`. CSS (tokens only) for `.recap-weeks`, `.recap-week-chip(.active)`, `.recap-empty`, `.recap-readmore` in `static/style.css` (model on `.recap-card-glass` and existing chip/pill styles; use `--btn-primary-bg` for the active chip, `--hairline` borders, `--ink`, `--surface-sunken`).
- [ ] **Step 6:** Run `python -m pytest tests/test_recap_routes.py tests/test_templates.py tests/test_theme.py tests/test_theme_sweep.py tests/test_standings_routes.py -q`; `pytest tests_e2e/test_nav_parity.py --collect-only -q`. Commit: `feat: recap pages with week picker, nav entries, and standings read-more link`.

---

## Task 4: Shared recap email builder and Publish endpoint + admin UI

**Files:**
- Modify: `services/email_service.py` (add builder), `routes/admin_routes.py` (extract helper, new route, reuse builder in `save_and_broadcast_recap`), `routes/models.py`, `scripts/generate_weekly_summary.py:16-35`, `templates/admin.html` (after `#recap-final-preview-container`, before the closing of `#recap-section`), `static/js/admin_main.js`, `static/js/api.js`
- Test: `tests/test_admin_routes.py` (new class `TestPublishRecap`), `tests/test_email_service.py`

**Interfaces:**
- Produces:
  - `email_service.build_recap_email_html(week: int, summary_text: str, footer_html: str = DEFAULT_RECAP_FOOTER) -> str`; `DEFAULT_RECAP_FOOTER = "This recap was generated by Gemini AI for the Wins Pool."` (matches the current inline text).
  - `POST /api/admin/recap/publish` body `{year?: int, week?: int, text: str, send_push: bool=false, send_email: bool=false}`; response `{"saved": true, "year", "week", "url": "/recap/{y}/{w}", "email": {"recipients": n} | null, "push": null}` (Task 7 fills `push`).
  - `admin_routes._resolve_recap_year_week(year, week) -> tuple[int, int] | None` (the defaulting logic currently inline in `preview_recap_prompt`, extracted verbatim; the preview route behavior and tests are unchanged).

- [ ] **Step 1: Failing tests.** In `tests/test_email_service.py`: `build_recap_email_html(3, "a <b>&")` contains `Weekly Recap: NFL Week 3`, contains `a &lt;b&gt;&amp;`, contains the default footer sentence, and uses a custom `footer_html` when given. In `tests/test_admin_routes.py` class `TestPublishRecap` (follow `TestPreviewRecapPromptDefaults` for the patch style of `load_data`, `get_active_season`, `get_most_recent_completed_week`):

```python
class TestPublishRecap:
    def _defaults(self):
        return (patch("routes.admin_routes._resolve_recap_year_week", return_value=(2026, 5)),)

    def test_publish_saves_trimmed_text_with_source(self, admin_token):
        with patch("routes.admin_routes._resolve_recap_year_week", return_value=(2026, 5)), \
             patch("routes.admin_routes.save_weekly_recap") as save:
            r = client.post("/api/admin/recap/publish", json={"text": "  Hello  "},
                            headers={"Authorization": admin_token})
        assert r.status_code == 200
        save.assert_called_once_with(2026, 5, "Hello", source="published")
        body = r.json()
        assert body["saved"] is True and body["url"] == "/recap/2026/5"
        assert body["email"] is None and body["push"] is None

    def test_blank_text_is_422(self, admin_token):
        with patch("routes.admin_routes._resolve_recap_year_week", return_value=(2026, 5)), \
             patch("routes.admin_routes.save_weekly_recap") as save:
            r = client.post("/api/admin/recap/publish", json={"text": "   "},
                            headers={"Authorization": admin_token})
        assert r.status_code == 422
        save.assert_not_called()

    def test_explicit_year_week_not_overridden(self, admin_token):
        with patch("routes.admin_routes.save_weekly_recap") as save:
            r = client.post("/api/admin/recap/publish",
                            json={"year": 2025, "week": 9, "text": "x"},
                            headers={"Authorization": admin_token})
        assert r.status_code == 200
        save.assert_called_once_with(2025, 9, "x", source="published")

    def test_no_completed_week_is_404(self, admin_token):
        with patch("routes.admin_routes._resolve_recap_year_week", return_value=None):
            r = client.post("/api/admin/recap/publish", json={"text": "x"},
                            headers={"Authorization": admin_token})
        assert r.status_code == 404

    def test_email_only_when_ticked(self, admin_token):
        with patch("routes.admin_routes._resolve_recap_year_week", return_value=(2026, 5)), \
             patch("routes.admin_routes.save_weekly_recap"), \
             patch("routes.admin_routes.recap_service.extract_weekly_data", return_value=({}, ["a@x.com"])), \
             patch("routes.admin_routes.email_service.send_weekly_recap_email") as send:
            r = client.post("/api/admin/recap/publish", json={"text": "x", "send_email": True},
                            headers={"Authorization": admin_token})
        assert r.json()["email"] == {"recipients": 1}
        send.assert_called_once()

    def test_requires_admin(self, auth_token):
        r = client.post("/api/admin/recap/publish", json={"text": "x"},
                        headers={"Authorization": auth_token})
        assert r.status_code in (401, 403)
```

- [ ] **Step 2:** Run; expected FAIL.
- [ ] **Step 3: Implement.** (a) `email_service.build_recap_email_html`: move the HTML template from `save_and_broadcast_recap` (lines ~373-387) into the function with `html.escape(summary_text)`, the `Weekly Recap: NFL Week {week}` heading kept exactly as today, and the footer paragraph taking `footer_html`. Replace the inline HTML in `save_and_broadcast_recap` with `email_service.build_recap_email_html(body.week, body.summary)`. In `scripts/generate_weekly_summary.py::build_recap_html`, delegate: footer is `f'This recap was generated by Gemini AI for the Wins Pool. View the full standings at <a href="{base_url}">{base_url}</a>'`; run `grep -rn build_recap_html tests` and keep any existing test passing (adapt only whitespace-insensitive assertions, never remove them). (b) Add `PublishRecapRequest` to `routes/models.py` (`year: Optional[int] = Field(None, ge=2000, le=2100)`, `week: Optional[int] = Field(None, ge=1, le=22)`, `text: str = Field(..., max_length=20000)`, `send_push: bool = False`, `send_email: bool = False`). (c) In `routes/admin_routes.py` extract `_resolve_recap_year_week(year, week)` from the Task-1 code in `preview_recap_prompt` (same body; returns `None` when no week can be determined), make `preview_recap_prompt` call it and keep its 404 text, then add:

```python
@router.post("/admin/recap/publish")
async def publish_recap(body: PublishRecapRequest, _: dict = Depends(require_admin)):
    """Admin: publish a finished recap (pasted text) to the app. No Gemini; email/push only if ticked."""
    text = (body.text or "").strip()
    if not text:
        return JSONResponse(status_code=422, content={"error": "Recap text is required."})
    resolved = _resolve_recap_year_week(body.year, body.week)
    if resolved is None:
        return JSONResponse(status_code=404, content={"error": "No completed games found; specify year and week."})
    year, week = resolved
    save_weekly_recap(year, week, text, source="published")
    email_info = None
    if body.send_email:
        _, emails = recap_service.extract_weekly_data(year, week)
        if emails:
            email_service.send_weekly_recap_email(
                emails, f"Week {week} Recap - Wins Pool",
                email_service.build_recap_email_html(week, text, footer_html="Published by the Wins Pool commissioner."))
        email_info = {"recipients": len(emails or [])}
    return JSONResponse(content={"saved": True, "year": year, "week": week,
                                 "url": f"/recap/{year}/{week}", "email": email_info, "push": None})
```

(When both `year` and `week` are given, `_resolve_recap_year_week` returns them unchanged without loading data.)
- [ ] **Step 4:** Run `python -m pytest tests/test_admin_routes.py tests/test_email_service.py -q` (and the generate_weekly_summary tests if present). Expected: PASS.
- [ ] **Step 5: UI.** In `templates/admin.html` add inside `#recap-section` after `#recap-final-preview-container`:

```html
<div id="recap-publish-box" style="border:1px solid var(--glass-border); padding:1rem; border-radius:8px; margin-top:1rem;">
  <h4 style="margin:0 0 .5rem 0;">Publish recap to the app</h4>
  <p style="font-size:.75rem; color:var(--text-secondary);">Paste your finished recap. Nothing is sent unless you tick a box.</p>
  <textarea id="recap-publish-text" style="width:100%; height:180px; background:var(--bg-elev-2); color:var(--ink); border:1px solid var(--glass-border); border-radius:4px; padding:.5rem; font-family:inherit; line-height:1.5; resize:vertical;"></textarea>
  <label style="display:block; margin-top:.5rem;"><input type="checkbox" id="recap-publish-push" checked> Send push notification</label>
  <label style="display:block;"><input type="checkbox" id="recap-publish-email"> Also email enrolled players</label>
  <button id="recap-publish-btn" class="btn-primary" style="margin-top:.75rem; width:100%;">Publish to app</button>
  <p id="recap-publish-result" style="font-size:.85rem; margin-top:.5rem;"></p>
</div>
```

In `static/js/api.js` add `publishRecap(playerId, year, week, text, sendPush, sendEmail)` posting `{playerId, year, week, text, send_push, send_email}` to `${API_BASE}/admin/recap/publish` (year/week sent as numbers, or omitted when blank). In `admin_main.js` add `publishRecap()` wired to `#recap-publish-btn` in the same place as the other recap listeners (~line 256): read the textarea and checkboxes, `confirm()` only when a box is ticked ("Publish and notify players?"), call `ApiService.publishRecap`, then set `#recap-publish-result` to `Published. View: <a href="/recap/Y/W">Week W recap</a>` plus email/push summaries using `textContent`/DOM nodes (no `innerHTML` with server strings except the constructed link via `createElement`). Reuse `#recap-year`/`#recap-week` values (blank allowed; server defaults).
- [ ] **Step 6:** `node --check static/js/admin_main.js`; add a Playwright check to `tests_e2e/test_admin_members_and_recap.py` (publish with both boxes unticked, then GET `/recap/{y}/{w}` shows the text) in the style of the existing recap test; confirm `--collect-only` works (the suite needs `E2E_*` env vars to run).
- [ ] **Step 7:** Commit: `feat: publish pasted recap to the app (no Gemini, email/push only if ticked)`.

---

## Task 5: Preference-aware push sends, saved push events, service-worker deep link

**Files:**
- Modify: `services/push_service.py`, `static/sw.js`
- Create: `services/push_events.py`
- Test: `tests/test_push_service.py` (extend; create if absent), `tests/test_push_events.py`, `tests/test_sw_push_js.py`

**Interfaces:**
- Produces:
  - `push_service._deliver(player_id, sub, title, body, url=None) -> "sent"|"failed"|"pruned"` (payload JSON gains `"url"` only when given).
  - `push_service.send_to_subscribers(build_message, *, pref=None, url=None) -> dict` with `{"counts": {"total","sent","failed","pruned","skipped"}, "messages": {player_id:int -> {"title","body","status"}}}`; `build_message(player_id: int) -> tuple[str, str] | None` (`None` skips that player, counted `skipped`); a player is also skipped when `pref` is set and `players.push_prefs[pref] is False`. Requires Firestore (`get_db() is None` returns empty counts and logs a warning); requires `is_configured()` else returns empty counts with a warning.
  - `push_service.broadcast_push_notification(title, body, *, pref=None, url=None) -> dict` returning exactly `{"total","sent","failed","pruned"}` (built on `send_to_subscribers`).
  - `push_events.record_push_event(event_id: str, kind: str, counts: dict, messages: dict | None = None, extra: dict | None = None) -> bool`, `get_push_event(event_id) -> dict | None`, `list_push_events(limit: int = 50) -> list[dict]` (newest first by `sent_at`, without `messages`), `push_event_exists(event_id) -> bool`. Collection `push_events`, fields `{kind, sent_at, counts, messages?, ...extra}`; all return falsy/empty and log when Firestore is unavailable.

- [ ] **Step 1: Failing tests.** In `tests/test_push_service.py` (check how existing tests there stub `get_db` and `_deliver`; reuse `FakeFirestore` for the players collection):

```python
def test_send_to_subscribers_respects_prefs_and_counts(monkeypatch):
    import services.push_service as ps
    from tests.fake_firestore import FakeFirestore
    fs = FakeFirestore()
    fs.collection("players").document("1").set({"push_subscription": {"endpoint": "e1"}})
    fs.collection("players").document("2").set({"push_subscription": {"endpoint": "e2"},
                                                "push_prefs": {"recap": False, "standings": True}})
    fs.collection("players").document("3").set({"fullName": "No Sub"})
    monkeypatch.setattr("services.db_service.get_db", lambda: fs)
    monkeypatch.setattr(ps, "_VAPID_PUBLIC", "pub"); monkeypatch.setattr(ps, "_VAPID_PRIVATE", "priv")
    calls = []
    monkeypatch.setattr(ps, "_deliver", lambda pid, sub, t, b, url=None: calls.append((pid, t, url)) or "sent")
    out = ps.send_to_subscribers(lambda pid: ("T", f"B{pid}"), pref="recap", url="/recap/2026/5")
    assert out["counts"]["total"] == 1 and out["counts"]["sent"] == 1 and out["counts"]["skipped"] == 1
    assert calls == [("1", "T", "/recap/2026/5")]
    assert out["messages"][1] == {"title": "T", "body": "B1", "status": "sent"}


def _fixture(monkeypatch):
    import services.push_service as ps
    from tests.fake_firestore import FakeFirestore
    fs = FakeFirestore()
    fs.collection("players").document("1").set({"push_subscription": {"endpoint": "e1"}})
    fs.collection("players").document("2").set({"push_subscription": {"endpoint": "e2"}})
    monkeypatch.setattr("services.db_service.get_db", lambda: fs)
    monkeypatch.setattr(ps, "_VAPID_PUBLIC", "pub")
    monkeypatch.setattr(ps, "_VAPID_PRIVATE", "priv")
    calls = []
    monkeypatch.setattr(ps, "_deliver", lambda pid, sub, t, b, url=None: calls.append(pid) or "sent")
    return ps, calls


def test_build_message_none_skips_player(monkeypatch):
    ps, calls = _fixture(monkeypatch)
    out = ps.send_to_subscribers(lambda pid: None if pid == 1 else ("T", "B"))
    assert out["counts"]["skipped"] == 1 and out["counts"]["sent"] == 1
    assert calls == ["2"] and 1 not in out["messages"]


def test_broadcast_return_keys_unchanged(monkeypatch):
    ps, calls = _fixture(monkeypatch)
    out = ps.broadcast_push_notification("t", "b")
    assert set(out) == {"total", "sent", "failed", "pruned"}
    assert out["total"] == 2 and out["sent"] == 2


def test_unconfigured_vapid_sends_nothing(monkeypatch):
    ps, calls = _fixture(monkeypatch)
    monkeypatch.setattr(ps, "_VAPID_PUBLIC", "")
    out = ps.send_to_subscribers(lambda pid: ("T", "B"))
    assert out["counts"]["total"] == 0 and calls == []


def test_failed_and_pruned_deliveries_are_counted(monkeypatch):
    ps, calls = _fixture(monkeypatch)
    statuses = iter(["failed", "pruned"])
    monkeypatch.setattr(ps, "_deliver", lambda pid, sub, t, b, url=None: next(statuses))
    out = ps.send_to_subscribers(lambda pid: ("T", "B"))
    assert out["counts"]["failed"] == 1 and out["counts"]["pruned"] == 1 and out["counts"]["sent"] == 0
In `tests/test_push_events.py` use `FakeFirestore` via `monkeypatch.setattr("services.db_service.get_db", ...)`: record then get returns counts/messages; `list_push_events` omits `messages` and orders newest first; `get_db() is None` makes record return `False` without raising. In `tests/test_sw_push_js.py` (node-skip pattern from `tests/test_nav_gating_js.py`): run a node script that reads `static/sw.js`, evaluates it with `vm.runInNewContext` against stubs for `self` (with `addEventListener` capturing handlers, `registration.showNotification` recording args, `location: {origin: 'https://app.test'}`) and `clients` (`matchAll` returning a configurable list of window stubs with `navigate`/`focus`, `openWindow` recording). Assert: the push handler passes `data: {url: '/recap/2026/5'}` to `showNotification`; click with an existing window calls `navigate('/recap/2026/5')` and not `openWindow`; click with no window calls `openWindow('/recap/2026/5')`; click with `url: 'https://evil.test/x'` opens `/`; click with no url opens `/`.
- [ ] **Step 2:** Run; expected FAIL.
- [ ] **Step 3: Implement `push_service`.** `_deliver(..., url=None)`: build `payload = {"title": title, "body": body}` and add `payload["url"] = url` when `url`; keep everything else. Add:

```python
def _player_doc_id_to_int(doc_id):
    try:
        return int(doc_id)
    except (TypeError, ValueError):
        return doc_id


def send_to_subscribers(build_message, *, pref=None, url=None) -> dict:
    counts = {"total": 0, "sent": 0, "failed": 0, "pruned": 0, "skipped": 0}
    messages = {}
    if not is_configured():
        logger.warning("push_service: VAPID not configured, nothing sent")
        return {"counts": counts, "messages": messages}
    from services.db_service import get_db
    db = get_db()
    if db is None:
        logger.warning("push_service: no database (local data mode), nothing sent")
        return {"counts": counts, "messages": messages}
    for doc in db.collection("players").stream():
        data = doc.to_dict() or {}
        sub = data.get("push_subscription")
        if not isinstance(sub, dict):
            continue
        prefs = data.get("push_prefs")
        if pref and isinstance(prefs, dict) and prefs.get(pref) is False:
            counts["skipped"] += 1
            continue
        pid = _player_doc_id_to_int(doc.id)
        msg = build_message(pid)
        if not msg:
            counts["skipped"] += 1
            continue
        title, body = msg
        counts["total"] += 1
        status = _deliver(doc.id, sub, title, body, url) if url else _deliver(doc.id, sub, title, body)
        counts[status] += 1
        messages[pid] = {"title": title, "body": body, "status": status}
    logger.info("push_service: send_to_subscribers pref=%s counts=%s", pref, counts)
    return {"counts": counts, "messages": messages}


def broadcast_push_notification(title: str, body: str, *, pref=None, url=None) -> dict:
    result = send_to_subscribers(lambda _pid: (title, body), pref=pref, url=url)
    c = result["counts"]
    return {"total": c["total"], "sent": c["sent"], "failed": c["failed"], "pruned": c["pruned"]}
```

Existing tests that monkeypatch `_deliver` with the 4-arg signature keep working because the 4-arg call is used when `url` is falsy. Note the old broadcast read `get_db()` inside the function; the new one imports it the same way, so patches on `services.db_service.get_db` keep working. Update `_deliver`'s existing call sites only as needed.
- [ ] **Step 4: Implement `services/push_events.py`:**

```python
"""services/push_events.py -- durable record of every notification batch sent."""
import logging
import time

logger = logging.getLogger(__name__)
_COLLECTION = "push_events"


def _db():
    from services.db_service import get_db
    return get_db()


def record_push_event(event_id, kind, counts, messages=None, extra=None) -> bool:
    db = _db()
    if db is None:
        logger.warning("push_events: no database, event %s not recorded", event_id)
        return False
    doc = {"kind": kind, "sent_at": time.time(), "counts": dict(counts)}
    if messages:
        doc["messages"] = {str(k): v for k, v in messages.items()}
    if extra:
        doc.update(extra)
    try:
        db.collection(_COLLECTION).document(event_id).set(doc)
        return True
    except Exception:
        logger.exception("push_events: failed to record %s", event_id)
        return False


def get_push_event(event_id):
    db = _db()
    if db is None:
        return None
    snap = db.collection(_COLLECTION).document(event_id).get()
    return {"id": event_id, **snap.to_dict()} if snap.exists else None


def push_event_exists(event_id) -> bool:
    db = _db()
    return bool(db is not None and db.collection(_COLLECTION).document(event_id).get().exists)


def list_push_events(limit: int = 50) -> list:
    db = _db()
    if db is None:
        return []
    rows = []
    for snap in db.collection(_COLLECTION).stream():
        d = snap.to_dict() or {}
        d.pop("messages", None)
        rows.append({"id": snap.id, **d})
    rows.sort(key=lambda r: r.get("sent_at", 0), reverse=True)
    return rows[:limit]
```

(The full collection stream is acceptable: at most about 40 docs per season; no pagination until that is false.)
- [ ] **Step 5: Implement `static/sw.js`** (replace the two handlers; keep icon/badge as today):

```js
self.addEventListener('push', event => {
    const data = event.data ? event.data.json() : { title: 'WinsPool', body: "You're on the clock!" };
    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            icon: '/static/fishbone.png',
            badge: '/static/fishbone.png',
            data: { url: data.url || '/' },
        })
    );
});

function _safeTarget(url) {
    try {
        const u = new URL(url || '/', self.location.origin);
        return u.origin === self.location.origin ? u.pathname + u.search + u.hash : '/';
    } catch (e) {
        return '/';
    }
}

self.addEventListener('notificationclick', event => {
    event.notification.close();
    const target = _safeTarget(event.notification.data && event.notification.data.url);
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
            for (const c of list) {
                if ('navigate' in c) {
                    return c.navigate(target).then(w => (w || c).focus());
                }
            }
            return clients.openWindow(target);
        })
    );
});
```

- [ ] **Step 6:** Run `python -m pytest tests/test_push_service.py tests/test_push_events.py tests/test_sw_push_js.py tests/test_admin_routes.py -q`. Expected: PASS (the admin tests monkeypatch `broadcast_push_notification` with a 2-arg lambda; the draft caller must not change). Commit: `feat: preference-aware push sends, saved push events, notification deep links`.

---

## Task 6: Notification preferences, player-page card, shared push client, standings nudge

**Files:**
- Modify: `services/push_service.py` (prefs helpers), `routes/api_routes.py` (2 routes), `routes/models.py`, `templates/base.html` (vapid meta), `main.py` (global `push_vapid_key`), `templates/player_profile.html`, `static/js/main.js`, `static/js/ios_push_hint.js` (export one helper only if needed), `static/style.css`, `templates/wins_pool.html` (load nudge)
- Create: `static/js/push_client.js`, `static/js/push_card.js`, `static/js/push_nudge.js`
- Test: `tests/test_push_prefs.py`, `tests/test_push_client_js.py`, extend `tests/test_templates.py`

**Interfaces:**
- Produces:
  - `push_service.get_push_prefs(player_id: int) -> {"recap": bool, "standings": bool}` (defaults true) and `push_service.set_push_prefs(player_id: int, recap: bool, standings: bool) -> bool` (writes `players/{id}.push_prefs`, invalidates the players cache with `_invalidate_players_cache()`), `push_service.has_subscription(player_id: int) -> bool`.
  - `GET /api/profile/push-status` -> `{"subscribed": bool, "prefs": {"recap": bool, "standings": bool}, "configured": bool}`; `POST /api/profile/push-prefs` body `{recap: bool, standings: bool}` -> `{"ok": true, "prefs": {...}}`. Both act on the authenticated caller only. Get the caller id the way the other `/api/profile/*` routes do (see `get_profile_portfolio`; the JWT claim is `sub`, cast to int).
  - JS `static/js/push_client.js` exports `pushSupportState({userAgent, platform, maxTouchPoints, standalone, hasServiceWorker, hasPushManager}) -> 'supported'|'ios-needs-install'|'unsupported'` (pure; reuses `isIosDevice` from `ios_push_hint.js`), `shouldShowPushNudge({support, subscribed, permission, dismissed}) -> boolean` (pure: true only when `support === 'supported'`, not subscribed, permission not `'denied'`, not dismissed), and `async subscribeToPush(playerId, vapidKey) -> 'granted'|'denied'|'error'` (the logic currently inside `main.js::initPushNotifications`: register `/sw.js`, `Notification.requestPermission()`, `pushManager.subscribe({userVisibleOnly: true, applicationServerKey: _urlBase64ToUint8Array(vapidKey)})`, POST to `/api/draft/push-subscribe` with `getAuthHeaders()`, report failures to `/api/push/client-error`).

- [ ] **Step 1: Failing tests.** `tests/test_push_prefs.py`: with `FakeFirestore` patched as `services.db_service.get_db`, `set_push_prefs(1, True, False)` then `get_push_prefs(1) == {"recap": True, "standings": False}`; missing prefs default to both true; `has_subscription` true only with a dict `push_subscription`; route tests with `TestClient(app)` (follow how `tests/test_api.py::_bearer` builds auth): `GET /api/profile/push-status` returns the shape above for the caller; `POST /api/profile/push-prefs` persists for the caller and 422s on a non-boolean body; unauthenticated gets 401. `tests/test_push_client_js.py` (node, ES module import via a `.mjs` wrapper or `node --input-type=module -e`): `pushSupportState` for desktop Chrome with PushManager -> `supported`; iPhone UA not standalone -> `ios-needs-install`; iPhone standalone with PushManager -> `supported`; Firefox without PushManager -> `unsupported`; `shouldShowPushNudge` truth table (supported/unsubscribed/default/not dismissed -> true; each of subscribed, denied, dismissed, unsupported, ios-needs-install -> false).
- [ ] **Step 2:** Run; expected FAIL.
- [ ] **Step 3: Backend.** Add the three helpers to `push_service.py`:

```python
def get_push_prefs(player_id: int) -> dict:
    from services.db_service import get_db
    db = get_db()
    prefs = {}
    if db is not None:
        snap = db.collection("players").document(str(player_id)).get()
        prefs = ((snap.to_dict() or {}).get("push_prefs") or {}) if snap.exists else {}
    return {"recap": prefs.get("recap") is not False, "standings": prefs.get("standings") is not False}


def set_push_prefs(player_id: int, recap: bool, standings: bool) -> bool:
    from services.db_service import get_db
    db = get_db()
    if db is None:
        return False
    db.collection("players").document(str(player_id)).update(
        {"push_prefs": {"recap": bool(recap), "standings": bool(standings)}})
    try:
        _invalidate_players_cache()
    except Exception:
        logger.warning("push_service: players cache invalidation failed after prefs save", exc_info=True)
    return True


def has_subscription(player_id: int) -> bool:
    try:
        return isinstance(_get_push_subscription(player_id), dict)
    except Exception:
        logger.warning("push_service: subscription lookup failed for %s", player_id, exc_info=True)
        return False
```

(The `FakeFirestore` in Task 2 implements `update`; if `players` doc is absent in the fake, create it in the test first.) Add `PushPrefsRequest(recap: bool, standings: bool)` to `routes/models.py` and the two routes to `routes/api_routes.py` next to `push_subscribe`, returning `{"ok": True, "prefs": ...}`; `set_push_prefs` returning `False` becomes `server_error()` like `push_subscribe` does today (explicit domain branch, not a generic wrapper).
- [ ] **Step 4: vapid meta.** In `main.py` add `t.env.globals['push_vapid_key'] = os.environ.get("VAPID_PUBLIC_KEY", "")` inside the template loop. In `templates/base.html` `<head>` add `{% if push_vapid_key and not vapid_public_key %}<meta name="vapid-public-key" content="{{ push_vapid_key }}">{% endif %}`. Test (in `tests/test_templates.py`): with `VAPID_PUBLIC_KEY` set before import is awkward, so instead set `main.standings_templates.env.globals['push_vapid_key']='K'` via monkeypatch and assert `GET /` contains `name="vapid-public-key" content="K"` exactly once; the draft page keeps its own tag.
- [ ] **Step 5: JS.** Create `push_client.js` with the pure functions (written to run under node without a DOM: no top-level `window`/`document` use) and move `_urlBase64ToUint8Array` and the subscribe logic out of `main.js` into it (`subscribeToPush`). In `main.js::initPushNotifications` keep its current guards and call `subscribeToPush(this.user.playerId, vapidKey)` so draft-page behavior is unchanged (including the iOS hint call added earlier). Keep an exported `_urlBase64ToUint8Array` import wherever `main.js` still references it.
- [ ] **Step 6: Player-page card.** In `templates/player_profile.html` inside `#own-page-only` add `<section id="notifications" class="card-glass push-card" hidden>` with: a status line `#push-status`, an Enable button `#push-enable-btn`, an iOS-guidance paragraph `#push-ios-hint` (hidden by default; text from `IOS_HINT_TEXT`), and two checkboxes `#push-pref-recap`, `#push-pref-standings` with labels "Weekly recap" and "Weekly standings" (draft alerts note: "Draft turn alerts stay on for anyone subscribed"). Add `<script type="module" src="{{ static_url('js/push_card.js') }}"></script>` at the existing script location. `push_card.js` (module): wait for the existing own-page reveal (check `document.getElementById('own-page-only').hidden === false` after DOMContentLoaded; the reveal happens in an inline script at ~line 421, so run on `DOMContentLoaded` plus a `MutationObserver` on `hidden`), compute `pushSupportState` from `navigator`, fetch `/api/profile/push-status` with `getAuthHeaders()`, render: unsupported -> message; ios-needs-install -> show `#push-ios-hint`; supported + not subscribed -> show Enable (click calls `subscribeToPush`, then refetch status; `denied` shows "Notifications are blocked in your browser settings"); subscribed -> checkboxes enabled and a POST to `/api/profile/push-prefs` on change. Add `.push-card` CSS (tokens only).
- [ ] **Step 7: Nudge.** `static/js/push_nudge.js` (module, loaded from `templates/wins_pool.html` scripts block): if `localStorage[nfl_wins_my_player_id]` is set, call `/api/profile/push-status`; compute `shouldShowPushNudge({support, subscribed, permission: Notification.permission, dismissed})` with `dismissed` read from `localStorage['nfl_wins_push_nudge_dismissed']` in try/catch; if true insert a dismissible `.push-nudge` banner above the standings content: text "Get notified when the weekly recap and standings are ready", a link `Turn on` to `/player/{myId}#notifications`, and a dismiss button (`aria-label="Dismiss"`) that stores the flag. Never calls `Notification.requestPermission`. CSS tokens only; keep it out of the iOS banner's way (it never shows for `ios-needs-install`).
- [ ] **Step 8:** Add template tests (card markup ids present in `player_profile.html`; `wins_pool.html` loads `push_nudge.js`). Run `python -m pytest tests/test_push_prefs.py tests/test_push_client_js.py tests/test_templates.py tests/test_theme.py tests/test_theme_sweep.py tests/test_ios_push_banner_js.py tests/test_nav_gating_js.py -q` and `node --check` each new JS file. Manually load `/player/<me>` locally if you can; note if not. Commit: `feat: notification preferences card, shared push client, standings nudge`.

---

## Task 7: Recap push on publish, admin push-events routes and list

**Files:**
- Modify: `routes/admin_routes.py` (publish route push branch + 2 read routes), `templates/admin.html` (Sent notifications list), `static/js/admin_main.js`, `static/js/api.js`
- Test: `tests/test_admin_routes.py` (class `TestPublishRecapPush`, `TestPushEventsRoutes`)

**Interfaces:**
- Consumes: `push_service.send_to_subscribers`, `push_events.record_push_event/get_push_event/list_push_events` (Task 5), the publish route (Task 4).
- Produces: `GET /api/admin/push-events` -> `{"events": [{id, kind, sent_at, counts}]}`; `GET /api/admin/push-events/{event_id}` -> the full record (404 when unknown, 422 when `event_id` does not match `^\d{4}_w\d{2}_(recap|standings)$`). Publish response `push` becomes `{"sent": n, "failed": n, "pruned": n, "skipped": n}` when `send_push` is true, else `null`; event id `f"{year}_w{week:02d}_recap"`.
- Helper: `admin_routes._first_sentence(text: str, limit: int = 120) -> str`.

- [ ] **Step 1: Failing tests:**

```python
def test_first_sentence_truncates_and_strips():
    from routes.admin_routes import _first_sentence
    assert _first_sentence("A big week. Then more.") == "A big week."
    assert _first_sentence("x" * 300).endswith("...") and len(_first_sentence("x" * 300)) <= 120
    assert _first_sentence("**Bold** start\nsecond line") == "Bold start"


class TestPublishRecapPush:
    def test_push_sent_when_ticked_and_event_recorded(self, admin_token):
        counts = {"total": 2, "sent": 2, "failed": 0, "pruned": 0, "skipped": 1}
        with patch("routes.admin_routes._resolve_recap_year_week", return_value=(2026, 5)), \
             patch("routes.admin_routes.save_weekly_recap"), \
             patch("routes.admin_routes.push_service.send_to_subscribers",
                   return_value={"counts": counts, "messages": {}}) as send, \
             patch("routes.admin_routes.push_events.record_push_event", return_value=True) as rec:
            r = client.post("/api/admin/recap/publish",
                            json={"text": "Big week. More.", "send_push": True},
                            headers={"Authorization": admin_token})
        assert r.json()["push"] == {"sent": 2, "failed": 0, "pruned": 0, "skipped": 1}
        kw = send.call_args.kwargs
        assert kw["pref"] == "recap" and kw["url"] == "/recap/2026/5"
        assert rec.call_args.args[0] == "2026_w05_recap"

    def test_push_failure_never_loses_the_saved_recap(self, admin_token):
        with patch("routes.admin_routes._resolve_recap_year_week", return_value=(2026, 5)), \
             patch("routes.admin_routes.save_weekly_recap") as save, \
             patch("routes.admin_routes.push_service.send_to_subscribers", side_effect=RuntimeError("boom")):
            r = client.post("/api/admin/recap/publish", json={"text": "x", "send_push": True},
                            headers={"Authorization": admin_token})
        assert r.status_code == 200 and r.json()["saved"] is True
        assert r.json()["push"] == {"error": "push failed; recap was saved"}
        save.assert_called_once()

    def test_no_push_when_unticked(self, admin_token):
        with patch("routes.admin_routes._resolve_recap_year_week", return_value=(2026, 5)), \
             patch("routes.admin_routes.save_weekly_recap"), \
             patch("routes.admin_routes.push_service.send_to_subscribers") as send:
            r = client.post("/api/admin/recap/publish", json={"text": "x"},
                            headers={"Authorization": admin_token})
        send.assert_not_called() and r.json()["push"] is None


class TestPushEventsRoutes:
    def test_list_and_detail_admin_only(self, admin_token, auth_token):
        ev = {"id": "2026_w05_recap", "kind": "recap", "sent_at": 1.0, "counts": {"sent": 2}}
        with patch("routes.admin_routes.push_events.list_push_events", return_value=[ev]), \
             patch("routes.admin_routes.push_events.get_push_event", return_value={**ev, "messages": {}}):
            assert client.get("/api/admin/push-events", headers={"Authorization": admin_token}).json()["events"] == [ev]
            assert client.get("/api/admin/push-events/2026_w05_recap", headers={"Authorization": admin_token}).status_code == 200
            assert client.get("/api/admin/push-events", headers={"Authorization": auth_token}).status_code in (401, 403)

    def test_bad_event_id_is_422_and_unknown_is_404(self, admin_token):
        h = {"Authorization": admin_token}
        assert client.get("/api/admin/push-events/../../etc", headers=h).status_code in (404, 422)
        assert client.get("/api/admin/push-events/bad_id", headers=h).status_code == 422
        with patch("routes.admin_routes.push_events.get_push_event", return_value=None):
            assert client.get("/api/admin/push-events/2026_w05_recap", headers=h).status_code == 404
```

Note the "push failure" branch is a deliberate domain-level `except Exception` (the recap is already saved and must not be lost); it is not the banned generic 500 boilerplate and must log with `logger.exception`.
- [ ] **Step 2:** Run; expected FAIL.
- [ ] **Step 3: Implement.** In `routes/admin_routes.py` import `from services import push_service, push_events` and `from starlette.concurrency import run_in_threadpool`; add:

```python
import re as _re

def _first_sentence(text: str, limit: int = 120) -> str:
    plain = _re.sub(r"[*_#>`]+", "", text or "").strip().replace("\r", "")
    plain = plain.split("\n", 1)[0].strip()
    m = _re.search(r"(?<=[.!?])\s", plain)
    sentence = plain[:m.start()] if m else plain
    return sentence if len(sentence) <= limit else sentence[: limit - 3].rstrip() + "..."
```

In `publish_recap` after the email block add the push branch:

```python
    push_info = None
    if body.send_push:
        try:
            title = f"Week {week} recap is ready"
            blurb = _first_sentence(text)
            result = await run_in_threadpool(
                push_service.send_to_subscribers,
                lambda _pid: (title, blurb), pref="recap", url=f"/recap/{year}/{week}")
            c = result["counts"]
            push_info = {k: c[k] for k in ("sent", "failed", "pruned", "skipped")}
            await run_in_threadpool(push_events.record_push_event,
                                    f"{year}_w{week:02d}_recap", "recap", c, None,
                                    {"year": year, "week": week, "title": title, "body": blurb})
        except Exception:
            logger.exception("publish_recap: push failed after the recap was saved")
            push_info = {"error": "push failed; recap was saved"}
```

and return `"push": push_info`. Add the two GET routes (`/admin/push-events`, `/admin/push-events/{event_id}`) with the id regex check returning 422 `{"error": "Invalid event id."}` and 404 for unknown. Update the Task-4 tests only where they asserted `push is None` for `send_push` false (they still hold).
- [ ] **Step 4: UI.** Add a "Sent notifications" block to `#recap-section` (`<table id="push-events-table">` with Kind, Week/Date, Sent, Failed columns and a "Details" button per row that fetches the detail route and shows `messages` (title/body/status per player id) in a `<pre id="push-event-detail">` via `textContent`), loaded when the tab opens (`initRecapTab`) and after a publish. `api.js`: `fetchPushEvents(playerId)` and `fetchPushEvent(playerId, id)`.
- [ ] **Step 5:** Run `python -m pytest tests/test_admin_routes.py -q`, `node --check static/js/admin_main.js`. Commit: `feat: recap-ready push on publish with saved event records and admin list`.

---

## Task 8: Weekly personal standings push

**Files:**
- Create: `services/standings_push_service.py`, `scripts/send_weekly_standings_push.py`
- Modify: `scripts/run_cron.py` (STEPS), `DEPLOY.md`
- Test: `tests/test_standings_push_service.py`, `tests/test_send_weekly_standings_push.py`, extend any existing run_cron/STEPS test (`grep -rn "run_cron" tests`)

**Interfaces:**
- Produces in `services.standings_push_service`:
  - `week_is_complete(games, season: int, week: int) -> bool` (REG games of that season/week: at least one, every `result` notna and not the `UNDRAFTED_SENTINEL`; look up that constant in `services/constants.py` or where `compute_team_records` imports it).
  - `latest_complete_week(games, season: int) -> int | None`.
  - `standings_as_of(games, season: int, through_week: int) -> pandas.DataFrame` with the exact columns of `scripts/daily_nfl_sync.py::compute_standings` for completed REG games with `week <= through_week`.
  - `pool_ranking(standings, draft_results, players, season, games=None) -> list[dict]` of `{"playerId": int, "fullName": str, "rank": int, "wins": int}` in rank order (row order of `analysis.calculate_wins_pool_standings`, which already applies `apply_tiebreakers`; `wins` is the sum of that frame's `wins1..wins3` columns).
  - `ordinal(n: int) -> str` ("1st", "2nd", "3rd", "4th", "11th", "12th", "21st").
  - `build_messages(week: int, current: list[dict], previous: list[dict] | None) -> dict[int, tuple[str, str]]`.
- `scripts/send_weekly_standings_push.py` CLI: `--dry-run`, `--force-week N`, `--season S`, `--force` (resend even if the event exists); event id `f"{season}_w{week:02d}_standings"`; exit 0 in every skip case (no VAPID, draft incomplete, nothing complete, already sent).

- [ ] **Step 1: Failing tests** `tests/test_standings_push_service.py` (pure pandas frames; no Firestore):

```python
import pandas as pd
from services import standings_push_service as sp


def _games(rows):
    return pd.DataFrame(rows, columns=["season", "week", "game_type", "home_team", "away_team",
                                       "home_score", "away_score", "result"])


def test_ordinal():
    assert [sp.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 112)] == \
        ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "112th"]


def test_week_is_complete_requires_every_game_final():
    g = _games([[2026, 1, "REG", "A", "B", 10, 3, 7],
                [2026, 1, "REG", "C", "D", None, None, None]])
    assert sp.week_is_complete(g, 2026, 1) is False
    g.loc[1, ["home_score", "away_score", "result"]] = [1, 2, -1]
    assert sp.week_is_complete(g, 2026, 1) is True
    assert sp.week_is_complete(g, 2026, 2) is False        # no games: not complete


def test_latest_complete_week_ignores_unfinished_later_week():
    g = _games([[2026, 1, "REG", "A", "B", 10, 3, 7],
                [2026, 2, "REG", "A", "C", 10, 3, 7],
                [2026, 3, "REG", "A", "D", None, None, None]])
    assert sp.latest_complete_week(g, 2026) == 2
    assert sp.latest_complete_week(_games([]), 2026) is None


def test_postponed_game_blocks_its_week():
    g = _games([[2026, 4, "REG", "A", "B", 10, 3, 7],
                [2026, 4, "REG", "C", "D", None, None, None]])
    assert sp.latest_complete_week(g, 2026) is None


def test_standings_as_of_matches_daily_sync_compute_standings():
    from scripts.daily_nfl_sync import compute_standings
    g = _games([[2026, 1, "REG", "A", "B", 24, 10, 14],
                [2026, 1, "REG", "C", "D", 17, 17, 0],
                [2026, 2, "REG", "A", "C", 3, 20, -17],
                [2026, 2, "REG", "B", "D", 9, 6, 3]])
    got = sp.standings_as_of(g, 2026, 2).sort_values("team").reset_index(drop=True)
    want = compute_standings(g).sort_values("team").reset_index(drop=True)
    pd.testing.assert_frame_equal(got[want.columns], want, check_dtype=False)
    wk1 = sp.standings_as_of(g, 2026, 1)
    assert int(wk1.loc[wk1.team == "A", "wins"].iloc[0]) == 1
    assert int(wk1.loc[wk1.team == "C", "ties"].iloc[0]) == 1


def test_build_messages_variants():
    cur = [{"playerId": 1, "fullName": "Sam Lee", "rank": 1, "wins": 14},
           {"playerId": 2, "fullName": "Ann Ray", "rank": 2, "wins": 13},
           {"playerId": 3, "fullName": "Bo Cox", "rank": 3, "wins": 9}]
    prev = [{"playerId": 2, "fullName": "Ann Ray", "rank": 1, "wins": 11},
            {"playerId": 1, "fullName": "Sam Lee", "rank": 3, "wins": 12},
            {"playerId": 3, "fullName": "Bo Cox", "rank": 3, "wins": 9}]
    m = sp.build_messages(6, cur, prev)
    assert m[1] == ("Week 6 standings", "You moved up to 1st (14 wins, +2). You lead the pool.")
    assert m[2] == ("Week 6 standings", "You dropped to 2nd (13 wins, +2). Leader: Sam.")
    assert m[3] == ("Week 6 standings", "You held 3rd (9 wins, +0). Leader: Sam.")


def test_build_messages_first_week_has_no_delta():
    cur = [{"playerId": 1, "fullName": "Sam Lee", "rank": 1, "wins": 3},
           {"playerId": 2, "fullName": "Ann Ray", "rank": 2, "wins": 2}]
    m = sp.build_messages(1, cur, None)
    assert m[2] == ("Week 1 standings", "You are 2nd with 2 wins. Leader: Sam.")
    assert m[1] == ("Week 1 standings", "You are 1st with 3 wins. You lead the pool.")


def test_pool_ranking_orders_by_total_wins():
    standings = pd.DataFrame([
        {"season": 2026, "team": t, "wins": w, "losses": 0, "ties": 0,
         "scored": 10.0 * w, "allowed": 5.0, "net": 10.0 * w - 5.0, "pct": 1.0}
        for t, w in [("A", 3), ("B", 2), ("C", 1), ("D", 0), ("E", 2), ("F", 1)]])
    draft_results = pd.DataFrame([
        {"season": 2026, "playerId": 1, "team": "A", "draftPick": 1},
        {"season": 2026, "playerId": 1, "team": "D", "draftPick": 4},
        {"season": 2026, "playerId": 1, "team": "C", "draftPick": 5},
        {"season": 2026, "playerId": 2, "team": "B", "draftPick": 2},
        {"season": 2026, "playerId": 2, "team": "E", "draftPick": 3},
        {"season": 2026, "playerId": 2, "team": "F", "draftPick": 6},
    ])
    players = pd.DataFrame([{"playerId": 1, "fullName": "Sam Lee"},
                            {"playerId": 2, "fullName": "Ann Ray"}])
    ranking = sp.pool_ranking(standings, draft_results, players, 2026)
    assert [r["playerId"] for r in ranking] == [2, 1]          # 5 wins beats 4 wins
    assert [r["rank"] for r in ranking] == [1, 2]
    assert [r["wins"] for r in ranking] == [5, 4]
If `calculate_wins_pool_standings`/`apply_tiebreakers` need extra columns on these frames (check an existing test: `grep -rn calculate_wins_pool_standings tests`), add them to the fixture rather than loosening the assertions.
- [ ] **Step 2:** Run; expected FAIL.
- [ ] **Step 3: Implement** `services/standings_push_service.py`. For `standings_as_of`, read `scripts/daily_nfl_sync.py::compute_standings` (lines ~97-150) fully and reproduce its per-team math exactly (same columns, same `pct`/`net` formulas, same row filtering) on `games` restricted to `season`, `game_type == "REG"`, `week <= through_week`; the equivalence test above is the contract (it compares against `compute_standings` on the same frame; do not import the script from the service, duplicate the logic). Implementations of the pure helpers:

```python
def ordinal(n: int) -> str:
    n = int(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def week_is_complete(games, season, week) -> bool:
    wk = games[(games["season"] == season) & (games["week"] == week)]
    if "game_type" in wk.columns:
        wk = wk[wk["game_type"] == "REG"]
    if wk.empty:
        return False
    res = wk["result"]
    return bool(res.notna().all() and (res != UNDRAFTED_SENTINEL).all())


def latest_complete_week(games, season):
    reg = games[games["season"] == season]
    if "game_type" in reg.columns:
        reg = reg[reg["game_type"] == "REG"]
    for week in sorted(reg["week"].dropna().astype(int).unique(), reverse=True):
        if week_is_complete(games, season, week):
            return int(week)
    return None
```

`build_messages` rules (first name via `services.utils.abbreviate_player_name`): leader = rank-1 entry of `current`; title `f"Week {week} standings"`; per player with `previous` present for that player: `delta = prev_rank - rank`; verb `moved up to {ordinal}` (delta > 0), `dropped to {ordinal}` (delta < 0), `held {ordinal}` (0); body `f"You {verb} ({wins} wins, +{gained}). "` + (`"You lead the pool."` if rank == 1 else `f"Leader: {leader_first}."`) where `gained = wins - prev_wins`; with no previous (week 1 or player missing): `f"You are {ordinal} with {wins} wins. "` plus the same leader sentence. Return `{playerId: (title, body)}`.
- [ ] **Step 4:** Run the service tests; green. Commit: `feat: weekly standings push message builder and week completion helpers`.
- [ ] **Step 5: Script tests** `tests/test_send_weekly_standings_push.py`. Structure the script so `main(argv)` calls small injectable functions (`_load()`; `push_service.send_to_subscribers`; `push_events.push_event_exists/record_push_event`) which the tests monkeypatch. Cases: (a) dry run prints each message, calls neither send nor record; (b) sends once and records `f"{season}_w{week:02d}_standings"` with the messages map; (c) second run with `push_event_exists` true sends nothing; `--force` sends again; (d) draft incomplete exits 0 without sending; (e) no complete week exits 0; (f) `push_service.is_configured()` false exits 0 with a warning; (g) week 1 passes `previous=None` to `build_messages`; (h) `send_to_subscribers` raising returns exit code 0 and does not write the event (so a retry can resend) but logs the exception.
- [ ] **Step 6: Implement the script.** Skeleton (fill `_load()` with the same calls as `routes/standings_routes.py::wins_pool_by_year`: `load_data()` returns `all_st, teams, all_games, players, draft_order, all_draft_results, rules`; use `services.draft_state.draft_is_complete(season, draft_results, draft_order, rules)` and `get_active_season(all_games, all_draft_results, rules)`; filter with `services.utils.filter_season`):

```python
#!/usr/bin/env python3
"""Send each opted-in player one personal standings push after a week completes.

Runs as a non-required step of winspool-sync-daily (scripts/run_cron.py), after
daily_nfl_sync.py. Safe to rerun: an event record per (season, week) prevents
duplicate sends. Best-effort: never exits non-zero for a skip or a send failure.
"""
import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
log = logging.getLogger("send_weekly_standings_push")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-week", type=int)
    ap.add_argument("--season", type=int)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    if not args.dry_run:
        from services.db_service import require_db
        require_db()                      # forces USE_LOCAL_DATA=False before db-using imports
    from services import push_service, push_events, standings_push_service as sp
    ...
```

Implement the flow: load data; season = `args.season or get_active_season(...)`; if the draft is not complete -> log and return 0; week = `args.force_week or sp.latest_complete_week(games, season)`; if None -> return 0; `event_id`; if not dry run and not `--force` and `push_events.push_event_exists(event_id)` -> return 0; if not dry run and `not push_service.is_configured()` -> warn, return 0; `current = sp.pool_ranking(sp.standings_as_of(games, season, week), draft_results, players, season, games)`; `previous = sp.pool_ranking(sp.standings_as_of(games, season, week - 1), ...) if week > 1 else None`; `messages = sp.build_messages(week, current, previous)`; dry run: print `playerId: title - body` lines and return 0; else try `result = push_service.send_to_subscribers(lambda pid: messages.get(pid), pref="standings", url=f"/wins-pool/{season}")`, then `push_events.record_push_event(event_id, "standings", result["counts"], result["messages"], {"season": season, "week": week})`; on any exception `log.exception(...)` and return 0 without recording.
- [ ] **Step 7: Cron step.** Append to `STEPS` in `scripts/run_cron.py`: `{'name': 'Weekly Standings Push', 'script': SCRIPTS_DIR / 'send_weekly_standings_push.py', 'required': False}` after the NFL Data Sync step; update any existing test that pins the STEPS list (additively). `DEPLOY.md`: add the one-time commands (replace `<JOB_SA>` with the job's service account, found via `gcloud run jobs describe winspool-sync-daily --region=us-east1 --format="value(spec.template.spec.template.spec.serviceAccountName)"`):

```
gcloud run jobs update winspool-sync-daily --region=us-east1 --project=fishbone-wins-pool \
  --update-env-vars VAPID_PUBLIC_KEY=<public key>,VAPID_CLAIMS_EMAIL=<mailto:you@example.com> \
  --update-secrets VAPID_PRIVATE_KEY=vapid-private-key:latest
gcloud secrets add-iam-policy-binding vapid-private-key --project=fishbone-wins-pool \
  --member=serviceAccount:<JOB_SA> --role=roles/secretmanager.secretAccessor
```

State that `deploy.ps1` only swaps images, so this is one-time, and that without it the step logs a warning and exits 0.
- [ ] **Step 8:** Run `python -m pytest tests/test_standings_push_service.py tests/test_send_weekly_standings_push.py tests/test_local_db_isolation.py -q` plus the cron/job-runner tests. Commit: `feat: weekly personal standings push step in the daily sync job`.

---

## Task 9: Documentation and final verification

- [ ] **Step 1: Docs.** `docs/api_endpoints.md` (publish, push-status, push-prefs, push-events routes, `/recap` pages, preview echo unchanged); `docs/database.md` (`season_recaps` section with the weeks map, `weekly_recaps` marked legacy and migrated by `scripts/migrate_weekly_recaps.py`, `push_events`, `players.push_prefs`); `docs/reference_manual.md` (updated recap function signatures: `get_season_recaps`, `list_recap_weeks`, `save_weekly_recap(..., source)`); `CLAUDE.md` (Module Layout: `recap_routes.py`, `recap_render.py`, `push_events.py`, `standings_push_service.py`, `push_client.js`/`push_card.js`/`push_nudge.js`; Data Flow table rows for `season_recaps` and `push_events`; Scheduled Jobs row for `winspool-sync-daily` gains the standings-push step; one line that recap text is stored as written and rendered through `recap_html`). `docs/frontend.md` is gitignored: edit locally, do not `git add`.
- [ ] **Step 2: Cache comment.** Update the `services/cache_service.py` comment (~468-480) that says recaps have no cache domain: recaps now use a read-through TTL cache inside `db_service` (not a cache domain).
- [ ] **Step 3: Verification** (use superpowers:verification-before-completion): `python -m pytest tests/ -n auto -q` (compare any failure with the known env-failing list; none are expected), `python -m pytest tests/test_local_db_isolation.py -q`, `node --check` on every new/changed JS file, and `pytest tests_e2e/test_nav_parity.py tests_e2e/test_admin_members_and_recap.py --collect-only -q`. State plainly which e2e/browser checks were not run.
- [ ] **Step 4:** `graphify update .`; commit `docs: recap page, publish flow, and weekly push documentation`.
- [ ] **Step 5:** After the owner approves, run the one-time production steps in this order: deploy the web service and job images (`deploy.ps1`), `python scripts/migrate_weekly_recaps.py` (dry run), then `--firestore`, then the Task 8 `gcloud` commands, then `python scripts/send_weekly_standings_push.py --dry-run` to review the first week's messages before the next real Tuesday run.

## Self-Review Notes

- Spec coverage: storage/cache/migration (T2), pages/nav/standings link/render (T1, T3), publish + shared email builder + footer param (T4), push service/prefs/events/SW deep link (T5), opt-in card + nudge + shared client (T6), recap push + admin event list (T4, T7), weekly standings push + cron + deploy (T8), docs and legacy touchpoints (T9). Rendering-time sanitizing and plain-text storage follow the spec's existing-code section.
- Deviations to confirm: TTL-only freshness (no cross-instance signal); `FakeFirestore` helper added under `tests/`; `year_picker` links need an extra `/recap/{year}` route.
- Known risks: `standings_as_of` must reproduce `compute_standings` exactly (equivalence test is the guard); `FieldFilter` attribute names in the fake; the player-page reveal timing for `push_card.js`.
