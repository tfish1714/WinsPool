# Team Page and Player Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** (1) Add a team page `/team/{abbr}` showing each pool season's wins, who drafted the team, and that season's pool winner, plus this season's schedule with the projected win/loss overlaid, with a native dropdown to jump between teams and a "Teams" entry in the More menu. (2) Link player names on the standings page to `/player/{id}` with a mobile-safe tap target, and link team abbreviations in the player page's pick table to the team page.

**Architecture:** A pure service function builds the team-page payload from already loaded data (draft results, standings, games, stored game predictions, projections). A thin route renders `templates/team.html`, which embeds the payload as JSON and renders with vanilla JS (textContent only), matching the player page pattern. The standings player-name link is added to all three server-rendered variants and to the 30s live refresh renderer.

**Tech Stack:** FastAPI, Jinja2, vanilla JS, pandas, pytest.

## Global Constraints

- NO emojis in anything written (code, comments, UI strings, docs, commits).
- Zero deletion of existing features/tests (additive or exact replacements only).
- Team abbreviations are normalized with `services.utils.normalize_team_abbr`; unknown abbreviation -> 404. Team names/logos come from existing helpers (`get_team_logo`, `nfl_teams` data); reuse how other pages resolve them.
- Pool seasons only: the history table lists only seasons where the team appears in `draft_results`.
- Projection gating (CLAUDE.md): projected wins and any preseason-projection-derived value are hidden from non-admins while `draft_active` is True (same gate as `/api/profile/portfolio`). Per-game win probabilities: expose them exactly as the existing Schedule page already does for non-admins (inspect `routes/` and `templates/schedule*.html`); if the Schedule page shows them to everyone, the team page may too; otherwise apply the same gate.
- Mobile first: every tap target at least 44px tall, no two links adjacent within a row, no new links inside dense chips. The team dropdown is a native `<select>`. On the standings page ONLY the player name becomes a link (one link per player per rendering); team logos/chips stay non-links there.
- Nav parity (CLAUDE.md gotcha): the "Teams" entry must be added to BOTH `static/js/main.js` `updateNav()` `moreLinks` AND the hardcoded drawer in `templates/base.html`; `tests_e2e/test_nav_parity.py` covers it (cannot be run here; add the path to its page list only if that file's structure calls for it).
- `/teams` (the More-menu target) redirects to the team page of the viewer's first drafted team in the active season when a valid `session_token` cookie identifies them, otherwise to the first team alphabetically.
- Never deploy, never write Firestore, never `git add` anything under `.superpowers/`, `.local_db/` or `*.png`. Commit trailer `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`. Docs need `git add -f`.
- This worktree is `C:\Users\fisch\OneDrive\Documents\Code\WinsPool\.claude\worktrees\team-page-and-links` (branch `sprint-team-page-and-links`); work only there. Tests that touch `.local_db/` must not pollute a developer's local data: if you find a test that writes into `.local_db`, use `tmp_path`/monkeypatch for your new tests.

## Review Focus

- A team that was never drafted, or a team missing from standings for a season, must not 500 (empty history, "Undrafted"-free: pool seasons only means it simply has no rows).
- Season with no games played yet: record 0-0, schedule rows all unplayed, overlay uses predictions (or "no prediction" placeholder), never NaN or "nan".
- Bye weeks appear as a row (no opponent) or are omitted deliberately; documented either way.
- Tie games and games with a null result are not counted as wins or losses.
- Pool winner note for a season with no completed games or no players must not crash; ties for first are handled deterministically (use the same ranking the app already uses for standings tiebreakers).
- The standings 30s live refresh must keep the player-name link (not strip it on re-render), and the link must not break the stacked mobile card or the existing tiebreaker tap behavior.

---

## Task 1: Team page and Teams nav entry

**Files:**
- Create: `services/team_page_service.py`, `templates/team.html`, `static/js/team_page.js`, `tests/test_team_page.py`
- Modify: `routes/history_routes.py` (or a new `routes/team_routes.py` registered in `main.py`; prefer adding to `history_routes.py` to avoid registration changes), `static/js/main.js` (`moreLinks`), `templates/base.html` (drawer), `static/style.css` (scoped `.team-page*` rules), `CLAUDE.md`

**Interfaces:**
- Produces: `team_page_service.build_team_page(team: str, season_hint: int | None, data: dict, include_projections: bool) -> dict | None` returning `None` for an unknown team and otherwise
  `{"team": "KC", "name": "...", "logo": "...", "current_season": 2026, "teams": [{"abbr","name"}...32], "history": [{"season", "wins", "losses", "ties", "drafter": {"playerId","name"} | None, "pick": int | None, "pool_winner": {"playerId","name","wins"} | None}], "current": {"record": {"wins","losses","ties"}, "projected_wins": float | None, "schedule": [{"week", "opponent", "home": bool, "status": "played"|"unplayed"|"bye", "result": "W"|"L"|"T"|None, "score": "24-17" | None, "win_prob": float | None, "projected": "W"|"L"|None}], "projected_record": {"wins","losses"} | None}}`.
  `data` is whatever the route gets from `load_data()` plus stored game predictions; define the exact dict keys in the tests and document them. History sorted newest first. `projected` for an unplayed game is "W" when the stored home-win probability favors this team (`pred_prob` is the HOME win probability; for an away team use `1 - pred_prob`), else "L"; `win_prob` is this team's win probability. `projected_record` = actual wins/losses to date plus projected W/L over unplayed games. All projection-derived fields are `None` when `include_projections` is False (per gating) but the dropdown, history and results still render.
- Produces: `GET /team/{abbr}` (HTML, case-insensitive abbr, 404 for unknown), `GET /teams` (redirect as in Global Constraints), More-menu entry "Teams" -> `/teams` in both navs.

- [ ] Tests first (`tests/test_team_page.py`): service unit tests with small in-memory frames: history rows only for drafted seasons with correct drafter/pick and pool winner; a team never drafted returns empty history; current schedule overlay math (played W/L, unplayed projection from pred_prob with home/away orientation, projected_record sums correctly, ties and null results not counted); `include_projections=False` nulls `projected_wins`, `win_prob`, `projected`, `projected_record`; bye week handling; unknown team returns None; route tests with TestClient: 200 for `/team/kc` and `/team/KC`, 404 for `/team/XXX`, `/teams` redirect with and without a valid cookie, rendered HTML contains the `<select>` with 32 options and the embedded JSON with no `nan` strings; a nav test asserting both `static/js/main.js` and `templates/base.html` contain the `/teams` link.
- [ ] Implement service, route, template (header with logo/name/current record/projected wins; dropdown `<select>` that navigates to `/team/{abbr}` on change; History table (season, record, drafted by [player name is a link to `/player/{id}`], pick, pool winner note as plain text "Pool winner: Name (N wins)"); This Season schedule list: week, "vs"/"@" opponent, result or projected badge styled with existing tokens (`--pos`/`--neg`, muted for projected), projected record line). Render via `team_page.js` (textContent only). Mobile: single column, rows at least 44px, horizontal scrolling avoided (stack columns under 480px).
- [ ] Add "Teams" to both navs. Update `CLAUDE.md`. Run targeted tests. Commit.

## Task 2: Standings player links and player-page team links

**Files:** Modify `templates/wins_pool.html`, `static/js/standings_refresh.js` (and any file that re-renders standings rows), `static/style.css`, `static/js/player_profile.js` and/or `templates/player_profile.html` (pick cells), tests in `tests/` for markup contracts, `CLAUDE.md`.

- [ ] Inspect all standings renderings (desktop leader card, desktop rows, stacked mobile cards, and the JS that re-renders on the 30s poll) and existing tap behaviors on rows (tiebreaker explain etc.). Make the player NAME the only new link in each rendering, pointing to `/player/{playerId}` (the standings payload must carry `playerId`; add it to the row data/JS payload if missing), with padding so the tap target is at least 44px tall, `min-width` sufficient, no underline clutter (use the existing link/accent token and a subtle affordance), and ensure the link does not trigger any row-level click handler (stopPropagation only if a row handler exists). Team logos on standings remain non-links.
- [ ] On the player page pick cells (history table), make the team abbreviation a link to `/team/{abbr}` with a comfortable tap area (the cell is a compact block, so link only the abbreviation text with vertical padding, never the whole cell), and keep all other cell content non-linked.
- [ ] Tests: markup contracts (rendered `wins_pool.html` contains `/player/{id}` links for each player row in every variant and none wrapping team logos; the JS renderer output includes the link, testable by asserting the source template string in `standings_refresh.js` or, if a pure function exists, unit-testing it via node is not available so assert on source), player page markup links teams to `/team/`. Update `CLAUDE.md`. Run targeted tests. Commit.
