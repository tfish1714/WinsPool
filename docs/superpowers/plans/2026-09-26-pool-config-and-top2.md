# Pool Config Per Season, Admin Pool Tab, and Top-2 Finish Odds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make pool fee and payouts configurable per season (default fee 200, payouts 1st 1400 and 2nd 600, dollar amounts, optional last-place payout) via a new admin Pool tab, and replace the profile card's per-team playoff proxy with the player's own probability of finishing top 2 in the pool.

**Architecture:** Per-season config lives in the existing `config/settings` doc under `pool_config: {"<season>": {entry_fee, payouts}}` and is read/written through `services/pool_service.py` (read-modify-write, so one season never clobbers another, including in the local json mirror). The top-2 odds are a vectorized Monte Carlo over every player's portfolio, using per-team projected wins and spread scaled to the games remaining.

**Tech Stack:** FastAPI, pandas, numpy, pytest, vanilla JS + Jinja2.

**Spec:** Sprint follow-up requests in this conversation (rulings 2, 3, 6 of `docs/superpowers/plans/2026-09-26-sprint-features-and-hardening.md`).

## Global Constraints

- NO emojis anywhere (code, comments, commits, docs, UI strings).
- Zero deletion of existing features/tests: additive or exact replacements only. The existing `compute_portfolio_projection` output keys stay (per-team playoff_prob fields remain in the response; the UI simply prioritizes the new fields).
- TDD: failing test first.
- Payouts are dollar amounts (source of truth), not percentages. Defaults when a season has no config: `entry_fee=200`, `payouts=[{place:1, amount:1400}, {place:2, amount:600}]`.
- Payout `place` is a positive integer or the string `"last"` (last-place money back).
- The old global keys `pool_entry_fee`/`pool_payouts` were never deployed; stop writing them, keep stripping them from the public `/api/config/settings` response and additionally strip `pool_config` there.
- Never run deploy, never write to Firestore. Never `git add` anything under `.superpowers/`.
- Projection gating (CLAUDE.md): non-admins must not receive projection-derived data while `draft_active`; the new odds obey the same gate as the existing portfolio route.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`. Docs need `git add -f` if ignored.

## Review Focus

- Saving season B's config must not overwrite season A's (both Firestore merge and local json shallow merge).
- Payout total not equal to the pot (member count changed) must be reported, not rejected and not crash.
- A season with 1 to 2 players, or a player with zero teams, must not break the top-2 odds (probability 1.0 for 2 or fewer players is acceptable; zero teams returns unavailable/zeros).
- Ties between players in the simulation must be broken fairly (random tie-break), not always in favor of a low playerId.
- A fully completed season (no games remaining) must give deterministic 0 or 1 odds from actual wins.
- Admin config endpoints require admin; non-admin gets 403; negative fee or negative amount rejected.

---

## Task 1: Per-season pool config and admin Pool tab

**Files:**
- Modify: `services/pool_service.py`, `routes/api_routes.py` (`get_pool_status`, `/api/config/settings` strip), `routes/admin_routes.py` (replace `POST /api/admin/pool/config`, add `GET /api/admin/pool/config`), `routes/models.py`, `templates/admin.html`, `static/js/pool_fee.js` (payout labels incl. last place), `static/style.css` if needed
- Create: `static/js/admin_pool.js`
- Test: `tests/test_pool_service.py` (append and adjust existing assertions that encode percentages: replace, do not delete coverage), `tests/test_config_api.py`, new `tests/test_admin_pool_config.py`

**Interfaces:**
- Produces: `pool_service.DEFAULT_ENTRY_FEE = 200`, `pool_service.DEFAULT_PAYOUTS = [{"place": 1, "amount": 1400}, {"place": 2, "amount": 600}]`.
- Produces: `pool_service.get_pool_config(settings: dict, season: int) -> {"entry_fee": float, "payouts": [{"place": int | "last", "amount": float}]}` reading `settings["pool_config"][str(season)]`, falling back to defaults per missing field; invalid entries dropped or defaulted, never raises.
- Produces: `pool_service.set_pool_config(season: int, entry_fee: float, payouts: list) -> dict` read-modify-write of the whole `pool_config` map through `get_config_settings()` then `set_config_settings({"pool_config": merged})` (merged contains all seasons); returns the saved season config.
- Produces: `build_pool_status(order_df, settings, season, player_id)` now returns `{"season","entry_fee","total_count","paid_count","total_pot","collected","payouts":[{"place","label","amount"}],"payout_total","pot_balance","my_paid"}` where `label` is "1st"/"2nd"/"3rd"/"4th"... or "Last place", `payout_total` = sum of amounts, `pot_balance = total_pot - payout_total` (may be negative or positive; informational). Payouts sorted with numeric places ascending then "last".
- Produces: `GET /api/admin/pool/config?season=<int>` (admin) returns `{"season","entry_fee","payouts","member_count","total_pot","payout_total","pot_balance","is_default": bool}`; `POST /api/admin/pool/config` body `{season: int, entryFee: float >= 0, payouts: [{place: int>=1 | "last", amount: float >= 0}]}` (admin) validates no duplicate places, at most 10 entries, saves via `set_pool_config`, returns the same shape as GET.

- [ ] **1a Tests first**: `get_pool_config` defaults (no config -> 200 and 1400/600), per-season isolation (config for 2026 does not affect 2027), invalid data falls back; `set_pool_config` merges seasons (patch `get_config_settings`/`set_config_settings`; assert the saved map still contains the other season); `build_pool_status` with 10 members, fee 200 -> total_pot 2000, payout_total 2000, pot_balance 0, labels "1st","2nd"; with a `"last"` payout of 200 and 1st 1400, 2nd 400 -> label "Last place" sorted last; fee changed to 150 with defaults payouts -> pot_balance = 1500 - 2000 = -500; `my_paid` behavior unchanged; admin GET returns defaults with `is_default` True; POST rejects negative fee, negative amount, duplicate places, place 0, place "middle" (400/422); non-admin 403; POST 2027 then GET 2026 unchanged; public `/api/config/settings` response contains none of `pool_config`, `pool_entry_fee`, `pool_payouts`.
- [ ] **1b Implement** service, routes, models (replace the old percentage-based `PoolConfigRequest` and its route; keep the route path `/api/admin/pool/config`). `get_pool_status` reads the requested/active season's config via `get_pool_config`.
- [ ] **1c Admin Pool tab**: add a tab button `Pool` (`data-tab="pool-section"`) and a `#pool-section` panel to `templates/admin.html` following the existing tab pattern (check how tabs are wired in `static/js/admin_main.js` and `admin_accuracy.js`, and load `admin_pool.js` the same way). The panel: season selector (populate from seasons that have draft_order, default active season; reuse an existing admin seasons source or `/api/admin/members/{season}`), entry fee number input, a payouts table with rows of [place select: 1..10 plus "Last place"] [amount input] [remove button], "Add payout" button, a live summary line (members, pot = fee x members, payouts total, balance shown as "Unallocated $X" or "Over-allocated $X"), Save button, and a status message. Build DOM with createElement/textContent only (no innerHTML with server data). Fetch with the same auth pattern as `admin_accuracy.js` (Bearer token from `nfl_wins_token` plus cookies). Mobile friendly at 390px (rows wrap). Use existing admin CSS classes and theme tokens.
- [ ] **1d Public banner**: update `static/js/pool_fee.js` to render `label` and `amount` per payout (money formatting with dollars), and show the pot balance warning only when nonzero is NOT required for members (do not show balance to members); hide banner when `entry_fee` is 0.
- [ ] **1e Run** `pytest tests/test_pool_service.py tests/test_admin_pool_config.py tests/test_config_api.py tests/test_admin_routes.py -q`, then the full suite comparing failing ids with the baseline of 34 known ids (29 failed + 5 errors, environment-caused; the list is in `$WS/baseline_failures.txt`, strip the trailing message before diffing). Commit.

## Task 2: Top-2 finish probability on the profile card

**Files:**
- Modify: `services/analysis_service.py`, `routes/api_routes.py` (`/api/profile/portfolio`), `templates/profile.html`
- Test: `tests/test_pool_odds.py` (new), extend `tests/test_portfolio_projection.py` only by appending

**Interfaces:**
- Produces: `analysis_service.simulate_pool_finish_odds(player_teams: dict[int, list[str]], team_projections: dict, team_records: dict, season_games: int = 17, n_sims: int = 10000, seed: int = 0, top_n: int = 2) -> dict[int, dict]` returning per player id `{"top_n_prob": float, "win_prob": float, "expected_rank": float}`.
  Per team: `played = wins+losses+ties` from `team_records[team]` (`{"wins","losses","ties"}`; missing -> 0), `remaining = max(season_games - played, 0)`, `mean = wins + remaining/season_games * projected_wins`, `sd = std_dev * sqrt(remaining/season_games)` floored at 0.5 only when `remaining > 0` (when `remaining == 0`, wins are exact). Team wins sampled independently from Normal(mean, sd) clipped to `[wins, wins + remaining]`. A player's total is the sum over their teams. Rank players per simulation with descending total and a random uniform tie-break (add a tiny random jitter smaller than any real gap, or use lexsort with a random key). `top_n_prob` = fraction of sims where rank <= top_n; `win_prob` = fraction with rank 1. `seed` makes results reproducible (use `numpy.random.default_rng(seed)`). Players with no projected teams get zero totals. With <= top_n players, `top_n_prob` is 1.0.
- Produces: `/api/profile/portfolio` response gains `top2_prob`, `win_prob`, `expected_rank`, `pool_size` (all floats/int; `top2_prob`/`win_prob` are None when unavailable). Existing keys unchanged. Same gating and unavailable reasons as before. Team records come from the loaded standings for the active season (see how the existing route/services obtain `nfl_standings` via `load_data()`; standings rows have team, wins, losses, ties).

- [ ] **Tests first** (`tests/test_pool_odds.py`): (1) two players, one holding a clearly stronger team set -> stronger player win_prob near 1 and weaker near 0 with `top_n=1`; (2) three players identical teams -> win_prob each within 0.05 of 1/3 (fair tie-break) and top-2 near 2/3, with n_sims 20000 and a fixed seed; (3) completed season (all teams played 17) -> results exactly 0.0 or 1.0 and match actual totals; (4) reproducible: same seed same output; (5) player with zero teams -> zeros, no exception; (6) two-player pool with top_n=2 -> 1.0; (7) in-season: a team already at 12 wins with 2 games left cannot exceed 14 (assert via a simulation where the only alternative is 13 wins clearly lower); route tests via TestClient (override `require_auth`, patch loaders as the existing portfolio route tests do): response includes `top2_prob` between 0 and 1 for a normal case, None with `available` False for `draft_in_progress`, `no_teams`, `no_projections`.
- [ ] **Implement** the function (vectorized numpy: shape (n_sims, n_teams) sampling, then per-player sums via a player-by-team indicator matrix, ranks via `argsort` of `-(totals + rng.random(totals.shape)*1e-6)`) and wire the route.
- [ ] **Frontend**: in `templates/profile.html` Season Outlook card, show two headline stats first: "Chance to finish top 2: NN%" and "Chance to win the pool: NN%" (format with rounding; treat null as hidden), keep expected wins and floor-ceiling, and move the per-team playoff percentages into the per-team list as before (they describe the NFL team, label them "team playoff chance"). Keep the un-awaited IIFE structure (the card load must never block the form). textContent only.
- [ ] Run new tests plus `pytest tests/test_portfolio_projection.py tests/test_auth.py -q`, then the full suite vs baseline ids. Commit.

## Task 3: Docs

- [ ] `CLAUDE.md`: fix the stale `static/style.css` line under Module Layout (it says to bump `?v=N` in `base.html`; the template uses `static_url()` which hashes file contents, so no manual bump is needed; verify in `templates/base.html` and `main.py`'s `static_url` before editing); update the "Newer API endpoints" section for per-season dollar-amount pool config, the admin GET/POST, `payout_total`/`pot_balance`, and the new `top2_prob`/`win_prob`/`expected_rank`/`pool_size` fields; update `docs/prediction_model.md` Player Portfolio Projection section with the Monte Carlo description (independence approximation, remaining-games scaling, random tie-break, top-2 default) keeping the earlier per-team playoff proxy text.
- [ ] Full suite vs baseline, commit.
