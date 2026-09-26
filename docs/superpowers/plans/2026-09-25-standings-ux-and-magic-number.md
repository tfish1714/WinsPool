# Standings UX, Seasonal Nav Gating and Magic Number Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Explain the 6-tier tiebreaker cascade on the standings page (GitHub #81) and gate the Playoff Race nav link to Week 10+ while adding clinch/elimination magic numbers to the Playoff Race page (GitHub #86).

**Architecture:** Magic numbers are computed in `calculate_playoff_race()` as additive keys (no existing key changes). The Playoff Race link visibility is decided client-side by a pure ES module (`static/js/nav_gating.js`) fed by a new `latest_week` field on the public `/api/config/settings` response. The decisive-tiebreaker logic is a second pure ES module (`static/js/tiebreaker_explain.js`) that reads the already-rendered `data-role` cells, so it stays correct after `standings_refresh.js` patches values in place.

**Tech Stack:** FastAPI, Jinja2, pandas, vanilla ES modules, pytest, Node (JS unit tests via subprocess, same pattern as `tests/test_grade_badge_js.py`), Playwright (`tests_e2e/`).

**Spec:** GitHub issues #81 and #86 (no design doc). Field semantics are defined in this plan's "Design Rulings" below.

## Global Constraints

- No emojis anywhere: code, comments, docs, commit messages.
- Zero-deletion policy: do not remove or rename any existing feature, key, test, or template block. All changes are additive.
- Follow the surrounding style: comments explain why, not what; match existing naming.
- `docs/` is gitignored: commit plan files with `git add -f <exact path>`. Never `git add -f tests`.
- Commit messages end with the two attribution lines given by the session harness (Co-Authored-By and Claude-Session).
- Worktree baseline failures that are NOT regressions: `tests/test_loaded_version.py` (2), `test_firebase_schema`/`test_data_alignment` errors (5), and `tests/test_player_analytics.py::test_api_player_analytics_returns_200` (flaky under `-n auto`). Record the baseline before Task 1.

## Design Rulings (made up front so no task stalls)

1. **Magic number (clinch first place):** `magic_number = max(other.max_wins for other in others) - player.current_wins + 1`, clamped to a minimum of 0. `0` means clinched outright. This is the issue's formula with `opponent_max_wins` = the best any single opponent can still reach.
2. **Podium (top 3) magic number:** generalisation with `k = PODIUM_SIZE = 3`: `podium_magic_number = kth_highest(other.max_wins) - player.current_wins + 1`, clamped to 0; if fewer than `k` opponents exist the podium is trivially clinched (0).
3. **Elimination:** a player is `eliminated` (from first place) when at least one other player already has `current_wins >= player.max_wins`. `podium_eliminated` is the same test with `k` others. This matches the existing strict `can_pass` convention (`max_wins > target_wins`), i.e. a tie on wins is not treated as a guaranteed loss because tiebreakers are decided later.
4. **Activation week:** `PLAYOFF_RACE_MIN_WEEK = 10` in `services/constants.py`. The nav link shows only when the active season's `latest_week >= 10`. Unknown or failed lookup returns `0`, so the link stays hidden (fail closed). The `/playoff-race/{year}` page itself stays reachable by URL; only the link is gated.
5. **Playoff Race page activation:** the route passes `magic_active = latest_week_for_year >= PLAYOFF_RACE_MIN_WEEK`. Past seasons are complete, so they are always active. The magic-number column renders only when `magic_active` is true.
6. **Mobile:** the drawer link AND the bottom tab bar "Playoff" item are both gated (both are Playoff Race entry points and `tests_e2e/test_nav_parity.py` requires desktop/drawer parity).
7. **Decisive tiebreaker:** for each adjacent pair (rank r, rank r+1) with equal `TotalWins`, the decisive tier is the first of TB1..TB6 (in cascade order) whose values differ. Both players' cells for that tier get class `tb-decisive`. If all six tiers are equal, no cell is highlighted and the tooltip says the tie is unresolved by the cascade.

## Review Focus

- A season with one player, or a player with no opponents: `magic_number` must be 0, no crash, no `max()` of an empty sequence.
- Nav on a fresh browser with empty localStorage and a slow/failed `/api/config/settings`: the Playoff link must not flash visible then vanish; default hidden.
- `latest_week` arriving as a string, null, or missing from a stale cached config: treated as 0 (hidden), never a JS exception that breaks the whole nav render.
- Three or more players tied on wins: each adjacent pair gets its own decisive tier; a middle player can be highlighted for two different tiers.
- Live refresh (`standings_refresh.js`) changes a tiebreaker value without changing rank order: highlights and tooltip text must be recomputed, not left stale.
- Point-differential cells render like `+12` / `-3`: parsing must handle the sign and leading `+`.
- Tooltip must be keyboard reachable (focus) and dismiss on Escape and on touch outside; hover-only is not acceptable on mobile.

## File Structure

- Modify `services/constants.py`: add `PLAYOFF_RACE_MIN_WEEK`, `PODIUM_SIZE`.
- Modify `services/analysis_service.py`: additive keys in `calculate_playoff_race()` via new helper `_compute_magic_numbers(records)`.
- Modify `routes/api_routes.py`: `latest_week` on `/api/config/settings`.
- Modify `routes/standings_routes.py`: `magic_active` context for `playoff_race_by_year`.
- Modify `templates/playoff_race.html`: magic-number column.
- Create `static/js/nav_gating.js`: pure `isPlayoffRaceVisible(latestWeek)`.
- Modify `static/js/main.js`: load/cache `latest_week`, gate the link in `updateNav()`, toggle drawer and bottom-tab items.
- Modify `templates/base.html`: ids and default `hidden` on the drawer and bottom-tab Playoff entries.
- Create `static/js/tiebreaker_explain.js`: pure `decisiveTier()` and `explainTie()` plus DOM layer.
- Modify `static/js/standings_refresh.js`: dispatch `standings:patched` after patching.
- Modify `templates/wins_pool.html`: load the module, tooltip element, expanded cascade text.
- Modify `static/style.css`: `.tb-decisive`, `.tb-tooltip`.
- Tests: `tests/test_analysis_service.py`, `tests/test_config_api.py`, `tests/test_standings_routes.py` (create), `tests/test_nav_gating_js.py` (create), `tests/test_tiebreaker_explain_js.py` (create), `tests_e2e/test_playoff_and_weekly.py`, `tests_e2e/test_standings.py`.

---

### Task 1: Magic numbers in calculate_playoff_race

**Files:**
- Modify: `services/constants.py`
- Modify: `services/analysis_service.py` (function `calculate_playoff_race`)
- Test: `tests/test_analysis_service.py`

**Interfaces:**
- Consumes: existing `records` list of dicts with keys `player`, `current_wins`, `remaining_games`, `max_wins`.
- Produces: each record from `calculate_playoff_race()` gains `magic_number: int`, `podium_magic_number: int`, `eliminated: bool`, `podium_eliminated: bool`. Constants `PLAYOFF_RACE_MIN_WEEK = 10`, `PODIUM_SIZE = 3` importable from `services.constants`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analysis_service.py`:

```python
def _race_schedule(games):
    """games: list of (away_owner, home_owner, result). result None = unplayed."""
    return pd.DataFrame([
        {"fullName_away": a, "fullName_home": h,
         "result": (-1000 if r is None else r), "week": i + 1}
        for i, (a, h, r) in enumerate(games)
    ])


def test_magic_number_is_best_opponent_max_minus_wins_plus_one():
    # A has 2 wins, 0 left. B has 1 win, 3 left (max 4). C has 0 wins, 1 left (max 1).
    schedule = _race_schedule([
        ("B", "A", -3), ("C", "A", -3),           # A beats B and C at home: A=2 wins
        ("B", "C", 7),                             # B (away) loses... result>0 = home win: C wins
    ])
    # Force explicit remaining games via unplayed rows
    schedule = pd.concat([schedule, _race_schedule([("B", "X", None)] * 3 + [("C", "Y", None)])],
                         ignore_index=True)
    race = {r["player"]: r for r in calculate_playoff_race(schedule, pd.DataFrame())}
    best_other_for_a = max(race["B"]["max_wins"], race["C"]["max_wins"])
    assert race["A"]["magic_number"] == max(0, best_other_for_a - race["A"]["current_wins"] + 1)


def test_magic_number_zero_when_clinched():
    # A already has 3 wins and nobody else can reach 3.
    schedule = _race_schedule([("B", "A", -1), ("B", "A", -1), ("B", "A", -1)])
    race = {r["player"]: r for r in calculate_playoff_race(schedule, pd.DataFrame())}
    assert race["A"]["current_wins"] == 3
    assert race["A"]["magic_number"] == 0


def test_magic_number_single_player_does_not_crash():
    schedule = _race_schedule([("A", "A", 5)])
    race = calculate_playoff_race(schedule, pd.DataFrame())
    assert len(race) == 1
    assert race[0]["magic_number"] == 0
    assert race[0]["podium_magic_number"] == 0
    assert race[0]["eliminated"] is False


def test_eliminated_when_another_player_already_has_more_than_max_wins():
    # B has 3 wins; A has 0 wins and 1 game left (max 1) -> A eliminated from first.
    schedule = _race_schedule([("A", "B", 1), ("A", "B", 1), ("A", "B", 1), ("A", "Z", None)])
    race = {r["player"]: r for r in calculate_playoff_race(schedule, pd.DataFrame())}
    assert race["A"]["eliminated"] is True
    assert race["B"]["eliminated"] is False


def test_podium_flags_use_kth_opponent():
    # Five players; only compare shape: podium number never exceeds first-place number.
    games = []
    for i, owner in enumerate(["A", "B", "C", "D", "E"]):
        games += [(owner, "Z", -1)] * (5 - i)          # owner wins (5 - i) games as away
        games += [(owner, "Z", None)] * 2              # 2 games left each
    race = calculate_playoff_race(_race_schedule(games), pd.DataFrame())
    for r in race:
        assert r["podium_magic_number"] <= r["magic_number"]
        assert r["magic_number"] >= 0 and r["podium_magic_number"] >= 0


def test_existing_playoff_race_keys_unchanged():
    schedule = _race_schedule([("B", "A", -1), ("A", "B", None)])
    rec = calculate_playoff_race(schedule, pd.DataFrame())[0]
    for key in ("player", "current_wins", "remaining_games", "max_wins", "race", "rank"):
        assert key in rec
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_analysis_service.py -k "magic or eliminated or podium_flags or existing_playoff" -v`
Expected: FAIL with `KeyError: 'magic_number'` (the last test passes already; that is fine, it guards the additive change).

- [ ] **Step 3: Write the implementation**

In `services/constants.py` add (near the other tunables):

```python
# Playoff Race page/nav activation: the link is hidden in the early season
# and appears once the active season reaches this week.
PLAYOFF_RACE_MIN_WEEK = 10

# "Podium" for the playoff-race magic number: finishing in the top N.
PODIUM_SIZE = 3
```

In `services/analysis_service.py`, import the constants where the other constants are imported, then add above `calculate_playoff_race`:

```python
def _kth_highest(values: List[int], k: int) -> Optional[int]:
    """The k-th highest value (1-indexed), or None when fewer than k values exist."""
    ordered = sorted(values, reverse=True)
    return ordered[k - 1] if len(ordered) >= k else None


def _compute_magic_numbers(records: List[Dict[str, Any]]) -> None:
    """Attach clinch and elimination magic numbers to each record in place.

    magic_number: wins this player still needs to be strictly ahead of every
    opponent's best possible finish (opponent_max_wins - current_wins + 1),
    clamped at 0 (0 == clinched). podium_magic_number is the same test against
    the PODIUM_SIZE-th best opponent, so at most PODIUM_SIZE - 1 opponents can
    still reach the player. eliminated / podium_eliminated use the strict
    `>=` convention (a tie on wins is decided by tiebreakers later, so it is
    not treated as a guaranteed loss until an opponent's CURRENT wins already
    reach this player's best possible total).
    """
    for rec in records:
        others = [o for o in records if o is not rec]
        other_max = [o['max_wins'] for o in others]
        other_cur = [o['current_wins'] for o in others]

        first_bar = max(other_max) if other_max else None
        rec['magic_number'] = (
            0 if first_bar is None else max(0, first_bar - rec['current_wins'] + 1)
        )
        podium_bar = _kth_highest(other_max, PODIUM_SIZE)
        rec['podium_magic_number'] = (
            0 if podium_bar is None else max(0, podium_bar - rec['current_wins'] + 1)
        )
        rec['eliminated'] = sum(1 for c in other_cur if c >= rec['max_wins']) >= 1
        rec['podium_eliminated'] = (
            sum(1 for c in other_cur if c >= rec['max_wins']) >= PODIUM_SIZE
        )
```

Call it at the end of `calculate_playoff_race()` just before the debug log, after the `rec['rank'] = i + 1` loop:

```python
    _compute_magic_numbers(records)
```

Ensure `Optional` is imported from `typing` in that module.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_analysis_service.py -v`
Expected: PASS (all, including the pre-existing playoff race tests).

- [ ] **Step 5: Commit**

```bash
git add services/constants.py services/analysis_service.py tests/test_analysis_service.py
git commit -m "feat: compute clinch and elimination magic numbers in playoff race"
```

---

### Task 2: latest_week in config, magic_active in the route, magic column in the template

**Files:**
- Modify: `routes/api_routes.py` (`get_config`)
- Modify: `routes/standings_routes.py` (`playoff_race_by_year`)
- Modify: `templates/playoff_race.html`
- Test: `tests/test_config_api.py`, `tests/test_standings_routes.py` (create)

**Interfaces:**
- Consumes: `PLAYOFF_RACE_MIN_WEEK`, race records from Task 1, `get_active_season`, `get_latest_week_for_year` (services.data_service).
- Produces: `/api/config/settings` JSON gains `latest_week: int` (0 on any failure). Template context gains `magic_active: bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config_api.py`:

```python
def test_get_config_settings_includes_integer_latest_week():
    data = client.get("/api/config/settings").json()
    assert "latest_week" in data
    assert isinstance(data["latest_week"], int) and data["latest_week"] >= 0


def test_get_config_latest_week_fails_closed_to_zero(monkeypatch):
    import routes.api_routes as api_routes

    def boom():
        raise RuntimeError("data unavailable")

    monkeypatch.setattr(api_routes, "_active_season_latest_week", boom)
    # _active_season_latest_week is called through a guarded wrapper; the
    # endpoint must still return 200 with latest_week == 0.
    data = client.get("/api/config/settings").json()
    assert data["latest_week"] == 0
```

Create `tests/test_standings_routes.py`:

```python
"""Route-level tests for /playoff-race/{year}: the magic-number column only
renders once the season has reached PLAYOFF_RACE_MIN_WEEK."""
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from main import app
from services.constants import PLAYOFF_RACE_MIN_WEEK

YEAR = 3000
client = TestClient(app)


def _schedule():
    return pd.DataFrame([
        {"fullName_away": "Ann", "fullName_home": "Bo", "result": -3, "week": 1},
        {"fullName_away": "Ann", "fullName_home": "Bo", "result": -1000, "week": 2},
    ])


def _load(week):
    games = pd.DataFrame([{"season": YEAR, "week": week, "result": 1}])
    empty = pd.DataFrame()
    return empty, empty, games, empty, empty, empty, empty


@pytest.fixture
def _authed(auth_token):
    return {"Authorization": auth_token}


def _render(week, monkeypatch):
    import routes.standings_routes as sr
    monkeypatch.setattr(sr, "load_data", lambda *a, **k: _load(week))
    monkeypatch.setattr(sr, "get_latest_week_for_year", lambda games, year: week)
    monkeypatch.setattr(sr.analysis, "get_enriched_schedule", lambda *a, **k: _schedule())
    monkeypatch.setattr(sr, "get_available_years", lambda *a, **k: [YEAR])
    monkeypatch.setattr(sr, "get_active_season", lambda *a, **k: YEAR)
    return client.get(f"/playoff-race/{YEAR}")


def test_magic_column_hidden_before_min_week(monkeypatch):
    resp = _render(PLAYOFF_RACE_MIN_WEEK - 1, monkeypatch)
    assert resp.status_code == 200
    assert "data-magic-number" not in resp.text


def test_magic_column_shown_from_min_week(monkeypatch):
    resp = _render(PLAYOFF_RACE_MIN_WEEK, monkeypatch)
    assert resp.status_code == 200
    assert "data-magic-number" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config_api.py tests/test_standings_routes.py -v`
Expected: FAIL (`latest_week` missing; `data-magic-number` never rendered). If the route tests fail on import names (e.g. `get_latest_week_for_year` not imported in `routes/standings_routes.py`), check the import lines at the top of that file and monkeypatch the names it actually uses.

- [ ] **Step 3: Write the implementation**

`routes/api_routes.py`, above `get_config`:

```python
def _active_season_latest_week() -> int:
    """Latest week of the active season, or 0 when it cannot be determined.

    The nav uses this to decide whether the Playoff Race link is shown, so
    every failure path returns 0 (link stays hidden) rather than raising.
    """
    from services.data_service import get_active_season, get_latest_week_for_year
    _, _, games, _, _, draft_results, rules = load_data()
    season = get_active_season(games, draft_results, rules)
    return int(get_latest_week_for_year(games, season))


def _safe_latest_week() -> int:
    try:
        return max(0, int(_active_season_latest_week()))
    except Exception:
        logger.exception("config: could not determine latest_week")
        return 0
```

In `get_config`, include it in both payloads:

```python
    version = {
        "app_version": os.environ.get("APP_VERSION", "dev"),
        "app_deployed_at": os.environ.get("APP_DEPLOYED_AT", ""),
        "latest_week": _safe_latest_week(),
    }
```

(the `version` dict already feeds both the success and the fallback response.)

`routes/standings_routes.py`, in `playoff_race_by_year`, import `PLAYOFF_RACE_MIN_WEEK` from `services.constants`, compute `latest_week = get_latest_week_for_year(games, year)` before the `try`, and add to the context:

```python
        "magic_active": latest_week >= PLAYOFF_RACE_MIN_WEEK,
```

`templates/playoff_race.html`: inside `.race-stats` (after the "Best Possible" stat) add:

```jinja
                    {% if magic_active %}
                    <div class="race-stat" data-magic-number>
                        {% if rec.eliminated %}
                        Magic #: <strong>Eliminated</strong>
                        {% elif rec.magic_number == 0 %}
                        Magic #: <strong>Clinched</strong>
                        {% else %}
                        Magic #: <strong>{{ rec.magic_number }}</strong>
                        {% endif %}
                    </div>
                    <div class="race-stat" data-podium-magic-number>
                        {% if rec.podium_eliminated %}
                        Podium #: <strong>Eliminated</strong>
                        {% elif rec.podium_magic_number == 0 %}
                        Podium #: <strong>Clinched</strong>
                        {% else %}
                        Podium #: <strong>{{ rec.podium_magic_number }}</strong>
                        {% endif %}
                    </div>
                    {% endif %}
```

Add a one-line legend under the page subtitle, also inside `{% if magic_active %}`: "Magic #: wins still needed to finish strictly ahead of every opponent's best case. Podium #: same for a top-3 finish."

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config_api.py tests/test_standings_routes.py tests/test_analysis_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add routes/api_routes.py routes/standings_routes.py templates/playoff_race.html tests/test_config_api.py tests/test_standings_routes.py
git commit -m "feat: expose latest_week and render magic numbers on playoff race"
```

---

### Task 3: Gate the Playoff Race nav link (desktop, drawer, bottom tab bar)

**Files:**
- Create: `static/js/nav_gating.js`
- Modify: `static/js/main.js` (constructor, sync block near `mockDraftActive`, `updateNav()`)
- Modify: `templates/base.html`
- Test: `tests/test_nav_gating_js.py` (create)

**Interfaces:**
- Consumes: `latest_week` from `/api/config/settings` (Task 2), `PLAYOFF_RACE_MIN_WEEK` mirrored as a JS constant.
- Produces: `export const PLAYOFF_RACE_MIN_WEEK = 10; export function isPlayoffRaceVisible(latestWeek)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_nav_gating_js.py` (same Node-driven pattern as `tests/test_grade_badge_js.py`):

```python
"""static/js/nav_gating.js -- the Playoff Race nav link only appears from
Week 10. There is no JS unit-test runner in this repo, so this drives the
pure ES module through Node and skips when Node is not installed."""
import json
import pathlib
import shutil
import subprocess

import pytest

from services.constants import PLAYOFF_RACE_MIN_WEEK

MODULE = pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "nav_gating.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run(expr: str):
    script = (
        f"import * as m from {json.dumps(MODULE.as_uri())};"
        f"console.log(JSON.stringify({expr}));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=20,
    )
    return json.loads(out.stdout)


def test_js_constant_matches_python_constant():
    assert _run("m.PLAYOFF_RACE_MIN_WEEK") == PLAYOFF_RACE_MIN_WEEK


@pytest.mark.parametrize("week,expected", [
    (1, False), (9, False), (10, True), (11, True), (18, True),
])
def test_visibility_threshold(week, expected):
    assert _run(f"m.isPlayoffRaceVisible({week})") is expected


@pytest.mark.parametrize("value", ["null", "undefined", "'abc'", "NaN", "-3", "''"])
def test_garbage_values_stay_hidden(value):
    assert _run(f"m.isPlayoffRaceVisible({value})") is False


def test_numeric_string_from_localstorage_is_accepted():
    assert _run("m.isPlayoffRaceVisible('12')") is True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_nav_gating_js.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

Create `static/js/nav_gating.js`:

```javascript
/**
 * nav_gating.js -- seasonal visibility rules for nav links.
 *
 * The Playoff Race link clutters the early season, so it only appears once
 * the active season reaches PLAYOFF_RACE_MIN_WEEK. Must stay in sync with
 * services/constants.py::PLAYOFF_RACE_MIN_WEEK (tests/test_nav_gating_js.py
 * asserts the two match). Anything that is not a finite number (missing,
 * null, a stale cache value) hides the link: fail closed.
 */
export const PLAYOFF_RACE_MIN_WEEK = 10;

export function isPlayoffRaceVisible(latestWeek) {
    if (latestWeek === null || latestWeek === undefined || latestWeek === '') return false;
    const week = Number(latestWeek);
    return Number.isFinite(week) && week >= PLAYOFF_RACE_MIN_WEEK;
}
```

`static/js/main.js`:
1. Add `import { isPlayoffRaceVisible } from './nav_gating.js';` with the other imports at the top.
2. Where the constructor initialises `this.mockDraftActive` from localStorage, add: `this.latestWeek = Number(localStorage.getItem('nfl_wins_latest_week')) || 0;` (wrap like the neighbouring reads if they use try/catch).
3. In the background sync block (right after the `freshMockDraftActive` handling), add:

```javascript
                const freshLatestWeek = Number(cfg.latest_week) || 0;
                localStorage.setItem('nfl_wins_latest_week', String(freshLatestWeek));
                if (freshLatestWeek !== this.latestWeek) {
                    this.latestWeek = freshLatestWeek;
                    needsNavUpdate = true;
                }
```

4. In `updateNav()`, replace the literal Playoff Race entry so it is conditionally included, keeping the same object:

```javascript
        const primaryLinks = [
            { href: `/wins-pool/${new Date().getFullYear()}`, label: 'Standings', paths: ['/wins-pool'] },
            { href: '/schedule',      label: 'Schedule',     paths: ['/schedule'] },
            this.draftActive
                ? { href: '/draft',   label: 'Live Draft',   paths: ['/draft'], live: true }
                : { href: '/draft-results', label: 'Draft Results', paths: ['/draft-results'] },
        ];
        const showPlayoffRace = isPlayoffRaceVisible(this.latestWeek);
        if (showPlayoffRace) {
            primaryLinks.push({ href: '/playoff-race', label: 'Playoff Race', paths: ['/playoff-race'] });
        }
```

(Do not delete the Admin push that follows.) Then, next to the existing "Drawer Mock Draft link" block, add:

```javascript
        // ── Drawer + bottom tab Playoff Race entries ──
        document.getElementById('drawer-playoff-race-link')?.classList.toggle('hidden', !showPlayoffRace);
        document.getElementById('btb-playoff-tab')?.classList.toggle('hidden', !showPlayoffRace);
```

`templates/base.html`: give both entries an id and default them hidden:

```html
                <a href="/playoff-race" id="drawer-playoff-race-link" class="hidden"><i data-lucide="flag"></i> Playoff Race</a>
```
```html
            <a href="/playoff-race" class="btb-item hidden" id="btb-playoff-tab" data-path="/playoff-race">
```

Then verify in `static/style.css` that `.bottom-tab-bar` lays out correctly with 4 items when one is hidden (flex or grid: if it uses `grid-template-columns: repeat(5, 1fr)`, change to `display: flex` with `flex: 1` items or `repeat(auto-fit, minmax(0, 1fr))`). Confirm `.hidden` resolves to `display: none` for `.btb-item` (check specificity against `.btb-item { display: ... }`; add `.btb-item.hidden { display: none; }` if needed).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_nav_gating_js.py -v`
Expected: PASS. Then `pytest tests/test_static_assets.py -v` (new module must be resolvable by the static asset machinery) and expect PASS.

- [ ] **Step 5: Commit**

```bash
git add static/js/nav_gating.js static/js/main.js templates/base.html static/style.css tests/test_nav_gating_js.py
git commit -m "feat: hide Playoff Race nav link until week 10"
```

---

### Task 4: Decisive tiebreaker highlight and explainer tooltip

**Files:**
- Create: `static/js/tiebreaker_explain.js`
- Modify: `static/js/standings_refresh.js` (dispatch event after patch)
- Modify: `templates/wins_pool.html` (module script, cascade legend)
- Modify: `static/style.css`
- Test: `tests/test_tiebreaker_explain_js.py` (create), `tests/test_standings_routes.py` (append)

**Interfaces:**
- Consumes: DOM hooks already in the template: `.standings-stacked-card[data-player-id]` containing `[data-role="total"]`, `[data-role="rank"]`, `[data-role="tb1"]`..`[data-role="tb6"]`; `.wp-row[data-player-id]` and `.wp-leader[data-player-id]` share the same `data-role` names.
- Produces (pure, exported): `TIERS` (array of six `{key, label, description}` in cascade order), `decisiveTier(prev, cur)` returning `{tier: 1..6, key: 'tb1'..'tb6'} | null` where `prev`/`cur` are `{total: number, tb: number[6]}`, and `explainTie(prev, cur, prevName, curName)` returning a plain-text sentence. DOM layer `applyTiebreakerHighlights(root = document)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tiebreaker_explain_js.py`:

```python
"""static/js/tiebreaker_explain.js -- decisive tiebreaker logic. Pure ES
module driven through Node (skips when Node is missing)."""
import json
import pathlib
import shutil
import subprocess

import pytest

MODULE = pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "tiebreaker_explain.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run(expr: str):
    script = (
        f"import * as m from {json.dumps(MODULE.as_uri())};"
        f"console.log(JSON.stringify({expr}));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=20,
    )
    return json.loads(out.stdout)


def test_six_tiers_in_cascade_order():
    keys = _run("m.TIERS.map(t => t.key)")
    assert keys == ["tb1", "tb2", "tb3", "tb4", "tb5", "tb6"]


def test_decisive_tier_is_first_differing_tier():
    prev = "{total: 9, tb: [3, 3, 3, 10, 5, 1]}"
    cur = "{total: 9, tb: [3, 2, 4, 20, 5, 1]}"
    assert _run(f"m.decisiveTier({prev}, {cur})") == {"tier": 2, "key": "tb2"}


def test_first_tier_can_decide():
    assert _run("m.decisiveTier({total: 5, tb: [2,0,0,0,0,0]}, {total: 5, tb: [1,9,9,9,9,9]})") == {"tier": 1, "key": "tb1"}


def test_point_differential_tier_decides_when_wins_tiers_equal():
    assert _run("m.decisiveTier({total: 5, tb: [1,1,3,12,0,0]}, {total: 5, tb: [1,1,3,-4,0,0]})") == {"tier": 4, "key": "tb4"}


def test_different_totals_have_no_decisive_tier():
    assert _run("m.decisiveTier({total: 6, tb: [1,1,1,1,1,1]}, {total: 5, tb: [0,0,0,0,0,0]})") is None


def test_fully_identical_tiers_have_no_decisive_tier():
    assert _run("m.decisiveTier({total: 5, tb: [1,1,3,4,0,0]}, {total: 5, tb: [1,1,3,4,0,0]})") is None


def test_explain_names_both_players_and_the_tier():
    text = _run("m.explainTie({total: 5, tb: [1,1,3,12,0,0]}, {total: 5, tb: [1,1,3,-4,0,0]}, 'Ann', 'Bo')")
    assert "Ann" in text and "Bo" in text and "worst team point differential" in text.lower()


def test_explain_unresolved_tie_says_so():
    text = _run("m.explainTie({total: 5, tb: [1,1,3,4,0,0]}, {total: 5, tb: [1,1,3,4,0,0]}, 'Ann', 'Bo')")
    assert "all six" in text.lower()


def test_parse_cell_handles_signed_differentials():
    assert _run("[m.parseCell('+12'), m.parseCell('-3'), m.parseCell('7'), m.parseCell(''), m.parseCell(null)]") == [12, -3, 7, 0, 0]
```

Append to `tests/test_standings_routes.py` a render test that the wins pool page loads the module and carries the legend (use the same monkeypatching approach: stub `load_data` and `analysis.calculate_wins_pool_standings` with a two-row DataFrame that has all columns the template reads; if constructing that frame is heavy, reuse the fixture builders from `tests/test_wins_pool_missing_standings.py`):

```python
def test_wins_pool_page_loads_tiebreaker_module_and_legend(monkeypatch):
    resp = _render_wins_pool(monkeypatch)   # helper defined in this file, see step 3 note
    assert resp.status_code == 200
    assert "tiebreaker_explain.js" in resp.text
    assert "id=\"tb-tooltip\"" in resp.text
    assert "worst team wins" in resp.text.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_tiebreaker_explain_js.py tests/test_standings_routes.py -v`
Expected: FAIL (module missing, assertions unmet).

- [ ] **Step 3: Implement**

Create `static/js/tiebreaker_explain.js`:

```javascript
/**
 * tiebreaker_explain.js -- explains WHY tied players are ranked where they
 * are. Standings sort by TotalWins, then six tiers in order (see
 * services/constants.py::TIEBREAKER_SORT_COLS, all descending). For each
 * adjacent pair with equal totals, the first tier whose values differ is the
 * decisive one: that cell is highlighted and explained in a tooltip.
 *
 * The DOM layer reads the rendered data-role cells (not server JSON) so it
 * stays correct after standings_refresh.js patches values in place; it
 * re-runs on the `standings:patched` event.
 */
export const TIERS = [
    { key: 'tb1', label: 'TB1', description: 'worst team wins' },
    { key: 'tb2', label: 'TB2', description: '2nd-worst team wins' },
    { key: 'tb3', label: 'TB3', description: 'best team wins' },
    { key: 'tb4', label: 'TB4', description: 'worst team point differential' },
    { key: 'tb5', label: 'TB5', description: '2nd-worst team point differential' },
    { key: 'tb6', label: 'TB6', description: 'best team point differential' },
];

export function parseCell(text) {
    if (text === null || text === undefined) return 0;
    const n = parseInt(String(text).replace('+', '').trim(), 10);
    return Number.isFinite(n) ? n : 0;
}

export function decisiveTier(prev, cur) {
    if (!prev || !cur || prev.total !== cur.total) return null;
    for (let i = 0; i < TIERS.length; i++) {
        if (prev.tb[i] !== cur.tb[i]) return { tier: i + 1, key: TIERS[i].key };
    }
    return null;
}

export function explainTie(prev, cur, prevName, curName) {
    const hit = decisiveTier(prev, cur);
    if (!hit) {
        return `${prevName} and ${curName} are level on wins and on all six tiebreakers; their order is not decided by the cascade.`;
    }
    const tier = TIERS[hit.tier - 1];
    return `${prevName} and ${curName} are tied on ${prev.total} wins. ` +
        `${prevName} ranks ahead on ${tier.label} (${tier.description}): ` +
        `${prev.tb[hit.tier - 1]} vs ${cur.tb[hit.tier - 1]}. ` +
        `Cascade: total wins, then worst team wins, 2nd-worst, best team wins, ` +
        `then worst, 2nd-worst and best team point differential.`;
}

function readEntry(card) {
    const val = (role) => parseCell(card.querySelector(`[data-role="${role}"]`)?.textContent);
    return {
        id: card.dataset.playerId,
        name: card.querySelector('.standings-stacked-card__name')?.textContent?.trim() || 'Player',
        total: val('total'),
        tb: TIERS.map((t) => val(t.key)),
    };
}

function cellsFor(playerId, key, root) {
    return root.querySelectorAll(`[data-player-id="${playerId}"] [data-role="${key}"]`);
}

export function applyTiebreakerHighlights(root = document) {
    root.querySelectorAll('.tb-decisive').forEach((el) => {
        el.classList.remove('tb-decisive');
        el.removeAttribute('data-tb-explain');
        el.removeAttribute('tabindex');
    });
    // The mobile stacked cards list every player (including rank 1) with all
    // six tiers, so they are the canonical data source on every viewport.
    const entries = Array.from(root.querySelectorAll('.standings-stacked-card')).map(readEntry);
    for (let i = 1; i < entries.length; i++) {
        const prev = entries[i - 1];
        const cur = entries[i];
        const hit = decisiveTier(prev, cur);
        if (!hit) continue;
        const text = explainTie(prev, cur, prev.name, cur.name);
        [prev.id, cur.id].forEach((id) => {
            cellsFor(id, hit.key, root).forEach((el) => {
                const host = el.closest('.tb-v, span') || el;
                host.classList.add('tb-decisive');
                host.setAttribute('data-tb-explain', text);
                host.setAttribute('tabindex', '0');
            });
        });
    }
}

function initTooltip() {
    const tip = document.getElementById('tb-tooltip');
    if (!tip) return;
    const show = (el) => {
        tip.textContent = el.getAttribute('data-tb-explain') || '';
        tip.hidden = false;
        const r = el.getBoundingClientRect();
        tip.style.top = `${window.scrollY + r.bottom + 8}px`;
        tip.style.left = `${Math.max(8, Math.min(window.scrollX + r.left, window.innerWidth - tip.offsetWidth - 8))}px`;
    };
    const hide = () => { tip.hidden = true; };
    document.addEventListener('mouseover', (e) => {
        const el = e.target.closest?.('.tb-decisive');
        if (el) show(el);
    });
    document.addEventListener('mouseout', (e) => {
        if (e.target.closest?.('.tb-decisive')) hide();
    });
    document.addEventListener('focusin', (e) => {
        const el = e.target.closest?.('.tb-decisive');
        if (el) show(el);
    });
    document.addEventListener('focusout', hide);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
    document.addEventListener('click', (e) => {
        const el = e.target.closest?.('.tb-decisive');
        if (el) show(el); else hide();
    });
}

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => {
        applyTiebreakerHighlights();
        initTooltip();
    });
    document.addEventListener('standings:patched', () => applyTiebreakerHighlights());
}
```

`static/js/standings_refresh.js`: at the end of `apply(data)` (after the `standings.forEach` patch loop) add:

```javascript
        document.dispatchEvent(new CustomEvent('standings:patched'));
```

`templates/wins_pool.html`:
- In the `{% block scripts %}` after the `standings_refresh.js` tag add `<script type="module" src="{{ static_url('js/tiebreaker_explain.js') }}"></script>`.
- Add the tooltip container once, just above the existing `.tiebreaker-note` div: `<div id="tb-tooltip" class="tb-tooltip" role="tooltip" hidden></div>`.
- Extend the existing `.tiebreaker-note` (do not remove any existing text) with a second sentence: `Players are ranked by total wins first; ties are broken by TB1 through TB6 in order. A highlighted tiebreaker cell shows the tier that separated two tied players (hover, tap, or focus it for details).`

`static/style.css`: append:

```css
.tb-decisive {
    outline: 2px solid var(--accent, #c9a24a);
    outline-offset: 2px;
    border-radius: 4px;
    cursor: help;
}
.tb-tooltip {
    position: absolute;
    z-index: 1000;
    max-width: min(320px, calc(100vw - 16px));
    padding: 0.6rem 0.75rem;
    font-size: 0.8rem;
    line-height: 1.35;
    color: var(--ink, #fff);
    background: rgba(0, 0, 0, 0.9);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 8px;
    pointer-events: none;
}
.tb-tooltip[hidden] { display: none; }
```

Note for the route test: define `_render_wins_pool(monkeypatch)` in `tests/test_standings_routes.py` by monkeypatching `routes.standings_routes.load_data` and `analysis.calculate_wins_pool_standings` to return a two-row frame containing every column the template reads (`playerId, fullName, TotalWins, Rank, team1..3, wins1..3, ptDiff1..3, Tiebreaker1..6_*`, `refreshTime`), plus `get_draft_progress` returning `(0, 0)`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_tiebreaker_explain_js.py tests/test_standings_routes.py tests/test_static_assets.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add static/js/tiebreaker_explain.js static/js/standings_refresh.js templates/wins_pool.html static/style.css tests/test_tiebreaker_explain_js.py tests/test_standings_routes.py
git commit -m "feat: highlight decisive tiebreaker and explain the cascade on standings"
```

---

### Task 5: Playwright coverage and full-suite verification

**Files:**
- Modify: `tests_e2e/test_playoff_and_weekly.py`
- Modify: `tests_e2e/test_standings.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4. Uses Playwright `page.route` to stub `**/api/config/settings` so the tests do not depend on local data's current week.

- [ ] **Step 1: Write the e2e tests**

Append to `tests_e2e/test_playoff_and_weekly.py` (reuse that file's existing login helper and fixtures; mirror the signature of `test_playoff_race_loads_and_year_picker_navigates`):

```python
import json


def _stub_latest_week(page, week):
    def handle(route):
        resp = route.fetch()
        body = resp.json()
        body["latest_week"] = week
        route.fulfill(response=resp, body=json.dumps(body))
    page.route("**/api/config/settings", handle)


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_playoff_race_link_hidden_before_week_10(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _stub_latest_week(page, 9)
    _login_and_open_standings(page, live_server, test_player_credentials[1])   # use this file's existing login helper
    if viewport["width"] >= 1024:
        assert page.locator("#nav-primary-links a[href='/playoff-race']").count() == 0
    else:
        assert page.locator("#btb-playoff-tab").is_hidden()
        page.click("#btb-more-tab")
        page.wait_for_selector("#nav-drawer.open", timeout=5000)
        assert page.locator("#drawer-playoff-race-link").is_hidden()


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_playoff_race_link_visible_from_week_10(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _stub_latest_week(page, 10)
    _login_and_open_standings(page, live_server, test_player_credentials[1])
    if viewport["width"] >= 1024:
        page.wait_for_selector("#nav-primary-links a[href='/playoff-race']", timeout=10000)
    else:
        page.wait_for_selector("#btb-playoff-tab:not(.hidden)", timeout=10000)
```

If the file has no login helper by that name, copy `_login` from `tests_e2e/test_standings.py` and adapt (page.goto(live_server), fill `#auth-email` / `#auth-password`, click `#auth-submit-btn`, wait for `#signin-screen` hidden).

Also add one test that `/playoff-race/{year}` still loads (HTTP 200) when the link is hidden (direct URL stays reachable).

Append to `tests_e2e/test_standings.py` a test that, on the standings page, any element with class `tb-decisive` (if present in the local data) carries a non-empty `data-tb-explain` and that focusing it shows `#tb-tooltip` (skip with `pytest.skip` when no ties exist in local data), plus an unconditional test that `#tb-tooltip` exists and is hidden by default.

- [ ] **Step 2: Run e2e**

Run: `pytest tests_e2e/test_playoff_and_weekly.py tests_e2e/test_standings.py tests_e2e/test_nav_parity.py -v`
Expected: PASS (parity test must still pass: desktop and drawer both omit or both include Playoff Race). If e2e cannot run in this environment (missing E2E env vars per `tests_e2e/conftest.py`), record that explicitly in the task report and confirm the tests at least collect: `pytest tests_e2e --collect-only -q`.

- [ ] **Step 3: Full unit suite**

Run: `pytest tests/ -n auto -q`
Expected: only the known baseline failures listed in Global Constraints.

- [ ] **Step 4: Manual smoke (local server)**

Run `USE_LOCAL_DATA=True JWT_SECRET=<32+ chars> uvicorn main:app --port 8123`, open the standings page and the playoff race page, confirm the nav toggles when `latest_week` is stubbed, and note the result in the task report.

- [ ] **Step 5: Commit**

```bash
git add tests_e2e/test_playoff_and_weekly.py tests_e2e/test_standings.py
git commit -m "test: e2e coverage for playoff race nav gating and tiebreaker tooltip"
```
