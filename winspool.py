import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import os

# Custom integer breaks function
def integer_breaks(n=5):
    def breaks(x):
        return np.floor(np.linspace(min(x), max(x), n)).astype(int)
    return breaks
# Constants
NFL_DATA_GITHUB = "https://raw.githubusercontent.com/leesharpe/nfldata/master/data/"
LOCAL_SAVE_PATH = r"G:\Other computers\My Laptop (1)\Gambling\WinsPool"

# Function to load data
def load_data():
    nfl_data_github = "https://raw.githubusercontent.com/leesharpe/nfldata/master/data/"
    local_save = "G:\\Other computers\\My Laptop (1)\\Gambling\\WinsPool\\"

    standings = pd.read_csv(nfl_data_github + "standings.csv")
    teams = pd.read_csv(nfl_data_github + "teams.csv")
    games = pd.read_csv(nfl_data_github + "games.csv")
    players = pd.read_csv(local_save + "WinsPoolPlayers.csv")
    draft_order = pd.read_csv(local_save + "WinsPoolDraftOrder.csv")
    draft_results = pd.read_csv(local_save + "WinsPoolDraftResults.csv")
    draft_order_rules = pd.read_csv(local_save + "WinsPoolDraftOrderRules.csv")
    standings = pd.read_csv(f"{NFL_DATA_GITHUB}standings.csv")
    teams = pd.read_csv(f"{NFL_DATA_GITHUB}teams.csv")
    games = pd.read_csv(f"{NFL_DATA_GITHUB}games.csv")
    
    players = pd.read_csv(os.path.join(LOCAL_SAVE_PATH, "WinsPoolPlayers.csv"))
    draft_order = pd.read_csv(os.path.join(LOCAL_SAVE_PATH, "WinsPoolDraftOrder.csv"))
    draft_results = pd.read_csv(os.path.join(LOCAL_SAVE_PATH, "WinsPoolDraftResults.csv"))
    draft_order_rules = pd.read_csv(os.path.join(LOCAL_SAVE_PATH, "WinsPoolDraftOrderRules.csv"))
    
    return standings, teams, games, players, draft_order, draft_results, draft_order_rules

# Process games data
def process_games_data(games):
    games['winner'] = np.where(games['result'] > 0, games['home_team'], 
                               np.where(games['result'] < 0, games['away_team'], np.nan))
    conditions = [
        games['result'] > 0,
        games['result'] < 0
    ]
    choices = [games['home_team'], games['away_team']]
    games['winner'] = np.select(conditions, choices, default=np.nan)
    
    games['rec'] = 1
    games['TotalWinsBySeason'] = games.groupby(['season', 'winner', 'game_type'])['rec'].cumsum()
    games.drop('rec', axis=1, inplace=True)
    
    # Rename 'winner' to 'team' to match the downstream logic
    games.rename(columns={'winner': 'team'}, inplace=True)
    
    return games

# Filter data for a specific season
def filter_season_data(season_to_look, teams, standings, draft_results, games):
    today_teams = teams[teams['season'] == season_to_look]
    today_standings = standings[standings['season'] == season_to_look]
    today_draft_results = draft_results[draft_results['season'] == season_to_look]
    today_teams = teams[teams['season'] == season_to_look].copy()
    today_standings = standings[standings['season'] == season_to_look].copy()
    today_draft_results = draft_results[draft_results['season'] == season_to_look].copy()
    
    today_games = games[(games['season'] == season_to_look) & (games['week'] <= games['week'].max())]
    max_week = games[games['season'] == season_to_look]['week'].max()
    today_games = games[(games['season'] == season_to_look) & (games['week'] <= max_week)].copy()
    
    return today_teams, today_standings, today_draft_results, today_games

# Join games with players and calculate player wins by season
def add_player_data(today_games, today_draft_results, players):
    games_player_added = pd.merge(today_games, today_draft_results, on=['team', 'season'], how='inner')
    games_player_added['rec'] = 1
    games_player_added['TotalPlayerWinsBySeason'] = games_player_added.groupby('playerId')['rec'].cumsum()
    games_player_added = pd.merge(games_player_added, players, on='playerId', how='inner')
    return games_player_added

# Calculate wins by week per player
def calculate_wins_by_week(games_player_added_wins_total):
    wins_by_week_player = games_player_added_wins_total.groupby(['season', 'week', 'nickName'])['TotalPlayerWinsBySeason'].max().reset_index()
    wins_by_week_player = wins_by_week_player.pivot_table(index=['season', 'week'], columns='nickName', values='TotalPlayerWinsBySeason').fillna(method='ffill').fillna(0).reset_index()
    wins_by_week_player = wins_by_week_player.pivot_table(index=['season', 'week'], columns='nickName', values='TotalPlayerWinsBySeason').ffill().fillna(0).reset_index()
    return wins_by_week_player

# Calculate wins pool standings
def calculate_wins_pool_standings(today_standings, today_draft_results, players):
    wins_pool_standings = pd.merge(today_standings, today_draft_results, on=['team', 'season'])
    wins_pool_standings['ptDiff'] = wins_pool_standings['scored'] - wins_pool_standings['allowed']
    wins_pool_standings['my_ranks'] = wins_pool_standings.groupby(['season', 'playerId'])['wins'].rank(ascending=False)
    wins_pool_standings = pd.merge(wins_pool_standings, players, on='playerId', how='inner')
    return wins_pool_standings

# Plot wins by week for players
def plot_wins_by_week(wins_by_week_player, season_to_look, local_save):
# Plot wins by week for players and teams
def plot_wins_by_week(wins_by_week_player, today_games, season_to_look, save_path):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 12))

    # Player wins by week
    for nickName in wins_by_week_player.columns[2:]:
        ax1.plot(wins_by_week_player['week'], wins_by_week_player[nickName], label=nickName)
    for nickName in wins_by_week_player.columns:
        if nickName not in ['season', 'week']:
            ax1.plot(wins_by_week_player['week'], wins_by_week_player[nickName], label=nickName)

    ax1.set_xlabel('Week')
    ax1.set_ylabel('Total Wins')
    ax1.set_title(f'Player Total Wins by Week - {season_to_look}')
    ax1.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax1.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax1.legend(loc='best')
    ax1.legend(loc='upper left', bbox_to_anchor=(1, 1))
    ax1.grid(True)

    # Team wins by week
    return fig, ax2
    teams_with_wins = today_games.dropna(subset=['team'])
    for team in teams_with_wins['team'].unique():
        team_data = teams_with_wins[teams_with_wins['team'] == team].sort_values('week')
        ax2.plot(team_data['week'], team_data['TotalWinsBySeason'], label=team)

# Plot wins by week for teams
def plot_team_wins_by_week(ax, today_games, season_to_look):
    for team in today_games['team'].unique():
        team_data = today_games[today_games['team'] == team]
        ax.plot(team_data['week'], team_data['TotalWinsBySeason'], label=team)
    ax2.set_xlabel('Week')
    ax2.set_ylabel('Total Wins by Team')
    ax2.set_title(f'Team Total Wins by Week - {season_to_look}')
    ax2.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax2.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax2.legend(loc='upper left', bbox_to_anchor=(1, 1), ncol=2, fontsize='small')
    ax2.grid(True)
    
    plt.tight_layout()
    plt.savefig(os.path.join(save_path, f'winsByWeekPlayerAndTeam_{season_to_look}.png'))
    plt.show()

    ax.set_xlabel('Week')
    ax.set_ylabel('Total Wins by Team')
    ax.set_title(f'Team Total Wins by Week - {season_to_look}')
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend(loc='best')
    ax.grid(True)

# Main function to run the workflow
def main():
    standings, teams, games, players, draft_order, draft_results, draft_order_rules = load_data()
    games = process_games_data(games)
    
    season_to_look = 2024
    today_teams, today_standings, today_draft_results, today_games = filter_season_data(season_to_look, teams, standings, draft_results, games)
    
    games_player_added_wins_total = add_player_data(today_games, today_draft_results, players)
    wins_by_week_player = calculate_wins_by_week(games_player_added_wins_total)
    
    wins_pool_standings = calculate_wins_pool_standings(today_standings, today_draft_results, players)
    
    local_save = "G:\\Other computers\\My Laptop (1)\\Gambling\\WinsPool\\"
    
    # Plot both player and team wins by week in the same figure
    fig, ax2 = plot_wins_by_week(wins_by_week_player, season_to_look, local_save)
    plot_team_wins_by_week(ax2, today_games, season_to_look)
    
    plt.tight_layout()
    plt.savefig(os.path.join(local_save, f'winsByWeekPlayerAndTeam_{season_to_look}.png'))
    plt.show()
    plot_wins_by_week(wins_by_week_player, today_games, season_to_look, LOCAL_SAVE_PATH)

if __name__ == "__main__":
    main()
