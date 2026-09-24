# Smart Live Scores and Real-Time Standings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the `winspool-live-scores` job doing full work outside NFL game windows, stop unplayed games from being recorded as ties, and let the standings page refresh itself live.

**Architecture:** A pure, injectable `is_live_score_window_active()` in `scripts/sync_live_scores.py` gates `main()` (fast exit, `--force` override). `sync_live_scores_to_df()` gains a status guard so `STATUS_SCHEDULED` rows are never written. A new pure builder in `services/live_standings_service.py` shapes the payload for a new public `GET /api/live-standings`; a new `static/js/standings_refresh.js` polls it every 30s (paused while `document.hidden`) and patches the existing server-rendered DOM.

**Tech Stack:** Python, FastAPI, pandas, pytest, vanilla JS, Jinja2.

**Spec:** `docs/superpowers/specs/2026-09-20-smart-live-scores-and-realtime-standings-design.md` (subsumes `docs/superpowers/specs/2026-09-19-live-score-service-followups.md`)

## Global Constraints

- No emojis in code, comments, docs, or commit messages.
- Zero-deletion policy: do not remove existing behavior, tests, or files; only add or narrow.
- ESPN data stays display-only; win totals still come only from nflverse `result`/`home_score`/`away_score`.
- Services and routes never read `rawdata/`. Only the script (`scripts/sync_live_scores.py`) may read `rawdata/schedules/games.csv`.
- Timezone for the window is `America/New_York`.
- Window opens 20 minutes before the earliest kickoff; closes 30 minutes after all of today's games are final, or 4.5 hours after the latest kickoff otherwise.
- Client polling interval is 30s and must skip while `document.hidden`.
- Bump `?v=N` on the style.css link in `base.html` if CSS changes.

## File Structure

- Modify `scripts/sync_live_scores.py`: add `is_live_score_window_active()`, `--force` handling, fast exit in `main()`.
- Modify `services/live_score_service.py`: status guard in `sync_live_scores_to_df()`.
- Create `services/live_standings_service.py`: `build_live_standings_payload()` (pure).
- Modify `routes/api_routes.py`: `GET /api/live-standings`.
- Create `static/js/standings_refresh.js`: polling and DOM patching.
- Modify `templates/wins_pool.html`: `data-*` hooks and script include.
- Modify `static/style.css`: small live-dot rule reusing `.status-live` if needed.
- Tests: `tests/test_live_score_window.py` (new), `tests/test_sync_live_scores.py` (add), `tests/test_live_score_service.py` (add), `tests/test_live_standings_route.py` (new).

Note: the spec names `tests/test_sync_live_scores.py`/`tests/test_api_routes.py` in the task brief; there is no `tests/test_api_routes.py`, so route tests go in the spec's `tests/test_live_standings_route.py`.

---

### Task 1: Guard unplayed games in `sync_live_scores_to_df()`

**Files:**
- Modify: `services/live_score_service.py:110-139`
- Test: `tests/test_live_score_service.py`

**Interfaces:**
- Produces: unchanged signature `sync_live_scores_to_df(games_df) -> DataFrame`; rows whose ESPN status is neither live nor `STATUS_FINAL` are left untouched (result stays NaN).

- [ ] **Step 1: Write failing tests** (append to `tests/test_live_score_service.py`)

```python
def test_scheduled_game_does_not_get_result_zero(monkeypatch):
    """ESPN reports unplayed games as 0-0 STATUS_SCHEDULED. Writing that would
    set result=0, which compute_team_records reads as a played tie."""
    monkeypatch.setattr(lss, "get_live_updates", lambda: {
        ("KC", "BUF"): {"home_score": 0, "away_score": 0, "status": "STATUS_SCHEDULED",
                        "clock": "0:00", "period": 0},
    })
    df = _repo_game("KC", "BUF")

    result = lss.sync_live_scores_to_df(df)

    assert pd.isna(result.at[0, "result"])


def test_in_progress_game_still_updates(monkeypatch):
    monkeypatch.setattr(lss, "get_live_updates", lambda: {
        ("KC", "BUF"): {"home_score": 10, "away_score": 3, "status": "STATUS_IN_PROGRESS",
                        "clock": "8:00", "period": 2},
    })

    result = lss.sync_live_scores_to_df(_repo_game("KC", "BUF"))

    assert result.at[0, "result"] == 7
    assert result.at[0, "is_live"] == True


def test_halftime_game_still_updates(monkeypatch):
    monkeypatch.setattr(lss, "get_live_updates", lambda: {
        ("KC", "BUF"): {"home_score": 10, "away_score": 3, "status": "STATUS_HALFTIME",
                        "clock": "0:00", "period": 2},
    })

    result = lss.sync_live_scores_to_df(_repo_game("KC", "BUF"))

    assert result.at[0, "clock"] == "Halftime"


def test_postponed_game_is_not_written(monkeypatch):
    monkeypatch.setattr(lss, "get_live_updates", lambda: {
        ("KC", "BUF"): {"home_score": 0, "away_score": 0, "status": "STATUS_POSTPONED",
                        "clock": "0:00", "period": 0},
    })

    result = lss.sync_live_scores_to_df(_repo_game("KC", "BUF"))

    assert pd.isna(result.at[0, "result"])
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_live_score_service.py -v`
Expected: the scheduled and postponed tests FAIL (result is 0).

- [ ] **Step 3: Implement** — in the loop, after `update = normalized_live_data[match_key]` and `espn_status = update['status']`, add:

```python
            # Not-yet-started (STATUS_SCHEDULED, 0-0) and other non-playing
            # statuses must not be written: result=0 would be read downstream
            # as a completed tie by compute_team_records().
            if not is_live_status(espn_status) and espn_status != 'STATUS_FINAL':
                continue
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_live_score_service.py tests/test_live_scores.py -v`
Expected: PASS (existing tests use IN_PROGRESS/FINAL).

- [ ] **Step 5: Commit**

```bash
git add services/live_score_service.py tests/test_live_score_service.py
git commit -m "fix: do not write scheduled games from ESPN in sync_live_scores_to_df"
```

---

### Task 2: Active-window fast exit in `scripts/sync_live_scores.py`

**Files:**
- Modify: `scripts/sync_live_scores.py`
- Create: `tests/test_live_score_window.py`

**Interfaces:**
- Produces: `is_live_score_window_active(now_et: datetime | None = None, games: pd.DataFrame | None = None) -> bool`. `games` defaults to `load_games()` (which reads `rawdata/schedules/games.csv`); `now_et` defaults to now in `America/New_York`. Never raises: any error returns `True` (fail open, so a broken schedule read never suppresses live updates).
- Produces: `main(argv=None)` parses `--force`; exits 0 immediately when the window is inactive and not forced.

Window rules: candidate games are those with `gameday` equal to today (ET) plus yesterday's games whose kickoff is within the last 5 hours. Games missing `gameday`/`gametime` are ignored. No candidates: inactive. `start = min(kickoff) - 20m`. If every candidate has `result` notna: `end = max(kickoff) + 30m`; otherwise `end = max(kickoff) + 4.5h`. Active iff `start <= now <= end`.

- [ ] **Step 1: Write failing tests** (`tests/test_live_score_window.py`)

```python
"""scripts/sync_live_scores.py::is_live_score_window_active() and main()'s
fast exit."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import scripts.sync_live_scores as sls

ET = ZoneInfo("America/New_York")


def _games(rows):
    return pd.DataFrame(rows, columns=["gameday", "gametime", "result"])


def _now(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


SUNDAY = "2026-09-20"
SUNDAY_GAMES = [
    (SUNDAY, "13:00", None),
    (SUNDAY, "16:25", None),
    (SUNDAY, "20:20", None),
]


def test_no_games_today_is_inactive():
    games = _games([("2026-09-22", "13:00", None)])
    assert sls.is_live_score_window_active(_now(2026, 9, 21, 15, 0), games) is False


def test_before_window_opens_is_inactive():
    assert sls.is_live_score_window_active(_now(2026, 9, 20, 10, 0), _games(SUNDAY_GAMES)) is False


def test_fifteen_minutes_before_kickoff_is_active():
    assert sls.is_live_score_window_active(_now(2026, 9, 20, 12, 45), _games(SUNDAY_GAMES)) is True


def test_exactly_twenty_minutes_before_is_active():
    assert sls.is_live_score_window_active(_now(2026, 9, 20, 12, 40), _games(SUNDAY_GAMES)) is True


def test_international_early_game_is_active():
    games = _games([(SUNDAY, "09:30", None), (SUNDAY, "13:00", None)])
    assert sls.is_live_score_window_active(_now(2026, 9, 20, 9, 15), games) is True


def test_snf_in_progress_is_active():
    assert sls.is_live_score_window_active(_now(2026, 9, 20, 21, 30), _games(SUNDAY_GAMES)) is True


def test_all_final_after_midnight_is_inactive():
    games = _games([(SUNDAY, "13:00", 3), (SUNDAY, "16:25", -7), (SUNDAY, "20:20", 10)])
    assert sls.is_live_score_window_active(_now(2026, 9, 21, 0, 30), games) is False


def test_snf_spilling_past_midnight_not_final_is_active():
    assert sls.is_live_score_window_active(_now(2026, 9, 21, 0, 30), _games(SUNDAY_GAMES)) is True


def test_not_final_closes_after_four_and_half_hours():
    games = _games([(SUNDAY, "20:20", None)])
    assert sls.is_live_score_window_active(_now(2026, 9, 21, 1, 0), games) is True
    assert sls.is_live_score_window_active(_now(2026, 9, 21, 1, 0).replace(minute=1), games) is False


def test_missing_gametime_rows_are_ignored():
    games = _games([(SUNDAY, None, None)])
    assert sls.is_live_score_window_active(_now(2026, 9, 20, 13, 0), games) is False


def test_error_fails_open():
    assert sls.is_live_score_window_active(_now(2026, 9, 20, 13, 0), pd.DataFrame({"x": [1]})) is True


def test_main_exits_zero_outside_window_without_touching_firebase(monkeypatch):
    monkeypatch.setattr(sls, "is_live_score_window_active", lambda *a, **k: False)
    monkeypatch.setattr(sls, "initialize_firebase",
                        lambda: pytest.fail("must not init firebase outside window"))
    with pytest.raises(SystemExit) as exc:
        sls.main([])
    assert exc.value.code == 0


def test_main_force_bypasses_window(monkeypatch):
    monkeypatch.setattr(sls, "is_live_score_window_active", lambda *a, **k: False)
    called = {}
    monkeypatch.setattr(sls, "initialize_firebase", lambda: called.setdefault("init", True) and object())
    monkeypatch.setattr(sls, "sync_authoritative", lambda db: pd.DataFrame())
    monkeypatch.setattr(sls, "run_espn_overlay_safely", lambda db, g: 0)
    monkeypatch.setattr("services.db_service.signal_data_update", lambda *a, **k: None)

    sls.main(["--force"])

    assert called.get("init") is True
```

Note: the "closes after 4.5h" test compares 01:00 (exactly end: 20:20 + 4.5h = 00:50) -- fix the arithmetic when writing: 20:20 + 4h30 = 00:50, so assert active at 00:45 and inactive at 00:51. Use those values.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_live_score_window.py -v`
Expected: FAIL (`is_live_score_window_active` not defined).

- [ ] **Step 3: Implement** — add near the top of `scripts/sync_live_scores.py`:

```python
import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
WINDOW_LEAD = timedelta(minutes=20)
WINDOW_FINAL_TAIL = timedelta(minutes=30)
WINDOW_LIVE_TAIL = timedelta(hours=4, minutes=30)
CARRYOVER = timedelta(hours=5)


def is_live_score_window_active(now_et=None, games=None) -> bool:
    """True when the current ET time is inside an NFL game window. Fails open
    (True) on any error so a broken schedule read never suppresses updates."""
    try:
        now = now_et or datetime.now(ET)
        if games is None:
            games = load_games()
        df = games.dropna(subset=["gameday", "gametime"]).copy()
        if df.empty:
            return False
        kickoffs = pd.to_datetime(df["gameday"].astype(str) + " " + df["gametime"].astype(str),
                                  errors="coerce")
        df["kickoff"] = [k.to_pydatetime().replace(tzinfo=ET) if pd.notna(k) else None
                         for k in kickoffs]
        df = df[df["kickoff"].notna()]
        today = now.date()
        keep = [(k.date() == today) or (k.date() == today - timedelta(days=1)
                                        and now - k <= CARRYOVER)
                for k in df["kickoff"]]
        df = df[keep]
        if df.empty:
            return False
        start = min(df["kickoff"]) - WINDOW_LEAD
        latest = max(df["kickoff"])
        all_final = bool(df["result"].notna().all())
        end = latest + (WINDOW_FINAL_TAIL if all_final else WINDOW_LIVE_TAIL)
        return start <= now <= end
    except Exception as e:
        print(f"[warn] window check failed, failing open: {e}")
        return True
```

Change `main()` to `def main(argv=None):`, and at the top:

```python
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Run even outside the active NFL game window")
    args = parser.parse_args(argv)
    if not args.force and not is_live_score_window_active():
        print("Outside active NFL game window. Exiting immediately.")
        sys.exit(0)
```

Update the module docstring's "Runs every 5 minutes" paragraph with a sentence describing the fast exit.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_live_score_window.py tests/test_sync_live_scores.py -v`
Expected: PASS. If `zoneinfo` cannot find `America/New_York` on Windows, `pip install tzdata` and add `tzdata` to `requirements.txt`.

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_live_scores.py tests/test_live_score_window.py requirements.txt
git commit -m "feat: fast-exit sync_live_scores outside NFL game windows"
```

---

### Task 3: `GET /api/live-standings`

**Files:**
- Create: `services/live_standings_service.py`
- Modify: `routes/api_routes.py` (add after `get_live_scores`)
- Create: `tests/test_live_standings_route.py`

**Interfaces:**
- Produces: `build_live_standings_payload(sorted_df, games, year) -> dict` returning `{"year", "last_updated", "standings": [...]}`; per-row keys `rank, player_id, full_name, total_wins, teams[{abbr, wins, pt_diff, is_live, live_score, period, clock}], tiebreakers{tb1..tb6}`. NaN/None become `0`/`null` as appropriate. Empty or None `sorted_df` yields `standings: []`.
- Produces: route returns the same dict via `JSONResponse(sanitize_state(...))`; public (same as `/api/live-scores`); draft-pending seasons return `standings: []`.

Live game detection: rows of `games` with truthy `is_live` and `result` NaN; a team's `live_score` is `"{away} {live_away_score} - {home} {live_home_score}"` style as `"AWAY 14 - HOME 17"` using team abbreviations.

- [ ] **Step 1: Write failing tests**

```python
"""services/live_standings_service.py and GET /api/live-standings."""
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from main import app
from services.live_standings_service import build_live_standings_payload

client = TestClient(app)


def _sorted_df():
    return pd.DataFrame([{
        "Rank": 1, "playerId": 4, "fullName": "Ann Lee", "TotalWins": 7,
        "team1": "KC", "wins1": 3, "ptDiff1": 18,
        "team2": "BUF", "wins2": 2, "ptDiff2": 10,
        "team3": "DET", "wins3": 2, "ptDiff3": np.nan,
        "Tiebreaker1_WorstTeamWins": 2, "Tiebreaker2_2ndWorstTeamWins": 2,
        "Tiebreaker3_BestTeamWins": 3, "Tiebreaker4_WorstTeamPtDiff": np.nan,
        "Tiebreaker5_2ndWorstTeamPtDiff": 10, "Tiebreaker6_BestTeamPtDiff": 18,
    }])


def _games(live=True):
    return pd.DataFrame([{
        "home_team": "LAC", "away_team": "KC", "is_live": live, "result": np.nan,
        "live_home_score": 14, "live_away_score": 17, "period": 3, "clock": "4:12",
    }])


def test_payload_shape_and_live_flag():
    out = build_live_standings_payload(_sorted_df(), _games(), 2026)
    row = out["standings"][0]
    assert out["year"] == 2026
    assert row["rank"] == 1 and row["player_id"] == 4 and row["total_wins"] == 7
    kc = row["teams"][0]
    assert kc["abbr"] == "KC" and kc["is_live"] is True
    assert kc["live_score"] == "KC 17 - LAC 14" and kc["period"] == 3 and kc["clock"] == "4:12"
    assert row["teams"][1]["is_live"] is False
    assert row["tiebreakers"] == {"tb1": 2, "tb2": 2, "tb3": 3, "tb4": 0, "tb5": 10, "tb6": 18}
    assert row["teams"][2]["pt_diff"] == 0


def test_final_game_is_not_live():
    g = _games()
    g["result"] = 3
    out = build_live_standings_payload(_sorted_df(), g, 2026)
    assert out["standings"][0]["teams"][0]["is_live"] is False


def test_empty_inputs():
    assert build_live_standings_payload(pd.DataFrame(), pd.DataFrame(), 2026)["standings"] == []
    assert build_live_standings_payload(None, None, 2026)["standings"] == []


def test_games_without_live_columns_ok():
    out = build_live_standings_payload(_sorted_df(), pd.DataFrame([{"home_team": "A", "away_team": "B"}]), 2026)
    assert out["standings"][0]["teams"][0]["is_live"] is False


def _patch_route(sorted_df, games, picks=(10, 10)):
    empty = pd.DataFrame()
    return (
        patch("routes.api_routes.load_data",
              return_value=(empty, empty, games, empty, empty, empty, empty)),
        patch("routes.api_routes.analysis.get_draft_progress", return_value=picks),
        patch("routes.api_routes.analysis.calculate_wins_pool_standings", return_value=sorted_df),
    )


def test_route_returns_payload_without_auth():
    p1, p2, p3 = _patch_route(_sorted_df(), _games())
    with p1, p2, p3:
        res = client.get("/api/live-standings?year=2026")
    assert res.status_code == 200
    body = res.json()
    assert body["standings"][0]["full_name"] == "Ann Lee"
    assert body["standings"][0]["teams"][0]["is_live"] is True


def test_route_draft_pending_returns_empty_standings():
    p1, p2, p3 = _patch_route(_sorted_df(), _games(), picks=(3, 10))
    with p1, p2, p3:
        res = client.get("/api/live-standings?year=2026")
    assert res.status_code == 200 and res.json()["standings"] == []


def test_route_requires_year():
    assert client.get("/api/live-standings").status_code == 422


def test_route_500_on_failure():
    with patch("routes.api_routes.load_data", side_effect=RuntimeError("boom")):
        res = client.get("/api/live-standings?year=2026")
    assert res.status_code == 500
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_live_standings_route.py -v`
Expected: FAIL (module/route missing).

- [ ] **Step 3: Implement**

`services/live_standings_service.py`:

```python
"""services/live_standings_service.py -- Lightweight leaderboard payload for
the standings page's client-side poll (/api/live-standings)."""
from datetime import datetime

import pandas as pd

TEAM_SLOTS = (1, 2, 3)


def _num(value, default=0):
    return default if value is None or pd.isna(value) else value


def _live_games_by_team(games) -> dict:
    """Map team abbreviation -> live-game info for games underway and not yet
    final per nflverse."""
    out = {}
    if games is None or games.empty or "is_live" not in games.columns:
        return out
    for _, g in games.iterrows():
        if not bool(g.get("is_live")) or pd.notna(g.get("result")):
            continue
        home, away = g.get("home_team"), g.get("away_team")
        info = {
            "live_score": f"{away} {_num(g.get('live_away_score'))} - {home} {_num(g.get('live_home_score'))}",
            "period": None if pd.isna(g.get("period")) else g.get("period"),
            "clock": None if pd.isna(g.get("clock")) else g.get("clock"),
        }
        out[home] = info
        out[away] = info
    return out


def build_live_standings_payload(sorted_df, games, year: int) -> dict:
    live = _live_games_by_team(games)
    rows = []
    if sorted_df is not None and not sorted_df.empty:
        for _, r in sorted_df.iterrows():
            teams = []
            for i in TEAM_SLOTS:
                abbr = r.get(f"team{i}")
                if not isinstance(abbr, str) or not abbr:
                    continue
                info = live.get(abbr)
                team = {
                    "abbr": abbr,
                    "wins": int(_num(r.get(f"wins{i}"))),
                    "pt_diff": int(_num(r.get(f"ptDiff{i}"))),
                    "is_live": info is not None,
                }
                if info:
                    team.update(info)
                teams.append(team)
            rows.append({
                "rank": int(_num(r.get("Rank"))),
                "player_id": int(_num(r.get("playerId"))),
                "full_name": r.get("fullName"),
                "total_wins": int(_num(r.get("TotalWins"))),
                "teams": teams,
                "tiebreakers": {
                    "tb1": int(_num(r.get("Tiebreaker1_WorstTeamWins"))),
                    "tb2": int(_num(r.get("Tiebreaker2_2ndWorstTeamWins"))),
                    "tb3": int(_num(r.get("Tiebreaker3_BestTeamWins"))),
                    "tb4": int(_num(r.get("Tiebreaker4_WorstTeamPtDiff"))),
                    "tb5": int(_num(r.get("Tiebreaker5_2ndWorstTeamPtDiff"))),
                    "tb6": int(_num(r.get("Tiebreaker6_BestTeamPtDiff"))),
                },
            })
    return {"year": year, "last_updated": datetime.now().astimezone().isoformat(), "standings": rows}
```

`routes/api_routes.py` (after `get_live_scores`; import `filter_season` from `services.utils` and `build_live_standings_payload`):

```python
@router.get("/live-standings")
def get_live_standings(year: int):
    """Lightweight leaderboard snapshot for the standings page's client-side
    poll. Public, same visibility as /wins-pool/{year} itself."""
    try:
        all_st, _, all_games, players, _, all_draft, rules = load_data()
        standings = filter_season(all_st, year)
        games = filter_season(all_games, year)
        draft_results = filter_season(all_draft, year)
        picks_made, picks_expected = analysis.get_draft_progress(draft_results, filter_season(rules, year))
        if picks_expected > 0 and picks_made < picks_expected:
            sorted_df = None
        else:
            sorted_df = analysis.calculate_wins_pool_standings(standings, draft_results, players, year, games)
        return JSONResponse(content=sanitize_state(build_live_standings_payload(sorted_df, games, year)))
    except Exception:
        logger.exception("Unhandled error in /api/live-standings")
        return server_error()
```

The route tests patch `routes.api_routes.analysis.*`; `filter_season` must tolerate the empty frames the tests pass (verify against `services/utils.py::filter_season`; if it raises on empty frames without a `season` column, guard in the route or adjust the test frames to include a `season` column).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_live_standings_route.py -v`

- [ ] **Step 5: Commit**

```bash
git add services/live_standings_service.py routes/api_routes.py tests/test_live_standings_route.py
git commit -m "feat: add GET /api/live-standings"
```

---

### Task 4: Client-side polling on `wins_pool.html`

**Files:**
- Create: `static/js/standings_refresh.js`
- Modify: `templates/wins_pool.html`
- Modify: `static/style.css` (only if a live indicator rule is needed; bump `?v=N` in `templates/base.html`)

**Interfaces:**
- Consumes: `/api/live-standings?year=` payload from Task 3; `window.STANDINGS_CONFIG.year`.
- DOM hooks (added to the template): every player block carries `data-player-id="{{ row.playerId }}"` on the leader section, each desktop `.wp-row`, and each `.standings-stacked-card`; team blocks carry `data-team="{{ abbr }}"`; patchable text nodes carry `data-role` values `total`, `rank`, `team-stats`, `tb1`..`tb6`. Every team block also gets a `<span class="live-dot" data-role="live" hidden></span>`.

- [ ] **Step 1: Template hooks.** Add the attributes above to the leader section, desktop rows, and mobile cards in `templates/wins_pool.html`. Existing text and layout are unchanged; only attributes and the hidden live-dot spans are added. Add `<script src="/static/js/standings_refresh.js"></script>` after `standings.js`.

- [ ] **Step 2: Write `standings_refresh.js`**

```javascript
/**
 * standings_refresh.js -- Polls /api/live-standings and patches the wins pool
 * standings page (hero card, desktop rows, mobile cards) without a reload.
 * Pauses while the tab is hidden. Requires window.STANDINGS_CONFIG.year.
 */
(function () {
    'use strict';

    const POLL_INTERVAL_MS = 30000;

    function signed(n) { return (n >= 0 ? '+' : '') + n; }

    function setText(root, role, text) {
        root.querySelectorAll('[data-role="' + role + '"]').forEach(function (el) {
            if (el.textContent !== text) el.textContent = text;
        });
    }

    function patchTeams(root, teams) {
        teams.forEach(function (t) {
            root.querySelectorAll('[data-team="' + t.abbr + '"]').forEach(function (block) {
                const stats = block.querySelector('[data-role="team-stats"]');
                if (stats) {
                    stats.innerHTML = '';
                    stats.append(t.wins + 'W · ');
                    const pd = document.createElement('span');
                    pd.className = t.pt_diff >= 0 ? 'pos' : 'neg';
                    pd.textContent = signed(t.pt_diff);
                    stats.appendChild(pd);
                }
                const dot = block.querySelector('[data-role="live"]');
                if (dot) {
                    dot.hidden = !t.is_live;
                    dot.title = t.is_live ? (t.live_score || 'Live') : '';
                }
            });
        });
    }

    function patchPlayer(root, row) {
        setText(root, 'rank', String(row.rank));
        setText(root, 'total', String(row.total_wins));
        const tb = row.tiebreakers;
        setText(root, 'tb1', String(tb.tb1));
        setText(root, 'tb2', String(tb.tb2));
        setText(root, 'tb3', String(tb.tb3));
        setText(root, 'tb4', String(tb.tb4));
        setText(root, 'tb5', String(tb.tb5));
        setText(root, 'tb6', String(tb.tb6));
        patchTeams(root, row.teams);
    }

    function apply(data) {
        (data.standings || []).forEach(function (row) {
            document.querySelectorAll('[data-player-id="' + row.player_id + '"]').forEach(function (root) {
                patchPlayer(root, row);
            });
        });
    }

    async function poll(year) {
        if (document.hidden) return;
        try {
            const res = await fetch('/api/live-standings?year=' + year);
            if (!res.ok) return;
            apply(await res.json());
        } catch (e) {
            console.error('Live standings refresh failed:', e);
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        const cfg = window.STANDINGS_CONFIG;
        if (!cfg || !cfg.year) return;
        setInterval(function () { poll(cfg.year); }, POLL_INTERVAL_MS);
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden) poll(cfg.year);
        });
    });
}());
```

Known limitation, documented in the file header: rows are patched in place by player id, so a rank change that reorders players updates the rank number but not the row position until the next page load. The first-rank hero card is patched by player id as well.

- [ ] **Step 3: Verify in browser** at desktop width and ~390px mobile width: load `/wins-pool/{year}` locally (`USE_LOCAL_DATA=True`), confirm no console errors, confirm the poll fires (Network tab) and that hiding the tab stops requests.

- [ ] **Step 4: Run full-page render test**

Run: `pytest tests/ -k "wins_pool or standings" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add static/js/standings_refresh.js templates/wins_pool.html static/style.css templates/base.html
git commit -m "feat: live-refresh standings page via /api/live-standings"
```

---

### Task 5: Verify, document, archive

- [ ] Run `pytest tests/ -n auto`; expected: all pass.
- [ ] Update `CLAUDE.md`: `sync_live_scores.py` Commands entry and Scheduled Jobs row (fast exit, `--force`), Module Layout (`live_standings_service.py`), API note for `/api/live-standings`.
- [ ] Note in the plan handoff: spec Part 2 (Cloud Scheduler `*/2` cadence) is a `gcloud scheduler jobs update` operational change and is NOT applied by this branch; surface to the user.
- [ ] `git mv` this plan and the two specs into `completed/` dirs (`docs/superpowers/plans/completed/`, `docs/superpowers/specs/completed/`), except for the cadence item being left open, which is recorded in the completed spec's status line.
- [ ] Use superpowers:verification-before-completion, then superpowers:finishing-a-development-branch.
