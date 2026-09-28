import concurrent.futures
import logging
import math
import re
import requests
import pandas as pd
from services.constants import UNDRAFTED_SENTINEL
from services.data_service import load_data, get_season_projection_blended
from services.analysis_service import get_enriched_schedule, compute_team_records, format_team_record
from services.ai_service import generate_weekly_summary, get_recap_prompt
from services.db_service import save_weekly_recap
from services.cache_service import get_quarter_scores_season
from services.utils import normalize_team_abbr

logger = logging.getLogger(__name__)


def _clean_text(text: str | None) -> str | None:
    """Normalize curly quotes, unicode hyphens, and strip replacement characters."""
    if not text:
        return None
    return (
        text
        .replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\ufffd", "")
        .strip()
    )


def fetch_weekly_espn_data(year: int, week: int) -> dict[tuple[str, str], dict]:
    """Fetch stat leaders, storylines, team statistics, and decisive plays for all games in a week from ESPN.

    First calls ESPN scoreboard API to get the matchups, headlines, and event IDs.
    Then concurrently fetches game summary endpoints for detailed team-by-team offensive leaders,
    boxscore stats (turnovers, total yards, red zone efficiency), and decisive scoring plays.

    Returns mapping of (home_team, away_team) -> {
        "headline": str | None,
        "leaders": [str],  # Scoreboard fallback combined leaders e.g. ["Josh Allen (BUF): 240 YDS, 2 TD"]
        "leaders_by_team": dict[str, list[str]],  # e.g. {"BUF": ["Josh Allen: 18/23, 232 YDS, 2 TD", ...], "MIA": [...]}
        "team_stats": dict[str, dict[str, str | int]],  # e.g. {"BUF": {"turnovers": 0, "totalYards": "352", "redZoneAttempts": "3-4"}}
        "decisive_play": str | None,  # e.g. "Tyler Bass 36 Yd Field Goal (OT 8:12)"
    }
    Team abbreviations are normalized to match nflverse canonical codes.
    Fails open (returns {} or degrades to scoreboard data) on any network error, timeout, or missing data.
    """
    url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={year}&seasontype=2&week={week}"
    try:
        resp = requests.get(url, timeout=10)
        if not resp.ok:
            return {}
        data = resp.json()
    except Exception as e:
        logger.warning("Failed to fetch ESPN weekly data for %s week %s: %s", year, week, e)
        return {}

    events = data.get("events", [])
    if not events:
        return {}

    # Concurrently fetch summary data for each event ID
    event_ids = [str(e["id"]) for e in events if e.get("id")]
    summaries: dict[str, dict] = {}

    def _fetch_summary(eid: str) -> tuple[str, dict | None]:
        summary_url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={eid}"
        try:
            r = requests.get(summary_url, timeout=5)
            if r.ok:
                return eid, r.json()
        except Exception as err:
            logger.debug("Failed to fetch ESPN summary for event %s: %s", eid, err)
        return eid, None

    if event_ids:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(event_ids), 8)) as executor:
            for eid, s_json in executor.map(_fetch_summary, event_ids):
                if s_json:
                    summaries[eid] = s_json

    out = {}
    for event in events:
        competitions = event.get("competitions", [{}])[0]
        competitors = competitions.get("competitors", [])

        home_abbr, away_abbr = None, None
        team_id_to_abbr = {}
        for c in competitors:
            raw_abbr = c.get("team", {}).get("abbreviation")
            if not raw_abbr:
                continue
            normalized = normalize_team_abbr(raw_abbr)
            if c.get("id"):
                team_id_to_abbr[str(c["id"])] = normalized
            if c.get("team", {}).get("id"):
                team_id_to_abbr[str(c["team"]["id"])] = normalized
            if c.get("homeAway") == "home":
                home_abbr = normalized
            elif c.get("homeAway") == "away":
                away_abbr = normalized

        if not home_abbr or not away_abbr:
            continue

        scoreboard_leaders = []
        for l in competitions.get("leaders", []):
            for athlete in l.get("leaders", []):
                name = athlete.get("athlete", {}).get("displayName")
                stat = athlete.get("displayValue")
                if name and stat:
                    team_id = str(athlete.get("team", {}).get("id") or athlete.get("athlete", {}).get("team", {}).get("id") or "")
                    team_abbr = team_id_to_abbr.get(team_id)
                    team_tag = f" ({team_abbr})" if team_abbr else ""
                    scoreboard_leaders.append(f"{name}{team_tag}: {stat}")

        headlines = competitions.get("headlines", [])
        headline_desc = headlines[0].get("description") if headlines else None
        if headline_desc:
            headline_desc = _clean_text(headline_desc)
            if headline_desc:
                headline_desc = headline_desc.lstrip("- ").strip()

        eid = str(event.get("id") or "")
        summary = summaries.get(eid)

        leaders_by_team: dict[str, list[str]] = {}
        team_stats: dict[str, dict[str, str | int]] = {}
        decisive_play: str | None = None

        if summary:
            # 1. Team-by-team stat leaders (passing, rushing, receiving)
            for tl in summary.get("leaders", []):
                t_raw = tl.get("team", {}).get("abbreviation")
                if not t_raw:
                    continue
                t_norm = normalize_team_abbr(t_raw)
                t_lead_list = []
                for cat in tl.get("leaders", []):
                    cat_name = cat.get("name")
                    if cat_name in ("passingYards", "rushingYards", "receivingYards"):
                        l_list = cat.get("leaders", [])
                        if l_list:
                            ath = l_list[0].get("athlete", {}).get("displayName")
                            val = l_list[0].get("displayValue")
                            if ath and val:
                                t_lead_list.append(f"{ath}: {val}")
                if t_lead_list:
                    leaders_by_team[t_norm] = t_lead_list

            # 2. Team statistics from boxscore (turnovers, total yards, red zone)
            for t_box in summary.get("boxscore", {}).get("teams", []):
                t_raw = t_box.get("team", {}).get("abbreviation")
                if not t_raw:
                    continue
                t_norm = normalize_team_abbr(t_raw)
                stat_dict = {}
                for s in t_box.get("statistics", []):
                    s_name = s.get("name")
                    val = s.get("displayValue")
                    if s_name in ("turnovers", "totalYards", "redZoneAttempts") and val is not None:
                        stat_dict[s_name] = val
                if stat_dict:
                    team_stats[t_norm] = stat_dict

            # 3. Decisive play from scoringPlays
            scoring_plays = summary.get("scoringPlays", [])
            if scoring_plays:
                last_play = scoring_plays[-1]
                p_text = _clean_text(last_play.get("text"))
                p_num = last_play.get("period", {}).get("number", 4)
                clock = last_play.get("clock", {}).get("displayValue", "")
                p_str = "OT" if p_num >= 5 else f"Q{p_num}"
                if p_text:
                    clock_part = f" ({p_str} {clock})" if clock else f" ({p_str})"
                    decisive_play = f"{p_text}{clock_part}"

        out[(home_abbr, away_abbr)] = {
            "headline": headline_desc or None,
            "leaders": scoreboard_leaders,
            "leaders_by_team": leaders_by_team,
            "team_stats": team_stats,
            "decisive_play": decisive_play,
        }

    return out


# Checkpoint labels for the comeback-win narrative line, keyed by which
# cumulative quarter checkpoint produced the winner's largest deficit.
_COMEBACK_CHECKPOINT_LABELS = {
    1: "after the 1st quarter",
    2: "at halftime",
    3: "entering the 4th quarter",
}


def _detect_comeback_win(qrow: dict, winner_is_home: bool) -> str | None:
    """Return a comeback-win narrative line if the winner overcame a real
    deficit, else None.

    A win counts as a comeback if the winner trailed entering the 4th quarter
    (cumulative Q1+Q2+Q3), OR trailed by 14+ points at any single checkpoint
    (after Q1, Q2, or Q3) -- computed from `qrow`'s cumulative home/away
    quarter scores. See docs/superpowers/specs/
    2026-09-15-comeback-win-recap-design.md, Design §5.
    """
    w_prefix, l_prefix = ("home", "away") if winner_is_home else ("away", "home")

    w_cum = 0
    l_cum = 0
    deficits: dict[int, int] = {}
    for checkpoint, q_key in ((1, "q1"), (2, "q2"), (3, "q3")):
        w_cum += qrow.get(f"{w_prefix}_{q_key}") or 0
        l_cum += qrow.get(f"{l_prefix}_{q_key}") or 0
        if l_cum > w_cum:
            deficits[checkpoint] = l_cum - w_cum

    if not deficits:
        return None

    trailing_entering_q4 = 3 in deficits
    worst_checkpoint = max(deficits, key=deficits.get)
    worst_deficit = deficits[worst_checkpoint]

    if not (trailing_entering_q4 or worst_deficit >= 14):
        return None

    return (f"Down {worst_deficit} {_COMEBACK_CHECKPOINT_LABELS[worst_checkpoint]} "
            f"and still found a way to win.")


def _check_close_margin(margin: int, winner_is_home: bool) -> str | None:
    if margin > 3:
        return None
    return f"Survived a close one by {margin}" if winner_is_home else f"Stole a win by {margin}"


def _check_blowout(margin: int, winner_is_home: bool) -> str | None:
    if margin < 17:
        return None
    return f"Absolute blowout! Won by {margin}" if winner_is_home else f"Dominant performance! Won by {margin}"


def _check_ugly_win(winner_score: int, winner_is_home: bool) -> str | None:
    if winner_score >= 14:
        return None
    return (f"Ugly win but counts! Scored only {winner_score} and escaped" if winner_is_home
            else f"Scrappy win! Scored only {winner_score} and still won")


def _collect_win_notes(qrow: dict | None, margin: int, winner_score: int, winner_is_home: bool) -> list[str]:
    """Run every notable-win check independently -- not an if/elif priority
    chain -- and return every descriptor that matched. The caller merges
    these into one combined notable-win line per game, keyed by
    (player, team, week, year), instead of one bullet per check."""
    checks = [
        _detect_comeback_win(qrow, winner_is_home=winner_is_home) if qrow is not None else None,
        _check_close_margin(margin, winner_is_home),
        _check_blowout(margin, winner_is_home),
        _check_ugly_win(winner_score, winner_is_home),
    ]
    return [note for note in checks if note]


def _format_roster_entry(team: str, draft_pick, total_players: int, team_records: dict) -> str:
    """Format one roster line for the weekly recap prompt: team, the draft
    pick it was taken with (so the AI can comment on reaches/steals), and
    the team's real overall record (so it can note a team carrying a
    roster). `draft_pick` is None/NaN when draft_results has no matching row."""
    record = format_team_record(team, team_records)
    if draft_pick is None or pd.isna(draft_pick) or not total_players:
        return f"{team} ({record})"
    draft_pick = int(draft_pick)
    draft_round = math.ceil(draft_pick / total_players)
    return f"{team} (Pick #{draft_pick}, Rd {draft_round}, {record})"


def extract_weekly_data(year, week):
    """
    Fetches results for the given week and identifies winners/bad beats, 
    while also including overall season-to-date records.
    
    Beat Definitions:
      Bad Beat: Lost by <= 3 points OR scored >= 30 and still lost.
      Good Beat: Won by >= 17 points OR scored < 14 and still won.
    """
    standings, _, games, players, _, draft_results, _ = load_data(year=year)
    schedule = get_enriched_schedule(games, draft_results, players, year)
    
    if schedule.empty:
        return None, []

    # 1. Calculate Overall Season Wins (up to and including this week)
    season_to_date = schedule[schedule['week'] <= week]
    played_to_date = season_to_date[(season_to_date['result'] != UNDRAFTED_SENTINEL) & (season_to_date['result'].notna())]
    
    overall_wins = {}
    for _, row in played_to_date.iterrows():
        win_pid = row['playerId_home_draft'] if row['result'] > 0 else row['playerId']
        if pd.notna(win_pid) and win_pid != UNDRAFTED_SENTINEL:
            overall_wins[win_pid] = overall_wins.get(win_pid, 0) + 1

    # 2. Extract specific week stats
    weekly_games = schedule[schedule['week'] == week]
    if weekly_games.empty:
        return None, []

    player_stats = {}
    pid_to_name = dict(zip(players['playerId'], players['fullName']))

    # Each player's drafted teams, the draft pick each was taken with, and
    # those teams' real overall W-L records (not the fantasy-pool win count
    # above) -- gives the AI roster context to comment on beyond just this
    # week's result: reaching for a team, a great late-round pick, or an NFL
    # team quietly carrying a player's whole roster.
    team_records = compute_team_records(games, year)
    player_rosters: dict = {}
    # Pool size must come from the draft itself (3 teams per player), not
    # len(players) -- that's every registered account, which can outnumber
    # this season's actual drafters (e.g. seeded e2e test accounts) and
    # silently shift every pick into the wrong round. Same fix already
    # applied in static/js/main.js and ui_renderer.js's round labels.
    total_players = 10
    if not draft_results.empty and 'season' in draft_results.columns:
        season_draft_results = draft_results[draft_results['season'] == year]
        if 'draftPick' in season_draft_results.columns:
            season_draft_results = season_draft_results.sort_values('draftPick')
        if len(season_draft_results):
            total_players = len(season_draft_results) / 3
        for _, row in season_draft_results.iterrows():
            pick = row.get('draftPick')
            player_rosters.setdefault(row['playerId'], []).append((row['team'], pick))

    # Quarter-by-quarter scores for comeback-win detection, indexed for O(1)
    # per-game lookup. Silent no-op when the scrape hasn't run yet for this
    # week (or predates the pipeline) -- comeback callouts are flavor, not
    # core recap content.
    quarter_scores_by_game = {
        (r["week"], r["home_team"], r["away_team"]): r
        for r in get_quarter_scores_season(year)
    }

    # Notable-win checks run independently per game (see _collect_win_notes)
    # and get merged into one combined line per game here, keyed by
    # (player, team, week, year) so a low-scoring close win with a comeback
    # doesn't produce three separate bullets for the same result.
    notable_win_notes: dict[tuple, list[str]] = {}
    notable_win_suffix: dict[tuple, str] = {}

    for _, row in weekly_games.iterrows():
        if row['result'] == UNDRAFTED_SENTINEL or row['result'] is None:
            continue

        a_pid = row['playerId']
        h_pid = row['playerId_home_draft']

        # A team can play an undrafted opponent (this pool doesn't draft all
        # 32 teams). Only skip crediting the undrafted side -- skipping the
        # whole row would also drop the drafted side's result for that game,
        # undercounting their weekly win/loss record.
        a_drafted = pd.notna(a_pid) and a_pid != UNDRAFTED_SENTINEL
        h_drafted = pd.notna(h_pid) and h_pid != UNDRAFTED_SENTINEL
        if not a_drafted and not h_drafted:
            continue

        if h_drafted and h_pid not in player_stats:
            player_stats[h_pid] = {'wins': 0, 'losses': 0, 'bad_beats': [], 'notable_wins': []}
        if a_drafted and a_pid not in player_stats:
            player_stats[a_pid] = {'wins': 0, 'losses': 0, 'bad_beats': [], 'notable_wins': []}

        home_score = row['home_score']
        away_score = row['away_score']
        margin = abs(home_score - away_score)
        qrow = quarter_scores_by_game.get((row['week'], row['home_team'], row['away_team']))

        if row['result'] > 0: # Home Win
            if h_drafted:
                player_stats[h_pid]['wins'] += 1
            if a_drafted:
                player_stats[a_pid]['losses'] += 1
            # Called out regardless of margin: losing outright to a team
            # nobody drafted is embarrassing on its own, independent of score.
            if a_drafted and not h_drafted:
                player_stats[a_pid]['bad_beats'].append(f"Lost outright to a team nobody even drafted ({row['away_team']} {away_score}-{home_score} {row['home_team']})")
            # Bad Beats for Away (loser side; kept fully independent of the
            # winner's notable-win note below so the two sides' categorizations
            # never interact)
            if a_drafted:
                if margin <= 3:
                    player_stats[a_pid]['bad_beats'].append(f"Lost a nail-biter by {margin} ({row['away_team']} {away_score}-{home_score} {row['home_team']})")
                elif away_score >= 30:
                    player_stats[a_pid]['bad_beats'].append(f"Scored {away_score} and still lost ({row['away_team']} {away_score}-{home_score} {row['home_team']})")

            # Notable Win for Home -- collect every matching check, merged
            # into one combined line after the loop (see notable_win_notes).
            if h_drafted:
                notes = _collect_win_notes(qrow, margin, home_score, winner_is_home=True)
                if notes:
                    key = (h_pid, row['home_team'], week, year)
                    notable_win_notes.setdefault(key, []).extend(notes)
                    notable_win_suffix[key] = f"({row['home_team']} {home_score}-{away_score} {row['away_team']})"

        else: # Away Win
            if a_drafted:
                player_stats[a_pid]['wins'] += 1
            if h_drafted:
                player_stats[h_pid]['losses'] += 1
            # Called out regardless of margin: losing outright to a team
            # nobody drafted is embarrassing on its own, independent of score.
            if h_drafted and not a_drafted:
                player_stats[h_pid]['bad_beats'].append(f"Lost outright to a team nobody even drafted ({row['home_team']} {home_score}-{away_score} {row['away_team']})")
            # Bad Beats for Home (loser side)
            if h_drafted:
                if margin <= 3:
                    player_stats[h_pid]['bad_beats'].append(f"Heartbreaker! Lost by {margin} ({row['home_team']} {home_score}-{away_score} {row['away_team']})")
                elif home_score >= 30:
                    player_stats[h_pid]['bad_beats'].append(f"Scored {home_score} and still lost ({row['home_team']} {home_score}-{away_score} {row['away_team']})")

            # Notable Win for Away -- see comment above; same merge-at-the-end rule.
            if a_drafted:
                notes = _collect_win_notes(qrow, margin, away_score, winner_is_home=False)
                if notes:
                    key = (a_pid, row['away_team'], week, year)
                    notable_win_notes.setdefault(key, []).extend(notes)
                    notable_win_suffix[key] = f"({row['away_team']} {away_score}-{home_score} {row['home_team']})"

    # Merge every check that matched for a given game into ONE combined
    # notable-win line instead of one bullet per check.
    for key, notes in notable_win_notes.items():
        pid = key[0]
        combined = "; ".join(notes)
        player_stats[pid]['notable_wins'].append(f"{combined} {notable_win_suffix[key]}")

    # 3. Build text for Gemini
    included_pids = [pid for pid in pid_to_name if pid in player_stats or pid in overall_wins]

    data_summary = f"NFL WEEK {week} RESULTS ({year})\n"
    data_summary += "---------------------------------\n\n"

    # Ranked standings up front -- include movement context when week > 1
    prior_ranks = {}
    prior_wins = {}
    if week > 1:
        prior_to_date = schedule[
            (schedule['week'] < week) & 
            (schedule['result'] != UNDRAFTED_SENTINEL) & 
            (schedule['result'].notna())
        ]
        for _, row in prior_to_date.iterrows():
            win_pid = row['playerId_home_draft'] if row['result'] > 0 else row['playerId']
            if pd.notna(win_pid) and win_pid != UNDRAFTED_SENTINEL:
                prior_wins[win_pid] = prior_wins.get(win_pid, 0) + 1

        prior_sorted = sorted(
            included_pids,
            key=lambda pid: (-prior_wins.get(pid, 0), pid_to_name.get(pid, "")),
        )
        prior_ranks = {pid: r for r, pid in enumerate(prior_sorted, start=1)}

    if included_pids:
        standings_entries = sorted(
            ((pid, pid_to_name[pid], overall_wins.get(pid, 0)) for pid in included_pids),
            key=lambda entry: (-entry[2], entry[1]),
        )
        data_summary += f"SEASON STANDINGS (THROUGH WEEK {week}):\n"
        for rank, (pid, name, wins) in enumerate(standings_entries, start=1):
            movement_note = ""
            if week > 1 and pid in prior_ranks:
                p_rank = prior_ranks[pid]
                p_wins = prior_wins.get(pid, 0)
                wk_wins = wins - p_wins
                if rank < p_rank:
                    movement_note = f" (+{wk_wins} this week, climbed from #{p_rank})"
                elif rank > p_rank:
                    movement_note = f" (+{wk_wins} this week, slipped from #{p_rank})"
                else:
                    movement_note = f" (+{wk_wins} this week, held #{p_rank})"
            data_summary += f" {rank}. {name} - {wins} wins{movement_note}\n"
        data_summary += "\n"

    # Weekly Game Highlights & Key Context (scores, upsets, rivalry tags, stat leaders, storylines)
    # Focuses explicitly on Wins Pool players, head-to-head clashes, and team stats.
    game_highlights = []
    seen_games = set()
    espn_data = fetch_weekly_espn_data(year, week)
    for _, row in weekly_games.iterrows():
        if row['result'] == UNDRAFTED_SENTINEL or pd.isna(row['result']):
            continue
        game_key = (row['home_team'], row['away_team'])
        if game_key in seen_games:
            continue
        seen_games.add(game_key)

        h_team = row['home_team']
        a_team = row['away_team']
        h_score = int(row['home_score']) if pd.notna(row.get('home_score')) else 0
        a_score = int(row['away_score']) if pd.notna(row.get('away_score')) else 0
        result = row['result']
        margin = abs(h_score - a_score)

        a_pid = row['playerId']
        h_pid = row['playerId_home_draft']
        a_drafted = pd.notna(a_pid) and a_pid != UNDRAFTED_SENTINEL
        h_drafted = pd.notna(h_pid) and h_pid != UNDRAFTED_SENTINEL

        # Only include games where at least one team was drafted by a pool player
        if not a_drafted and not h_drafted:
            continue

        h_player = pid_to_name.get(h_pid) or row.get('fullName_home') or f"Player {h_pid}"
        a_player = pid_to_name.get(a_pid) or row.get('fullName_away') or f"Player {a_pid}"

        if result > 0:
            score_text = f"{h_team} {h_score}, {a_team} {a_score}"
            winner_team, loser_team = h_team, a_team
            winner_player, loser_player = h_player, a_player
            winner_drafted, loser_drafted = h_drafted, a_drafted
        elif result < 0:
            score_text = f"{a_team} {a_score}, {h_team} {h_score}"
            winner_team, loser_team = a_team, h_team
            winner_player, loser_player = a_player, h_player
            winner_drafted, loser_drafted = a_drafted, h_drafted
        else:
            score_text = f"{h_team} {h_score}, {a_team} {a_score} (Tie)"
            winner_team, loser_team = h_team, a_team
            winner_player, loser_player = h_player, a_player
            winner_drafted, loser_drafted = h_drafted, a_drafted

        tags = []
        if bool(row.get('div_game')):
            tags.append("Division Rivalry")
        if bool(row.get('overtime')):
            tags.append("Overtime")

        spread = row.get('spread_line')
        if pd.notna(spread) and spread != 0:
            favored = h_team if spread > 0 else a_team
            spread_mag = abs(spread)
            if winner_team and winner_team != favored:
                tags.append(f"UPSET ({favored} favored by {spread_mag})")
            else:
                tags.append(f"Spread: {favored} -{spread_mag}")

        # Check for bad beat against undrafted team
        if loser_drafted and not winner_drafted:
            tags.append("BAD BEAT - Lost to Undrafted Team")

        tag_str = f" [{', '.join(tags)}]" if tags else ""

        # Format matchup header focusing on Wins Pool players
        if h_drafted and a_drafted:
            if result != 0:
                header = f"* HEAD-TO-HEAD: {winner_player} ({winner_team}) def. {loser_player} ({loser_team}) -- {score_text}{tag_str}\n"
            else:
                header = f"* HEAD-TO-HEAD: {h_player} ({h_team}) tied {a_player} ({a_team}) -- {score_text}{tag_str}\n"
        elif winner_drafted and not loser_drafted:
            header = f"* MATCHUP: {winner_player} ({winner_team}) def. Undrafted ({loser_team}) -- {score_text}{tag_str}\n"
        elif loser_drafted and not winner_drafted:
            header = f"* MATCHUP: Undrafted ({winner_team}) def. {loser_player} ({loser_team}) -- {score_text}{tag_str}\n"
        else:
            header = f"* {score_text}{tag_str}\n"

        highlight_entry = header

        h_norm = normalize_team_abbr(h_team)
        a_norm = normalize_team_abbr(a_team)
        espn_game = espn_data.get((h_norm, a_norm)) or espn_data.get((h_team, a_team)) or {}

        headline = espn_game.get("headline")
        if headline:
            highlight_entry += f"  - Storyline: {headline}\n"

        # Key stats (turnovers, total yards, red zone)
        team_stats = espn_game.get("team_stats", {})
        if team_stats:
            stat_parts = []
            to_parts = []
            for t in (winner_team, loser_team):
                t_n = normalize_team_abbr(t)
                if t_n in team_stats and "turnovers" in team_stats[t_n]:
                    to_parts.append(f"{t} {team_stats[t_n]['turnovers']}")
            if to_parts:
                stat_parts.append(f"Turnovers: {', '.join(to_parts)}")

            yd_parts = []
            for t in (winner_team, loser_team):
                t_n = normalize_team_abbr(t)
                if t_n in team_stats and "totalYards" in team_stats[t_n]:
                    yd_parts.append(f"{t} {team_stats[t_n]['totalYards']}")
            if yd_parts:
                stat_parts.append(f"Total Yards: {', '.join(yd_parts)}")

            rz_parts = []
            for t in (winner_team, loser_team):
                t_n = normalize_team_abbr(t)
                if t_n in team_stats and "redZoneAttempts" in team_stats[t_n]:
                    rz_parts.append(f"{t} {team_stats[t_n]['redZoneAttempts']}")
            if rz_parts:
                stat_parts.append(f"Red Zone: {', '.join(rz_parts)}")

            if stat_parts:
                highlight_entry += f"  - Key Stats: {' | '.join(stat_parts)}\n"

        # Decisive play for close games (margin <= 8 or overtime)
        decisive_play = espn_game.get("decisive_play")
        if decisive_play and (margin <= 8 or bool(row.get('overtime'))):
            highlight_entry += f"  - Decisive Play: {decisive_play}\n"

        # Leaders: prefer team-by-team leaders; fallback to scoreboard combined leaders
        leaders_by_team = espn_game.get("leaders_by_team", {})
        if leaders_by_team:
            for t in (winner_team, loser_team):
                t_n = normalize_team_abbr(t)
                if t_n in leaders_by_team and leaders_by_team[t_n]:
                    highlight_entry += f"  - {t} Leaders: {', '.join(leaders_by_team[t_n])}\n"
        else:
            leaders = espn_game.get("leaders", [])
            if leaders:
                highlight_entry += f"  - Stat Leaders: {', '.join(leaders)}\n"

        game_highlights.append(highlight_entry)

    if game_highlights:
        data_summary += f"WEEK {week} GAME HIGHLIGHTS & KEY CONTEXT:\n"
        data_summary += "".join(game_highlights)
        data_summary += "\n"


    for pid in included_pids:
        name = pid_to_name[pid]
        stats = player_stats.get(pid, {'wins': 0, 'losses': 0, 'bad_beats': [], 'notable_wins': []})
        total_wins = overall_wins.get(pid, 0)

        data_summary += f"PLAYER: {name}\n"
        roster = player_rosters.get(pid)
        if roster:
            formatted_roster = ", ".join(
                _format_roster_entry(t, pick, total_players, team_records) for t, pick in roster
            )
            data_summary += f"TEAMS: {formatted_roster}\n"
        data_summary += f"WEEKLY RESULT: {stats['wins']}-{stats['losses']}\n"
        data_summary += f"CUMULATIVE SEASON WINS (UP TO WEEK {week}): {total_wins}\n"

        if stats['notable_wins']:
            data_summary += "NOTABLE WINS THIS WEEK (GOOD BEATS):\n"
            for win in stats['notable_wins']:
                data_summary += f" + {win}\n"
        
        if stats['bad_beats']:
            data_summary += "BAD BEATS THIS WEEK:\n"
            for beat in stats['bad_beats']:
                data_summary += f" - {beat}\n"
        data_summary += "\n"
        
    return data_summary, list(players['email'].dropna().unique())

def extract_draft_data(year):
    """
    Builds the context string for the preseason Draft AI recap.
    It includes drafted teams per player, consensus Win Totals (if available), 
    and general season context.
    """
    
    standings, teams, games, players, draft_order, draft_results, draft_order_rules = load_data(year=year)
    # Same resolver the live draft room's running portfolio uses
    # (services.draft_service.load_draft_state), so the recap's projections
    # match what players actually saw on the draft page. frozen=True: the
    # draft room itself reads the frozen pre-draft snapshot, not the live
    # model, so this must too or the two would silently diverge.
    preds = get_season_projection_blended(year, frozen=True)
    
    if draft_results.empty:
        return None, []
    
    # Filter draft results for the requested season
    draft_results = draft_results[draft_results['season'] == year]
    if draft_results.empty:
        return None, []

    pid_to_name = dict(zip(players['playerId'], players['fullName']))
    
    data_summary = f"NFL {year} PRESEASON DRAFT RECAP\n"
    data_summary += "---------------------------------\n\n"
    
    # Group drafted teams by Player
    player_rosters = {}
    for _, row in draft_results.iterrows():
        pid = row['playerId']
        team = row['team']
        if pid not in player_rosters:
            player_rosters[pid] = []
        player_rosters[pid].append(team)
        
    for pid, name in pid_to_name.items():
        if pid not in player_rosters:
            continue
            
        roster = player_rosters[pid]
        data_summary += f"PLAYER: {name}\n"
        
        # Build roster string with projections and calculate total
        formatted_roster = []
        proj_wins = 0.0
        for team in roster:
            # mean_wins, not projected_wins: projected_wins is rounded to a whole
            # number, but the running portfolio shown during the draft
            # (ui_renderer.js) sums mean_wins, so the recap must match that number.
            if team in preds and preds[team].get('mean_wins') is not None:
                val = float(preds[team]['mean_wins'])
                formatted_roster.append(f"{team} ({val})")
                proj_wins += val
            else:
                formatted_roster.append(team)

        data_summary += f"DRAFTED TEAMS: {', '.join(formatted_roster)}\n"
                
        if proj_wins > 0:
            data_summary += f"ROSTER PROJECTED WINS (Average): {proj_wins:.1f}\n"
            
        data_summary += "\n"
        
    return data_summary, list(players['email'].dropna().unique())
