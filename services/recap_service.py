import math
import pandas as pd
from services.constants import UNDRAFTED_SENTINEL
from services.data_service import load_data, get_season_projection_blended
from services.analysis_service import get_enriched_schedule, compute_team_records, format_team_record
from services.ai_service import generate_weekly_summary, get_recap_prompt
from services.db_service import save_weekly_recap
from services.cache_service import get_quarter_scores_season

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

            # Notable Win for Home -- a single if/elif chain so each game
            # produces exactly one narrative label instead of several
            # overlapping ones (e.g. a low-scoring close win previously hit
            # both the close-margin AND low-score checks). Comeback is the
            # most interesting story so it takes priority when present, and
            # always carries the team/score context like every other label.
            if h_drafted:
                game_suffix = f"({row['home_team']} {home_score}-{away_score} {row['away_team']})"
                comeback = _detect_comeback_win(qrow, winner_is_home=True) if qrow is not None else None
                if comeback:
                    player_stats[h_pid]['notable_wins'].append(f"{comeback} {game_suffix}")
                elif margin <= 3:
                    player_stats[h_pid]['notable_wins'].append(f"Survived a close one by {margin} {game_suffix}")
                elif margin >= 17:
                    player_stats[h_pid]['notable_wins'].append(f"Absolute blowout! Won by {margin} {game_suffix}")
                elif home_score < 14:
                    player_stats[h_pid]['notable_wins'].append(f"Ugly win but counts! Scored only {home_score} and escaped {game_suffix}")

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

            # Notable Win for Away -- see comment above; same single-label rule.
            if a_drafted:
                game_suffix = f"({row['away_team']} {away_score}-{home_score} {row['home_team']})"
                comeback = _detect_comeback_win(qrow, winner_is_home=False) if qrow is not None else None
                if comeback:
                    player_stats[a_pid]['notable_wins'].append(f"{comeback} {game_suffix}")
                elif margin <= 3:
                    player_stats[a_pid]['notable_wins'].append(f"Stole a win by {margin} {game_suffix}")
                elif margin >= 17:
                    player_stats[a_pid]['notable_wins'].append(f"Dominant performance! Won by {margin} {game_suffix}")
                elif away_score < 14:
                    player_stats[a_pid]['notable_wins'].append(f"Scrappy win! Scored only {away_score} and still won {game_suffix}")

    # 3. Build text for Gemini
    included_pids = [pid for pid in pid_to_name if pid in player_stats or pid in overall_wins]

    data_summary = f"NFL WEEK {week} RESULTS ({year})\n"
    data_summary += "---------------------------------\n\n"

    # Ranked standings up front -- the system prompt already asks Gemini to
    # always note overall standings, but this recap is sent standalone (no
    # separate standings page in context), so making the ranking explicit
    # here removes any need for it to infer rank order from the per-player
    # CUMULATIVE SEASON WINS lines below.
    if included_pids:
        standings_entries = sorted(
            ((pid_to_name[pid], overall_wins.get(pid, 0)) for pid in included_pids),
            key=lambda entry: (-entry[1], entry[0]),
        )
        data_summary += f"SEASON STANDINGS (THROUGH WEEK {week}):\n"
        for rank, (name, wins) in enumerate(standings_entries, start=1):
            data_summary += f" {rank}. {name} - {wins} wins\n"
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
    # match what players actually saw on the draft page.
    preds = get_season_projection_blended(year)
    
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
