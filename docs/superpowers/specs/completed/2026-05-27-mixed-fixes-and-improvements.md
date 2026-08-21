# Mixed Fixes and Improvements — Spec

**Date:** 2026-05-27  
**Items:** 7 features/bugs/refactors across admin UX, schedule display, test quality, and data hygiene

---

## Item 1: Unified ML Accuracy + Predictions Tab

### Problem
Two admin tabs ("ML Accuracy" and "Predictions") do related things with no clear distinction:
- ML Accuracy: aggregate season/week stats, click season → week-level accuracy
- Predictions: season + week selectors, load → per-game table with actual winner + ✓/✗

### Desired Behavior
**Remove the "Predictions" tab.** Fold per-game detail INTO the ML Accuracy tab:
- Season row click → expands week rows inline (unchanged)
- **Week row click → expands per-game rows inline** (new)
- Per-game row shows: Matchup | Model Pick | Actual | ✓/✗ | Model Spread | Vegas | Edge
- A small "🔍" icon on each per-game row links to `/admin/predictions?season=X&week=Y&home=H&away=A` for feature debug
- Incorrect predictions tinted red

The "Feature Debug" page (`/admin/predictions`) and Vegas Edge tab remain intact.

### Files affected
- `templates/admin.html` — remove `#predictions-section` div + Predictions tab button
- `static/js/admin_accuracy.js` — extend `renderWeekPanel()` to fetch + render per-game rows via existing `/api/admin/predictions/games` endpoint; add inline expansion rather than a separate panel
- `static/js/admin_predictions_games.js` — keep as-is (guards on element existence already)
- `routes/admin_routes.py` — no change (endpoint already exists)

---

## Item 2: Elo Tooltip Sorted by Highest

### Problem
Elo chart tooltip shows teams in Chart.js data-order (insertion order). With many teams selected it's hard to read which team is highest at a given point.

### Fix
Add `itemSort: (a, b) => (b.parsed.y ?? 0) - (a.parsed.y ?? 0)` to the `tooltip` config block in `static/js/admin_elo.js` (line ~255).

### Files affected
- `static/js/admin_elo.js` — one-line addition

---

## Item 3: StableNick / Stability Test User Cleanup

### Problem
The `players` collection (Firestore + `.local_db/players.pkl`) contains 49 test players from load/stability tests:
- playerIds 6, 14–67
- `fullName = "Stability Test User"`, `nickName = "StableNick"`

These pollute dropdown menus, player lists, and standings pages.

### Implementation
New script `scripts/cleanup_test_players.py`:
1. Connect to Firestore (refuses to run in local-only mode unless `--force-local`)
2. Query players where `nickName == "StableNick"` OR `fullName == "Stability Test User"`
3. Delete in batches of 500; print IDs + count
4. `--dry-run` flag: print what would be deleted without deleting
5. After deletion: instruct user to run `python scripts/refresh_local_pkls.py`

### Files affected
- Create: `scripts/cleanup_test_players.py`

---

## Item 4: Wins Pool Dropdown Shows 2013 When Viewing 2026

### Problem
`/wins-pool/2026` renders with the dropdown defaulting to "2013 Season".

### Root Cause (two bugs)
1. `get_available_years()` caps to `active_season` (2025). When `year=2026` is not in the returned list, no `<option>` has `selected` → browser defaults to first item in DOM order.
2. Options rendered oldest-first in several templates — most recent should be at top.

### Fix
**`routes/standings_routes.py`** in `wins_pool_by_year()`, after computing `available_years`:
```python
if year not in available_years:
    available_years = sorted(available_years + [year])
```

**Templates** — add `| sort(reverse=True)` to `available_years` loop where missing:
- `templates/wins_pool.html`
- `templates/schedule.html`
- `templates/weekbyweek.html`
- `templates/playoff_race.html`

(`draft_results.html` and `headtohead.html` already sort descending — no change.)

### Files affected
- `routes/standings_routes.py`
- `templates/wins_pool.html`, `schedule.html`, `weekbyweek.html`, `playoff_race.html`

---

## Item 5: Schedule Tab Shows "Live" for All Upcoming Games

### Problem
Every game row on the Schedule page shows a "Live" status badge — including future games weeks away that obviously have not started.

### Why -1000?
`result` is the home-team point differential for a completed game (e.g. `7.0` means home won by 7). For any game that hasn't been played yet — whether it's tomorrow or next season — the value is set to `-1000` (a sentinel for "no result"). The template checks `result != -1000` to mean "Final", otherwise shows "Live". Since all unplayed games share the same sentinel, they all get "Live".

### Why even games with a spread line show "Live"
A `spread_line` is set by Vegas **before** the game is played — it has nothing to do with whether the game has been completed. A game can have a spread line and still have `result == -1000` because the game hasn't happened yet. So "has a line" ≠ "has been played".

### Desired Behavior
- `result != -1000` → **"Final"** (green `status-final` CSS)
- `result == -1000` → **"—"** (neutral, no CSS class)

The actual live-game indicator ("LIVE - Q2 14:32") is already handled separately by `live_score_service` via JS and the `live-badge` element. The status label doesn't need to duplicate it, and "Live" is actively misleading for future scheduled games.

### Fix
In `templates/schedule.html` lines ~95–96, change:
```html
class="game-status-label {% if row['result'] != -1000 %}status-final{% else %}status-live{% endif %}"
{{ 'Final' if row['result'] != -1000 else 'Live' }}
```
To:
```html
class="game-status-label {% if row['result'] is number and row['result'] != -1000 %}status-final{% endif %}"
{{ 'Final' if (row['result'] is number and row['result'] != -1000) else '—' }}
```

### Files affected
- `templates/schedule.html`

---

## Item 6: DRY Violations — Full Completion of Issue #22

### Problems
Four DRY violations across the Python backend:

**A. Password complexity regex** — identical string in 3 places:
- `routes/auth_routes.py:100` — setup new password
- `routes/auth_routes.py:265` — change password
- `routes/admin_routes.py:227` — admin set temp password

**B. `_first_name()` helper** — identical function in 2 places:
- `routes/history_routes.py:14`
- `routes/standings_routes.py:23`

**C. `load_data()` positional 7-tuple unpacking** — 32 call sites across 7 files, all use fragile positional `_` unpacking like `_, _, games, _, _, _, _ = load_data()`. Adding a new return value would silently break all callers.

**D. Season-filter one-liner** — `df[df['season'] == year] if not df.empty else df` appears verbatim ~5 times in standings and history routes.

### Fix

**A — Password regex:**
Add to `services/constants.py` (create if it doesn't exist):
```python
PASSWORD_COMPLEXITY_RE = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{12,}$"
```
Replace all 3 inline definitions with `from services.constants import PASSWORD_COMPLEXITY_RE`.

**B — `_first_name` helper:**
Add to `services/utils.py` (create if it doesn't exist):
```python
def abbreviate_player_name(name: str) -> str:
    """Return 'J. Smith' from 'John Smith', or the original if single-word."""
    parts = str(name).split()
    return f"{parts[0][0]}. {' '.join(parts[1:])}" if len(parts) > 1 else name
```
Remove the local definitions from both route files and import from `services.utils`.

**C — `DataBundle` NamedTuple:**
Add to `services/data_service.py`:
```python
from typing import NamedTuple
import pandas as pd

class DataBundle(NamedTuple):
    standings:        pd.DataFrame
    teams:            pd.DataFrame
    games:            pd.DataFrame
    players:          pd.DataFrame
    draft_order:      pd.DataFrame
    draft_results:    pd.DataFrame
    draft_order_rules: pd.DataFrame
```
Change `load_data()` final return from bare tuple to `return DataBundle(...)`.

Since `NamedTuple` is a tuple subclass, all existing positional unpacking (`a, b, c, ... = load_data()`) continues to work unchanged — this is a **non-breaking change**. The benefit is that new code (and incrementally updated old call sites) can use `data.games` instead of index 2.

Update the 5 internal call sites in `data_service.py`, `draft_service.py`, and `sandbox_service.py` to use named access. Route files can be migrated opportunistically over time.

**D — `filter_season` utility:**
Add to `services/utils.py`:
```python
def filter_season(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Return rows where df['season'] == year, or df unchanged if empty/no column."""
    if df.empty or 'season' not in df.columns:
        return df
    return df[df['season'] == year]
```
Replace the ~5 inline occurrences in `standings_routes.py` and `history_routes.py`.

### Files affected
- Create/modify: `services/constants.py`
- Create/modify: `services/utils.py`
- Modify: `routes/auth_routes.py`, `routes/admin_routes.py`
- Modify: `routes/history_routes.py`, `routes/standings_routes.py`
- Modify: `services/data_service.py` (DataBundle definition + return)
- Modify: `services/draft_service.py`, `services/sandbox_service.py` (internal callers)
- Modify: `tests/test_auth.py` (import constant for password tests)

---

## Item 7: Test Quality — Full Completion of Issue #18

### Problems
Five structural quality issues:

**A. Empty test body** — `tests/test_cache_service.py::test_local_cache_read_write` has `pass` as body (line 21). Silently passes without asserting anything.

**B. Duplicate game DataFrame setup** — identical `pd.DataFrame([...])` construction in `test_prediction_service.py`, `test_analysis_service.py`, and `test_recap_service.py`. Should be a shared `conftest.py` fixture.

**C. Password + abbreviation tests not parameterized** — 4 hardcoded cases each; should use `@pytest.mark.parametrize` over a table of 10+ inputs covering edge cases.

**D. `test_db.py` hardcoded emails** — player creation uses fixed emails, causing collision on re-runs if Firestore state persists.

**E. `test_prediction_service.py` is 556 lines / 13 classes** — needs splitting by concern.

### Fix

**A — Fill in empty test:**
```python
def test_local_cache_read_write(mock_open, mock_env_vars):
    """Round-trip: write analytics cache entry, read it back, assert equal."""
    from services.cache_service import write_analytics_cache, get_analytics_cache
    payload = {"test_key": 42, "nested": {"a": 1}}
    write_analytics_cache("test_metric", 2024, 1, payload)
    result = get_analytics_cache("test_metric", 2024, 1)
    assert result == payload
```

**B — Shared game fixture in `conftest.py`:**
```python
# tests/conftest.py — add fixture
import pytest
import pandas as pd

@pytest.fixture
def sample_games_df():
    """Minimal NFL games DataFrame for unit tests."""
    return pd.DataFrame([
        {"season": 2024, "week": 1, "home_team": "KC",  "away_team": "BUF",
         "home_score": 27, "away_score": 24, "result": 3.0,  "spread_line": -2.5},
        {"season": 2024, "week": 1, "home_team": "PHI", "away_team": "DAL",
         "home_score": 22, "away_score": 16, "result": 6.0,  "spread_line":  3.5},
        {"season": 2024, "week": 2, "home_team": "SF",  "away_team": "SEA",
         "home_score": 0,  "away_score": 0,  "result": -1000, "spread_line": -4.0},
    ])
```
Update `test_prediction_service.py`, `test_analysis_service.py`, and `test_recap_service.py` to use `sample_games_df` fixture instead of inline construction where they share equivalent shapes.

**C — Parameterize password complexity tests:**
In `tests/test_auth.py`, replace repeated `assert re.match(regex, "...")` calls with:
```python
@pytest.mark.parametrize("pw,should_match", [
    ("Short1!",                 False),  # too short
    ("alllowercase1!longpwd",   False),  # no uppercase
    ("ALLUPPERCASE1!longpwd",   False),  # no lowercase
    ("NoSpecialChar12345678",   False),  # no special char
    ("NoNumbers!!LongEnough",   False),  # no digit
    ("Valid1!LongEnoughPwd",    True),
    ("Another$Valid1Password",  True),
    ("Short1!",                 False),
    ("A1!aaaaaaaaaaaaaaaaaa",   True),   # exactly 12 chars
    ("A1!aaaaaaaaaaa",          True),
])
def test_password_complexity(pw, should_match):
    from services.constants import PASSWORD_COMPLEXITY_RE
    import re
    assert bool(re.match(PASSWORD_COMPLEXITY_RE, pw)) == should_match
```

**D — Unique emails in `test_db.py`:**
Replace hardcoded emails with UUID-suffixed generation:
```python
import uuid
def _test_email():
    return f"test_{uuid.uuid4().hex[:8]}@example.com"
```

**E — Split `test_prediction_service.py`** into three files:

| New file | Classes moved |
|---|---|
| `tests/test_elo.py` | `TestHaversine`, `TestWinProbability`, `TestMoVMultiplier`, `TestPreseasonReversion`, `TestPredictionServiceInit` |
| `tests/test_game_prediction.py` | `TestPythagoreanExpectation`, `TestGamePrediction` |
| `tests/test_portfolio.py` | `TestPortfolioProjection`, `TestDraftConfidence`, `TestTeamSummary`, `TestSeasonTeams`, `TestDefunctTeamFiltering`, `TestTeamProjectedWins`, `TestScheduleEnrichment` |

Delete `tests/test_prediction_service.py` after splitting. All imports, fixtures, and helper functions must be replicated in each new file (or extracted to `conftest.py`).

### Files affected
- Modify: `tests/test_cache_service.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_analysis_service.py`, `tests/test_recap_service.py` (use shared fixture)
- Modify: `tests/test_auth.py` (parameterized password tests)
- Modify: `tests/test_db.py` (UUID emails)
- Create: `tests/test_elo.py`, `tests/test_game_prediction.py`, `tests/test_portfolio.py`
- Delete: `tests/test_prediction_service.py`

---

## Scope Summary

| Item | Type | Size | Closes |
|---|---|---|---|
| 1. Unified accuracy+predictions tab | UX refactor | M | — |
| 2. Elo tooltip sort | UX fix | XS | — |
| 3. StableNick cleanup script | Data hygiene | S | — |
| 4. Wins pool dropdown bug | Bug fix | S | — |
| 5. Schedule "Live" → "—" label fix | Bug fix | XS | — |
| 6. DRY violations — full (#22) | Refactor | M | #22 |
| 7. Test quality — full (#18) | Test refactor | M | #18 |
