# Smart Live Scores and Real-Time Standings Design

**Date:** 2026-09-20  
**Status:** Implemented on `worktree-smart-live-scores` (2026-09-24), except Part 2's Cloud Scheduler `*/2` cadence change, which is a manual `gcloud scheduler jobs update` and has not been applied. Deviation: the container has no `rawdata/`, so the window check does one small nflverse schedule GET instead of reading a local file (fails open on error).  
**Target Areas:** `scripts/sync_live_scores.py`, `services/live_score_service.py`, `routes/api_routes.py`, `templates/wins_pool.html`, `static/js/standings.js`

---

## 1. Context & Motivation

### The Cloud Run Waste Problem
The `winspool-live-scores` Cloud Run Job is configured in Cloud Scheduler with the cron expression `*/5 * * 9-12,1 *`. It runs every 5 minutes, 24 hours a day, 7 days a week, from September through January (and via `-trigger-feb` through February 10).

This equates to:
- 288 executions per day
- 2,016 executions per week
- Over 43,000 executions per NFL season

In reality, NFL games are played only during narrow windows across 3 to 4 days per week:
- **Thursday:** ~8:15 PM to 11:30 PM ET
- **Sunday:** ~9:30 AM ET (London/Germany international games) or ~1:00 PM ET through ~11:45 PM ET (SNF)
- **Monday:** ~8:15 PM to 11:30 PM ET
- **Occasional exceptions:** Black Friday afternoon, Saturday doubleheaders in Weeks 15-18, Christmas Day, and Playoff weekends.

Across an entire week, live NFL football is played for roughly 20 to 24 total hours. Over **88% to 90% of all scheduled live-score runs execute when zero NFL games are kicking off, active, or concluding**.

Every unnecessary run carries concrete costs:
1. Spins up a container instance on Cloud Run.
2. Executes `sync_nflverse_data.py --priority 1` as a subprocess.
3. Loads and parses CSV data from disk.
4. Executes Firestore batch operations (read/diff/write) against `nfl_standings` and `nfl_games`.
5. Sends HTTP requests to ESPN's public scoreboard API.
6. Emits `signal_data_update(DOMAIN_ACTIVE)` cache invalidation signals.

### The Real-Time Standings Gap
On the client side:
- `templates/schedule.html` already has client-side dynamic polling via `static/js/live_refresh.js`, which polls `/api/live-scores?year={year}` every 30 seconds and patches scores, quarter, clock, and possession in the DOM without a page reload.
- `templates/wins_pool.html` (the core leaderboard) has no real-time refresh. Users tracking their pool on game days must manually pull-to-refresh or reload their browser to observe win changes, point differential shifts, or live game status updates.

### The Pre-Existing `sync_live_scores_to_df()` Bug
As documented in `docs/superpowers/specs/2026-09-19-live-score-service-followups.md`:
In `services/live_score_service.py::sync_live_scores_to_df()`, scores and result differentials are assigned unconditionally:
```python
df.at[idx, 'home_score'] = update['home_score']
df.at[idx, 'away_score'] = update['away_score']
df.at[idx, 'result'] = update['home_score'] - update['away_score']
```
When ESPN returns scheduled games that have not yet kicked off (where score is `0 - 0` and status is `STATUS_SCHEDULED`), this logic sets `result = 0`. Downstream in `services/analysis_service.py::compute_team_records`, any row with `pd.notna(result)` is classified as a completed game, and `result == 0` is classified as a tie. This corrupts team records for unplayed games during game days if invoked via `cache_builder.py`.

---

## 2. Architecture & Technical Design

### Part 1: Fast Exit and Dynamic Active Window in `scripts/sync_live_scores.py`

Instead of running full synchronization logic unconditionally, `scripts/sync_live_scores.py` will determine whether the current timestamp falls within an **Active Game Window** before executing any subprocesses, Firestore queries, or network requests.

#### Active Window Determination Algorithm
All NFL schedule kickoffs in `rawdata/schedules/games.csv` specify `gameday` (date string `YYYY-MM-DD`) and `gametime` (Eastern Time string `HH:MM`).

Let `now_et` be the current datetime in the `America/New_York` timezone:
1. Load the current season schedule from `rawdata/schedules/games.csv` via `load_games()`.
2. Inspect all regular season and postseason games scheduled for today (date matching `now_et.date()`), plus any games from yesterday that could theoretically still be in overtime/delay (kickoff within the last 5 hours).
3. If no games match today:
   - The window is **inactive**.
4. If games match today:
   - Find `earliest_kickoff_et = min(game_kickoffs)`.
   - Find `latest_kickoff_et = max(game_kickoffs)`.
   - Calculate `window_start = earliest_kickoff_et - 20 minutes` (buffer for pre-game coverage and early lock verification).
   - Check completion status of all games scheduled today:
     - If all games scheduled today are marked final (e.g., `pd.notna(result)` and result is non-sentinel), calculate `window_end = latest_kickoff_et + 30 minutes` after the final game concluded, or immediately close after a 30-minute settling buffer.
     - If any games scheduled today are not yet final: calculate `window_end = latest_kickoff_et + 4.5 hours` (standard 3-hour game window plus 1.5-hour overtime/weather-delay buffer).
   - If `window_start <= now_et <= window_end`:
     - The window is **active**.
   - Otherwise:
     - The window is **inactive**.

#### Fast Exit Execution
At the very top of `main()` in `scripts/sync_live_scores.py`:
```python
if not is_live_score_window_active() and not args.force:
    print("Outside active NFL game window. Exiting immediately.")
    sys.exit(0)
```
- **Execution Time:** Less than 0.2 seconds.
- **Resource Usage:** Zero subprocess execution, zero external network requests, zero Firestore reads/writes.
- **CLI Flag:** A `--force` argument will be provided to allow manual execution during development, testing, or ad-hoc operational runs outside window hours.

---

### Part 2: In-Window Cadence Tuning (Cloud Scheduler)

Because the fast-exit logic eliminates ~89% of resource consumption outside game windows, the execution cadence during game windows can be made significantly more aggressive without increasing monthly billing:
- **Current Schedule:** `*/5 * * 9-12,1 *` (every 5 minutes, 288 runs/day).
- **Proposed Schedule:** `*/2 * * 9-12,1 *` (every 2 minutes, 720 runs/day nominal).
  - Outside windows (~21 hours/day): 630 runs exit in <0.2s with zero billable API operations.
  - Inside windows (~3 hours/day): ~90 runs execute full sync every 2 minutes instead of every 5 minutes.
  - Users experience game updates with less than half the previous latency.

---

### Part 3: Real-Time Standings Refresh on `wins_pool.html`

#### Polling vs. WebSocket Evaluation
| Dimension | WebSockets (e.g., FastAPI WebSocket / Socket.io) | Smart HTTP Polling (30s cadence) |
| :--- | :--- | :--- |
| **Cloud Run Suitability** | Requires persistent connections, prevents container scale-to-zero, requires session affinity and increased memory/CPU limits. | Fully stateless; leverages serverless scale-to-zero and existing reverse proxy infrastructure. |
| **Mobile Reliability** | Mobile browsers aggressively terminate background WebSockets when the screen locks or tabs switch, requiring reconnect handshakes. | Native `document.hidden` pause/resume handles backgrounding cleanly with zero zombie connection overhead. |
| **Architectural Fit** | Introduces stateful connection management not present anywhere else in the codebase. | Matches existing, verified pattern used in `static/js/live_refresh.js` on `schedule.html`. |

**Decision:** Adopt Smart HTTP Polling.

#### Standings API Endpoint: `/api/live-standings`
Add an endpoint in `routes/api_routes.py` returning lightweight standings data for client-side consumption:
```python
@router.get("/live-standings")
def get_live_standings(year: int):
    """Returns player standings, wins, point differentials, and current active
    team game indicators for client-side live leaderboard updates."""
```
**Payload Schema:**
```json
{
  "year": 2026,
  "last_updated": "2026-09-20T16:45:00-04:00",
  "standings": [
    {
      "rank": 1,
      "player_id": 4,
      "full_name": "Player Name",
      "total_wins": 7,
      "teams": [
        {"abbr": "KC", "wins": 3, "pt_diff": 18, "is_live": true, "live_score": "KC 17 - LAC 14", "period": 3, "clock": "4:12"},
        {"abbr": "BUF", "wins": 2, "pt_diff": 10, "is_live": false},
        {"abbr": "DET", "wins": 2, "pt_diff": 6, "is_live": false}
      ],
      "tiebreakers": {
        "tb1": 2,
        "tb2": 2,
        "tb3": 3,
        "tb4": 6,
        "tb5": 10,
        "tb6": 18
      }
    }
  ]
}
```

#### Client-Side DOM Updating (`static/js/standings_refresh.js`)
- Runs on `templates/wins_pool.html`.
- Polls `/api/live-standings?year={year}` every 30 seconds.
- Uses `if (document.hidden) return;` to pause while in the background.
- Updates:
  1. Hero card (Rank 1): Total wins, team win counts, point differential badges.
  2. Desktop standings rows: Rank numbers, total wins, team stat lines, tiebreaker values.
  3. Mobile stacked cards: Score labels, team stat summaries, tiebreakers.
  4. Visual indicators: Adds subtle `status-live` pulsing indicator next to teams currently playing live.

---

### Part 4: Live Score Service Correctness Fix

Resolve the outstanding issue from `docs/superpowers/specs/2026-09-19-live-score-service-followups.md`:

#### 1. Add Guard in `services/live_score_service.py::sync_live_scores_to_df()`
Prevent unplayed scheduled games from assigning `result = 0`:
```python
# Only update scores and result if the game is active or final
if not is_live_status(update.get("status")) and update.get("status") != "STATUS_FINAL":
    continue
```
Do not overwrite rows for scheduled games whose kickoff has not occurred.

#### 2. Address `cache_builder.py` Redundancy
`cache_builder.py::build_year()` currently invokes `sync_live_scores_to_df()`. Ensure `cache_builder.py` uses the sanitized live score service without corrupting unplayed games, ensuring scheduled games retain `result = NaN`.

---

## 3. Non-Goals & Boundaries

1. **No Stateful WebSockets:** Persistent WebSocket connections will not be introduced due to Cloud Run container scaling constraints and mobile lifecycle drops.
2. **No Alteration of Authoritative Win Formulas:** Standings calculations remain driven strictly by official `nflverse` scores (`result` / `home_score` / `away_score`). ESPN scoreboard data remains display-only (`live_home_score`, `live_away_score`, `clock`, `period`).
3. **No Historical Rework:** Changes apply only to the active season window.

---

## 4. Test and Verification Plan

### Automated Pytest Suite
1. **Window Calculation Tests (`tests/test_live_score_window.py`):**
   - Tuesday afternoon with zero games scheduled -> returns `False`.
   - Sunday at 10:00 AM ET before standard 1:00 PM kickoffs -> returns `False`.
   - Sunday at 12:45 PM ET (15 minutes prior to 1:00 PM kickoff) -> returns `True`.
   - International game day at 9:15 AM ET (kickoff at 9:30 AM ET) -> returns `True`.
   - Sunday evening with SNF in progress -> returns `True`.
   - Sunday night at 12:30 AM ET after all games are marked final -> returns `False`.
   - `--force` flag overrides inactive window -> returns `True`.
2. **Scheduled Game Guard Tests (`tests/test_live_score_service.py`):**
   - Verify that ESPN updates with status `STATUS_SCHEDULED` (score 0-0) do not set `result = 0` on DataFrame rows.
   - Verify that active games (`STATUS_IN_PROGRESS`) and final games (`STATUS_FINAL`) update correctly.
3. **Standings Endpoint Tests (`tests/test_live_standings_route.py`):**
   - Validate response shape, status codes, and sanitization of NaN values for `/api/live-standings`.
4. **End-to-End Regression Suite:**
   - Execute full test suite: `pytest tests/`.
