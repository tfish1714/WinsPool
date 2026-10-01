# Per-Game Pool Finish Odds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Drive the profile card's top-2 / win odds from the stored weekly per-game win probabilities over the actual remaining schedule (capturing head-to-head games between pool teams), falling back to the existing per-team normal approximation when no per-game predictions exist; also remove the never-used `pool_entry_fee`/`pool_payouts` legacy keys.

**Architecture:** New `analysis_service.simulate_pool_finish_odds_from_games(...)` samples every unplayed regular-season game once (home wins with the stored probability), adds the outcomes to each team's actual wins, and ranks pool players per simulation with a random tie-break. The `/api/profile/portfolio` route builds the remaining-game list from schedule plus stored `game_predictions` and picks per-game when available.

**Tech Stack:** FastAPI, pandas, numpy, pytest.

## Global Constraints

- NO emojis anywhere. Zero deletion of existing features/tests except the explicitly requested legacy-key removal below. TDD. Never deploy, never write to Firestore, never `git add` anything under `.superpowers/`. Docs need `git add -f` if ignored. Commit trailer `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- Stored predictions: `cache_service.get_game_predictions(season)` returns `{"W07_JAX_LA": {"pred_prob": <HOME win probability>, "pred_winner": ..., ...}}` keyed `W{week:02d}_{HOME}_{AWAY}` (team abbreviations normalized via `services.utils.normalize_team_abbr`). `pred_prob` is the home team's win probability (verify against `scripts/cache_builder.py` / `services/utils.py::derive_prediction_scalars` before relying on it and state what you confirmed).
- Projection gating and unavailable reasons of `/api/profile/portfolio` are unchanged.
- Existing `simulate_pool_finish_odds` and all its tests stay untouched.

## Review Focus

- A game already played (result present) must never be re-simulated; actual wins come from standings/results.
- An unplayed game missing a stored prediction uses 0.5 and is counted so the response can say how many games used fallback; if there are NO remaining games with stored predictions, the route falls back to the old team-level function.
- Each game is sampled ONCE for both teams (a pool player holding both sides of a game must get exactly one win from it).
- Random fair tie-break; same seed gives same output; a completed season gives exact 0/1 odds; <= top_n players gives 1.0; players with no teams get zero totals.

---

## Task 1: Per-game odds and legacy key removal

**Files:** Modify `services/analysis_service.py`, `routes/api_routes.py` (portfolio route; public `/api/config/settings` strip), `tests/test_config_api.py`, `docs/prediction_model.md`, `CLAUDE.md` (endpoint bullet); Create `tests/test_pool_odds_games.py`

**Interfaces:**
- Produces: `simulate_pool_finish_odds_from_games(player_teams: dict[int, list[str]], base_wins: dict[str, float], remaining_games: list[tuple[str, str, float]], n_sims: int = 10000, seed: int = 0, top_n: int = 2) -> dict[int, dict]` returning per player `{"top_n_prob","win_prob","expected_rank"}` (same shape as the existing function). `remaining_games` items are `(home_team, away_team, home_win_prob)`. `base_wins` maps team to actual wins to date (missing -> 0).
- Produces: `/api/profile/portfolio` gains `odds_basis` in `{"per_game", "team_projection", None}` and `games_remaining` (int or None); other keys unchanged.

- [ ] Tests first (`tests/test_pool_odds_games.py`): (1) one remaining game A(home) vs B with prob 0.7, player 1 holds A, player 2 holds B, top_n=1: win_prob near 0.7 / 0.3 (tolerance 0.02 at 20000 sims); (2) one player holds BOTH A and B: their total is always exactly 1 from that game (assert expected_rank unaffected and, using a third player with a fixed base_wins of 0.5 ... design an assertion that fails if the game is sampled twice independently); (3) base_wins shift the odds (a player already 2 wins ahead with one coin-flip game left has win_prob 1.0 when the game cannot close the gap); (4) no remaining games: exact 0.0/1.0 from base_wins; (5) same seed same output; different tie players near 1/N over many sims (fair random tie-break); (6) player with no teams zero totals; <= top_n players gives top_n_prob 1.0; (7) route tests (patch loaders like the existing portfolio route tests): when stored predictions exist for remaining games `odds_basis == "per_game"` and `games_remaining` matches; when none exist `odds_basis == "team_projection"` and the old numbers still returned; an unplayed game without a stored prediction is simulated at 0.5 and still counted in `games_remaining`; (8) legacy keys: `/api/config/settings` no longer needs to strip `pool_entry_fee`/`pool_payouts` (they are never written); update `tests/test_config_api.py` accordingly by keeping the `pool_config` non-leak assertion and dropping only the two legacy-key assertions and fixture entries (the user explicitly asked to delete the never-used legacy keys), and remove those two names from the strip tuple in `routes/api_routes.py`.
- [ ] Implement the function (vectorized: sample `rng.random((n_sims, n_games)) < probs`, accumulate home/away win indicators into a `(n_games, n_teams)` matrix multiply, add base wins, sum per player with an indicator matrix, rank with jittered argsort as in the existing function) and wire the route: from `load_data()` obtain the season's games (REG only, `result` null = unplayed), build `remaining_games` using stored `get_game_predictions(season)` (key `W{week:02d}_{home}_{away}`, normalized abbreviations; missing prediction -> 0.5), and `base_wins` from the same standings/records the route already uses. Keep the whole odds block inside its existing try/except so a failure never breaks the base response.
- [ ] Frontend `templates/profile.html`: keep the two headline stats; add a small muted line under them when `odds_basis == "per_game"`: "Based on N remaining games" and when `team_projection`: "Based on preseason team projections" (textContent only; keep the un-awaited IIFE structure).
- [ ] Docs: update the "Pool finish odds" subsection of `docs/prediction_model.md` and the portfolio bullet in `CLAUDE.md` for the per-game method, the 0.5 fallback, `odds_basis`, and `games_remaining`; remove mention of the legacy keys if any remain.
- [ ] Run affected tests, then the full `python -m pytest tests/ -n auto -q -p no:cacheprovider --color=no`; compare failing ids to `$WS/baseline_failure_ids.txt` (29 failed + 5 errors pre-existing). Commit.
