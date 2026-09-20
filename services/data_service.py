import logging
import os
import pandas as pd

logger = logging.getLogger(__name__)
import numpy as np
import time
from typing import Tuple, Dict, Any, List, NamedTuple
from services.db_service import get_collection_df
from services.utils import get_team_logo_url
from services.constants import UNDRAFTED_SENTINEL, TEAMS_PER_PLAYER


class DataBundle(NamedTuple):
    """Named 7-tuple returned by load_data(). All fields are pandas DataFrames."""
    standings:          "pd.DataFrame"
    teams:              "pd.DataFrame"
    games:              "pd.DataFrame"
    players:            "pd.DataFrame"
    draft_order:        "pd.DataFrame"
    draft_results:      "pd.DataFrame"
    draft_order_rules:  "pd.DataFrame"


def get_team_logo(team_code: str) -> str:
    """Returns the official high-resolution logo URL for an NFL team."""
    return get_team_logo_url(team_code)

from services.cache_service import clear_data_cache

def check_remote_signals(use_local: bool) -> None:
    """Poll metadata/cache_control (at most once per _REMOTE_CHECK_INTERVAL)
    and clear any cache domain whose remote signal is newer than what this
    process has cached -- the only channel by which a separate process
    (winspool-predict-daily, or another web-service instance) can tell this
    process its cached data is stale.
    """
    import services.cache_service as cs
    current_time = time.time()
    if use_local or (current_time - cs._LAST_REMOTE_CHECK) <= cs._REMOTE_CHECK_INTERVAL:
        return
    cs._LAST_REMOTE_CHECK = current_time
    try:
        from services.db_service import get_db
        db = get_db()
        if not db:
            return
        ctrl = db.collection("metadata").document("cache_control").get(timeout=5)
        if not ctrl.exists:
            return
        remote = ctrl.to_dict()
        for domain, field in cs.DOMAIN_SIGNAL_FIELDS.items():
            remote_ts = remote.get(field, 0)
            if remote_ts > cs.get_domain_timestamp(domain):
                logger.info("Remote invalidation detected for domain '%s' (remote=%s, local=%s).",
                            domain, remote_ts, cs.get_domain_timestamp(domain))
                cs.clear_domain(domain)
    except Exception as e:
        logger.warning("Failed to check remote cache control: %s", e)


def _domain_is_fresh(domain: str) -> bool:
    """True while `domain`'s cached value is still inside _CACHE_TTL_SECONDS.

    Read through the module (not the name imported above) so a test or a
    runtime tweak of cache_service._CACHE_TTL_SECONDS is actually honoured.
    """
    import services.cache_service as cs
    return (time.time() - cs.get_domain_timestamp(domain)) < cs._CACHE_TTL_SECONDS


def _fetch_static_bucket():
    """The 5 collections that are never season-filtered -- one shared
    in-memory bundle instead of being re-fetched inside every year-keyed
    slot the old cache used."""
    return {
        "teams":             get_collection_df("nfl_teams"),
        "players":           get_collection_df("players"),
        "draft_order":       get_collection_df("draft_order"),
        "draft_results":     get_collection_df("draft_results"),
        "draft_order_rules": get_collection_df("draft_order_rules"),
    }


def _get_static_bucket():
    """No TTL here -- per the design spec, static is refreshed only by an
    explicit write-triggered clear or the remote-signal check, never on a
    routine cadence. Every writer that touches players/draft_order/
    draft_results/draft_order_rules already calls clear_data_cache(DOMAIN_STATIC)
    + signal_data_update(DOMAIN_STATIC) (see Task 4), so a TTL fallback here
    would only add a needless full 5-collection refetch on every warm
    instance once an hour, forever."""
    import services.cache_service as cs
    cached = cs.get_domain(cs.DOMAIN_STATIC)
    if cached is not None:
        return cached
    bucket = _fetch_static_bucket()
    cs.set_domain(cs.DOMAIN_STATIC, bucket)
    return bucket


def _bootstrap_games_standings():
    """Cold-start only: one unfiltered fetch of nfl_games/nfl_standings,
    used to determine the active season, then split in memory into the
    active/historical buckets so this fetch is never repeated per-bucket.
    """
    import services.cache_service as cs
    all_games = get_collection_df("nfl_games")
    all_standings = get_collection_df("nfl_standings")
    static = _get_static_bucket()
    season = get_active_season(all_games, static["draft_results"], static["draft_order_rules"])

    active_bucket = {
        "season": season,
        "games": all_games[all_games["season"] == season].copy() if not all_games.empty else all_games,
        "standings": all_standings[all_standings["season"] == season].copy() if not all_standings.empty else all_standings,
    }
    # `!= season`, not `< season`: nfl_games/nfl_standings routinely carry a
    # FUTURE season (next year's schedule is synced long before that season's
    # draft completes, so get_active_season() still resolves to the current
    # one). Splitting on `<` dropped those rows from both buckets and they
    # disappeared from load_data() entirely. A future season is just as frozen
    # as a past one from the cache's point of view -- only the active season
    # changes routinely -- so both belong here.
    historical_bucket = {
        "games": all_games[all_games["season"] != season].copy() if not all_games.empty else all_games,
        "standings": all_standings[all_standings["season"] != season].copy() if not all_standings.empty else all_standings,
    }
    cs.set_domain(cs.DOMAIN_ACTIVE, active_bucket)
    cs.set_domain(cs.DOMAIN_HISTORICAL, historical_bucket)
    return active_bucket, historical_bucket


def _get_active_bucket():
    import services.cache_service as cs
    cached = cs.get_domain(cs.DOMAIN_ACTIVE)
    if cached is not None and _domain_is_fresh(cs.DOMAIN_ACTIVE):
        # Season-rollover guard: if the static bucket's own draft data now
        # resolves to a different active season, this bucket is stale by
        # identity, not just by TTL -- rebuild regardless of cache age.
        static = _get_static_bucket()
        all_games_for_check = pd.concat([
            cs.get_domain(cs.DOMAIN_HISTORICAL)["games"] if cs.get_domain(cs.DOMAIN_HISTORICAL) else pd.DataFrame(),
            cached["games"],
        ], ignore_index=True) if not cached["games"].empty else cached["games"]
        current_active = get_active_season(all_games_for_check, static["draft_results"], static["draft_order_rules"])
        if current_active == cached["season"]:
            return cached
        # No clear_domain() here: _bootstrap_games_standings() unconditionally
        # set_domain()s both ACTIVE and HISTORICAL on the very next line, which
        # replaces value *and* timestamp. An extra eviction would be a second,
        # redundant invalidation of the same domain in the same call.
    active, _ = _bootstrap_games_standings()
    return active


def _get_historical_bucket():
    """No TTL here -- per the design spec, historical is "fetched once, then
    effectively permanent: refreshed only when an explicit ... signal fires
    ..., not on any routine cadence." A TTL fallback would force a full
    unfiltered nfl_games/nfl_standings refetch on every warm instance once
    an hour forever, which is exactly the read-volume cost this plan exists
    to eliminate."""
    import services.cache_service as cs
    cached = cs.get_domain(cs.DOMAIN_HISTORICAL)
    if cached is not None:
        return cached
    _, historical = _bootstrap_games_standings()
    return historical


def load_data(year: int = None):
    """
    Loads data for the given season year using the active/historical/static
    cache domains (see docs/superpowers/specs/2026-09-14-cache-mutability-
    redesign-design.md). year=None returns the full multi-year history.
    """
    use_local = os.environ.get('USE_LOCAL_DATA', 'False').lower() == 'true'
    check_remote_signals(use_local)

    active = _get_active_bucket()
    historical = _get_historical_bucket()
    static = _get_static_bucket()

    if year is None:
        games = pd.concat([historical["games"], active["games"]], ignore_index=True)
        standings = pd.concat([historical["standings"], active["standings"]], ignore_index=True)
    elif year == active["season"]:
        games = active["games"]
        standings = active["standings"]
    else:
        games = historical["games"][historical["games"]["season"] == year].copy() if not historical["games"].empty else historical["games"]
        standings = historical["standings"][historical["standings"]["season"] == year].copy() if not historical["standings"].empty else historical["standings"]

    teams = static["teams"]
    players = static["players"]
    draft_order = static["draft_order"]
    draft_results = static["draft_results"]
    draft_order_rules = static["draft_order_rules"]

    # Schema Healing — Ensure MixedCase column names for downstream logic
    RENAME_MAP = {
        'playerid': 'playerId',
        'fullname': 'fullName',
        'draftpick': 'draftPick',
        'draftorder': 'draftOrder',
        'teamid': 'teamId',
        'nickname': 'nickName',
        'score': 'TotalWinsBySeason'
    }
    for df in [standings, teams, games, players, draft_order, draft_results, draft_order_rules]:
        if not df.empty:
            df.rename(columns={k: v for k, v in RENAME_MAP.items() if k in df.columns}, inplace=True)
            if 'season' in df.columns:
                df['season'] = pd.to_numeric(df['season'], errors='coerce').fillna(0).astype(int)
            if 'week' in df.columns:
                df['week'] = pd.to_numeric(df['week'], errors='coerce').fillna(0).astype(int)
            if 'playerId' in df.columns:
                df['playerId'] = pd.to_numeric(df['playerId'], errors='coerce').fillna(0).astype(int)
            if 'draftPick' in df.columns:
                df['draftPick'] = pd.to_numeric(df['draftPick'], errors='coerce').fillna(0).astype(int)
            if 'draftOrder' in df.columns:
                df['draftOrder'] = pd.to_numeric(df['draftOrder'], errors='coerce').fillna(0).astype(int)

    if not players.empty and 'playerId' in players.columns:
        players = players.dropna(subset=['playerId'])
        players = players.sort_values('playerId').drop_duplicates(subset=['playerId'], keep='last').reset_index(drop=True)

    return DataBundle(standings, teams, games, players, draft_order, draft_results, draft_order_rules)


def load_data_season(year: int):
    """Returns data sliced to a single season year -- now a thin wrapper,
    since load_data(year=X) does exactly this via the historical/active
    bucket split above."""
    return load_data(year=year)


def get_active_season(games: pd.DataFrame, draft_results: pd.DataFrame = None,
                       rules: pd.DataFrame = None) -> int:
    """
    Returns the latest season that has completed game results.
    If draft_results is provided, only considers seasons that also have draft picks.
    This prevents future/post-season data (e.g. 2025 playoffs) from overriding a
    season where draft data hasn't been loaded yet.

    If rules (draft_order_rules) is also provided, the result can advance past
    the games-results season to a later one whose draft is fully complete --
    the week between draft day and kickoff shouldn't leave every page pointed
    at last season.
    """
    if games.empty or 'season' not in games.columns:
        active = 2024
    elif 'result' not in games.columns:
        active = int(games['season'].max())
    else:
        has_results = games[games['result'].notna() & (games['result'] != UNDRAFTED_SENTINEL)]
        active = int(has_results['season'].max()) if not has_results.empty else 2024

    # If draft_results provided, cap to the latest season that has draft picks
    if draft_results is not None and not draft_results.empty and 'season' in draft_results.columns:
        draft_seasons = set(draft_results['season'].dropna().astype(int).unique())
        # Walk back from active until we find a year with draft picks
        while active > 2013 and active not in draft_seasons:
            active -= 1

    # Advance forward to the latest season whose draft is fully complete, even
    # with zero games played yet.
    if (draft_results is not None and not draft_results.empty and rules is not None
            and not rules.empty and 'season' in draft_results.columns and 'season' in rules.columns):
        candidate_seasons = sorted(
            s for s in draft_results['season'].dropna().astype(int).unique() if s > active
        )
        for season in candidate_seasons:
            picks_made = len(draft_results[draft_results['season'] == season])
            picks_expected = len(rules[rules['season'] == season]) * TEAMS_PER_PLAYER
            if picks_expected > 0 and picks_made >= picks_expected:
                active = season

    return active

def get_available_years(draft_results: pd.DataFrame, games: pd.DataFrame = None,
                         rules: pd.DataFrame = None) -> list:
    """Returns seasons with draft data, capped at the active season.
    Use this for standings/schedule/race pages.

    Passing rules lets a season with a fully-completed draft (but zero games
    played yet) appear in the dropdown without relying on a redirect having
    already force-added it as the currently-viewed year.
    """
    if draft_results.empty or 'season' not in draft_results.columns:
        return [2024]
    years = sorted(draft_results['season'].dropna().astype(int).unique().tolist())
    if games is not None and not games.empty:
        active = get_active_season(games, draft_results, rules)
        years = [y for y in years if y <= active]
    return years

def get_draft_years(draft_results: pd.DataFrame) -> list:
    """Returns ALL seasons with draft data, including future seasons (e.g. 2025 pre-draft).
    Use this for draft-specific pages (draft results, draft history)."""
    if draft_results.empty or 'season' not in draft_results.columns:
        return [2024]
    return sorted(draft_results['season'].dropna().astype(int).unique().tolist())

def get_latest_week_for_year(games: pd.DataFrame, year: int) -> int:
    """
    Returns the 'current' week for the schedule view:
    - Finds the highest week where at least one game is complete but NOT all games are done
      (i.e., the week is still in progress / just finished but within the season).
    - Falls back to the highest week with any completed game if all weeks are fully done.
    - Returns 1 if no completed games exist.
    """
    if games.empty or 'week' not in games.columns:
        return 1
    reg_games = games[(games['season'] == year) & (games.get('game_type', 'REG') == 'REG')] if 'game_type' in games.columns else games[games['season'] == year]
    if reg_games.empty:
        return 1

    # Group by week: count total games and completed games
    def completed(r):
        return r.notna() & (r != UNDRAFTED_SENTINEL)

    week_stats = (
        reg_games.groupby('week')
        .apply(lambda g: pd.Series({
            'total': len(g),
            'done': completed(g['result']).sum()
        }), include_groups=False)
        .reset_index()
    )
    week_stats = week_stats[week_stats['done'] > 0]  # Only weeks with at least one completed game
    if week_stats.empty:
        return 1

    # Find highest week where done < total (in-progress / live week)
    in_progress = week_stats[week_stats['done'] < week_stats['total']]
    if not in_progress.empty:
        return int(in_progress['week'].max())

    # All weeks are fully complete — return highest completed week
    return int(week_stats['week'].max())

def get_latest_season_and_week(games: pd.DataFrame) -> Tuple[int, int]:
    """Determines the latest regular season week available in the data."""
    if games.empty:
        return 2024, 1
        
    if 'game_type' in games.columns:
        reg_games = games[games['game_type'] == 'REG']
        if reg_games.empty: # Fallback
            reg_games = games
    else:
        reg_games = games
        
    latest_season = reg_games['season'].max()
    latest_week = reg_games[reg_games['season'] == latest_season]['week'].max()
    
    # Handle NaN cases
    if pd.isna(latest_season):
        latest_season = 2024
    if pd.isna(latest_week):
        latest_week = 1
        
    return int(latest_season), int(latest_week)

def _predictions_domain_for(season: int) -> str:
    import services.cache_service as cs
    active = _get_active_bucket()
    return cs.DOMAIN_PREDICTIONS_ACTIVE if season == active["season"] else cs.DOMAIN_PREDICTIONS_HISTORICAL


def _get_predictions_bucket_entry(season: int) -> dict:
    """Returns the per-season dict for `season`'s predictions, cached within
    whichever domain (active/historical) that season falls into today -- see
    docs/superpowers/specs/2026-09-14-cache-mutability-redesign-design.md SS3.

    The entry is populated lazily, one key at a time (preseason_df /
    consensus_df / cache_service.get_game_predictions()'s own
    game_predictions key) -- calling get_preseason_predictions() alone must
    not also fetch consensus_projections, and vice versa."""
    import services.cache_service as cs
    domain = _predictions_domain_for(season)
    bucket = cs.get_domain(domain)
    if bucket is None:
        # Only stamp the domain's timestamp on first creation -- once it
        # exists, later calls mutate this same dict object in place (via the
        # reference cs.get_domain() returns), so lazily adding a key for a
        # different season/collection must never re-bump the timestamp; that
        # would make check_remote_signals() never see a remote signal as
        # newer, since "newer than right now" is never true.
        bucket = {}
        cs.set_domain(domain, bucket)
    return bucket.setdefault(season, {})


def get_preseason_predictions(season: int) -> Dict[str, dict]:
    """Retrieves Win Totals (including avg, std_dev, and sources) from the database."""
    entry = _get_predictions_bucket_entry(season)
    if "preseason_df" not in entry:
        entry["preseason_df"] = get_collection_df("preseason_predictions", filters=[("season", "==", season)])
    preds_df = entry["preseason_df"]
    if preds_df.empty:
        return {}
    
    # Return a map of team -> {projected_wins, std_dev, sources}
    res = {}
    for _, row in preds_df.iterrows():
        # row.get("mean_wins", ...) only falls back when the key is absent, not
        # when the column exists but is NaN for this row (e.g. a season mixed
        # into a DataFrame with other seasons that do populate mean_wins) --
        # so the NaN case must be checked explicitly with pd.notna.
        mean_wins = row.get("mean_wins")
        res[row["team"]] = {
            "projected_wins": float(row.get("projected_wins", 0)),
            "mean_wins": float(mean_wins) if pd.notna(mean_wins) else float(row.get("projected_wins", 0)),
            "std_dev": float(row.get("std_dev", 0)),
            "sources": row.get("sources", {})
        }
    return res

def get_draft_snapshot_predictions(season: int) -> Dict[str, dict]:
    """Frozen, pre-draft snapshot of model win projections for `season`.

    Same shape and caching pattern as get_preseason_predictions(), but reads
    draft_snapshot_predictions instead -- a number that stops moving once a
    real draft (or a mock draft bot) has used it. See
    docs/superpowers/specs/2026-09-15-preseason-draft-snapshot-design.md.
    """
    entry = _get_predictions_bucket_entry(season)
    if "snapshot_df" not in entry:
        entry["snapshot_df"] = get_collection_df("draft_snapshot_predictions", filters=[("season", "==", season)])
    preds_df = entry["snapshot_df"]
    if preds_df.empty:
        return {}

    res = {}
    for _, row in preds_df.iterrows():
        mean_wins = row.get("mean_wins")
        res[row["team"]] = {
            "projected_wins": float(row.get("projected_wins", 0)),
            "mean_wins": float(mean_wins) if pd.notna(mean_wins) else float(row.get("projected_wins", 0)),
            "std_dev": float(row.get("std_dev", 0)),
            "sources": row.get("sources", {})
        }
    return res

def get_consensus_projections(season: int) -> Dict[str, dict]:
    """Retrieve analyst consensus projections for a season, keyed by team."""
    entry = _get_predictions_bucket_entry(season)
    if "consensus_df" not in entry:
        entry["consensus_df"] = get_collection_df("consensus_projections", filters=[("season", "==", season)])
    df = entry["consensus_df"]
    if df.empty:
        return {}

    res = {}
    for _, row in df.iterrows():
        res[row["team"]] = {
            "sources":          row.get("sources", {}),
            "n_sources":        int(row.get("n_sources", 0) or 0),
            "consensus_mean":   row.get("consensus_mean"),
            "consensus_median": row.get("consensus_median"),
            "consensus_min":    row.get("consensus_min"),
            "consensus_max":    row.get("consensus_max"),
            "consensus_std":    row.get("consensus_std"),
        }
    return res

def get_season_projection(season: int, frozen: bool = False) -> Dict[str, dict]:
    """Resolve the best available win projection for a season, per team.

    Model output wins when it exists; analyst consensus is the fallback. This
    lets preseason_predictions mean "model output" and consensus_projections
    mean "analyst consensus" without historical views losing their numbers.

    frozen=True reads the model side from draft_snapshot_predictions instead
    of preseason_predictions -- see get_draft_snapshot_predictions() and
    docs/superpowers/specs/2026-09-15-preseason-draft-snapshot-design.md.
    Consensus is never frozen; only readers that must show a stable, pre-draft
    number pass frozen=True.

    Returns {team: {"wins": float, "source_type": "model"|"consensus", "detail": dict}}
    """
    model = get_draft_snapshot_predictions(season) if frozen else get_preseason_predictions(season)
    consensus = get_consensus_projections(season)

    out = {}
    for team in set(model) | set(consensus):
        if team in model:
            row = model[team]
            wins = row.get("mean_wins")
            if wins is None:
                wins = row.get("projected_wins")
            out[team] = {
                "wins": float(wins) if wins is not None else None,
                "source_type": "model",
                "detail": row,
            }
        else:
            row = consensus[team]
            out[team] = {
                "wins": row.get("consensus_median"),
                "source_type": "consensus",
                "detail": row,
            }
    return out

def get_season_projection_legacy_shape(season: int, frozen: bool = False) -> Dict[str, dict]:
    """get_season_projection() flattened to the shape the UI has always read.

    Callers that render projections -- the draft room payload (consumed verbatim
    by static/js/*.js), the player profile, the draft recap -- read
    `.projected_wins` / `.std_dev` off a flat per-team dict. This adapts the
    resolver's {"wins", "source_type", "detail"} back to that shape so those
    callers keep working across both collections without each one re-deriving it.

    `projected_wins` prefers the stored value and falls back to `wins`: model
    rows carry their own rounded projected_wins (which is NOT mean_wins), while
    consensus detail has no such key and must use the resolved median. The two
    source types put their central value and spread under different key names,
    so each is tried in turn -- a model detail never holds consensus_* keys, and
    vice versa, which is what lets one lookup chain serve both.
    """
    out = {}
    for team, proj in get_season_projection(int(season), frozen=frozen).items():
        detail = proj.get("detail") or {}
        wins = proj.get("wins")
        raw_std = detail.get("std_dev", detail.get("consensus_std", 0))
        out[team] = {
            "projected_wins": detail.get("projected_wins", wins),
            "mean_wins": detail.get("mean_wins", detail.get("consensus_mean", wins)),
            "std_dev": round(raw_std, 2) if raw_std is not None else 0,
            "sources": detail.get("sources", {}),
        }
    return out

def get_season_projection_dual(season: int, frozen: bool = False) -> Dict[str, dict]:
    """Per-team model AND consensus projections, both exposed separately --
    unlike get_season_projection_legacy_shape(), which collapses to whichever
    one source_type wins and discards the other. Admin-only data: callers
    must gate this the same way they gate the legacy shape.

    Returns {team: {"model": {"projected_wins", "std_dev"} | None,
                     "consensus": {"consensus_mean", "consensus_median", "consensus_std"} | None}}
    """
    model = get_draft_snapshot_predictions(season) if frozen else get_preseason_predictions(season)
    consensus = get_consensus_projections(season)

    out = {}
    for team in set(model) | set(consensus):
        m = model.get(team)
        c = consensus.get(team)
        out[team] = {
            "model": {"projected_wins": m["projected_wins"], "std_dev": m["std_dev"]} if m else None,
            "consensus": {
                "consensus_mean": c["consensus_mean"],
                "consensus_median": c["consensus_median"],
                "consensus_std": c["consensus_std"],
            } if c else None,
        }
    return out

def get_season_projection_blended(season: int, frozen: bool = False) -> Dict[str, dict]:
    """Per-team inverse-variance blend of model and analyst-consensus wins.

    Unlike get_season_projection_legacy_shape() (model wins outright when it
    exists, consensus is only a fallback), this actually combines both when
    both exist: model.mean_wins and consensus.consensus_mean are weighted by
    1/std_dev^2 each, so whichever source is more confident (tighter std_dev)
    pulls the blend toward it. A team with only one source, or a source whose
    std_dev is missing/non-positive (can't weight a zero-width distribution),
    falls back to that one source outright.

    Used by the draft room's running portfolio and the post-draft recap's
    value calculus -- both want "our best combined read," not a hard model
    vs. consensus resolver. Returns the same flat shape as
    get_season_projection_legacy_shape(), and a team with only one source
    available reports that source's own established values unchanged (a
    model-only team keeps its own rounded projected_wins, not mean_wins) --
    only a team with both sources gets an actual blended figure.
    """
    model = get_draft_snapshot_predictions(season) if frozen else get_preseason_predictions(season)
    consensus = get_consensus_projections(season)

    out = {}
    for team in set(model) | set(consensus):
        m = model.get(team)
        c = consensus.get(team)
        m_std = m["std_dev"] if m else None
        c_std = c["consensus_std"] if c else None

        if m and c and m_std and c_std:
            w_m, w_c = 1 / (m_std ** 2), 1 / (c_std ** 2)
            blended = (m["mean_wins"] * w_m + c["consensus_mean"] * w_c) / (w_m + w_c)
            out[team] = {
                "projected_wins": round(blended),
                "mean_wins": round(blended, 2),
                "std_dev": round((1 / (w_m + w_c)) ** 0.5, 2),
                "sources": c["sources"],
            }
        elif m:
            out[team] = {"projected_wins": m["projected_wins"], "mean_wins": m["mean_wins"],
                         "std_dev": round(m["std_dev"], 2) if m["std_dev"] is not None else 0,
                         "sources": m["sources"]}
        elif c:
            out[team] = {"projected_wins": c["consensus_median"], "mean_wins": c["consensus_mean"],
                         "std_dev": round(c["consensus_std"], 2) if c["consensus_std"] is not None else 0,
                         "sources": c["sources"]}
    return out

def get_team_schedule(team: str, games_df: pd.DataFrame, season: int) -> List[str]:
    """Extracts a team's sequential 17-game schedule from the NFL Games dataframe."""
    schedule = []
    if games_df.empty or "season" not in games_df.columns:
        return schedule
        
    season_games = games_df[(games_df["season"] == season)]
    team_games = season_games[(season_games["home_team"] == team) | (season_games["away_team"] == team)].copy()

    if "week" in team_games.columns:
        team_games = team_games.sort_values(by="week")
        
    for _, row in team_games.iterrows():
        opp = row["away_team"] if row["home_team"] == team else row["home_team"]
        home_away = "vs" if row["home_team"] == team else "@"
        schedule.append(f"Wk{row.get('week', '?')} {home_away} {opp}")
        
    return schedule

if __name__ == "__main__":
    import json
    from services.analysis_service import get_season_progress
    st, tm, gm, pl, do, dr, drr = load_data()
    s, w = get_latest_season_and_week(gm)
    print(f"Latest: Season {s} Week {w}")
    res = get_season_progress(s, w)
    print(json.dumps(res)[:500])
