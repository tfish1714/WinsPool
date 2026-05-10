import csv
import random
import re

def clean_team(name):
    # Mapping
    mapping = {
        "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
        "Carolina Panthers": "CAR", "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
        "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
        "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC",
        "Las Vegas Raiders": "LV", "L.A. Chargers": "LAC", "Los Angeles Chargers": "LAC", "L.A. Rams": "LA", 
        "Los Angeles Rams": "LA", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN", "New England Patriots": "NE", 
        "New Orleans Saints": "NO", "N.Y. Giants": "NYG", "New York Giants": "NYG", "N.Y. Jets": "NYJ", 
        "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF", 
        "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB", "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", 
        "Washington Commanders": "WAS", "L.A. Rams": "LA",
        "Arizona": "ARI", "Atlanta": "ATL", "Baltimore": "BAL", "Buffalo": "BUF", "Carolina": "CAR", "Chicago": "CHI", 
        "Cincinnati": "CIN", "Cleveland": "CLE", "Dallas": "DAL", "Denver": "DEN", "Detroit": "DET", "Green Bay": "GB", 
        "Houston": "HOU", "Indianapolis": "IND", "Jacksonville": "JAX", "Kansas City": "KC", "Las Vegas": "LV", 
        "Miami": "MIA", "Minnesota": "MIN", "New England": "NE", "New Orleans": "NO", "Philadelphia": "PHI", 
        "Pittsburgh": "PIT", "San Francisco": "SF", "Seattle": "SEA", "Tampa Bay": "TB", "Tampa Bay": "TB", 
        "Tennessee": "TEN", "Washington": "WAS", "N.Y. Jets": "NYJ", "N.Y. Giants": "NYG", "L.A. Chargers": "LAC", 
        "L.A. Rams": "LA", "Los Angeles": "LA"
    }
    name = name.replace('\u2008', ' ').replace('\xa0', ' ').strip()
    if name in mapping: return mapping[name]
    for k, v in mapping.items():
        if k in name: return v
    return name

def build_valid_schedule(games_list):
    """Attempt to assign games to 17 weeks greedily with random restarts"""
    for _ in range(100):
        random.shuffle(games_list)
        weeks = [[] for _ in range(17)]
        game_q = list(games_list)
        
        success = True
        for g in game_q:
            ht, at = g
            placed = False
            
            candidates = []
            for w in range(17):
                if len(weeks[w]) < 16:
                    teams_in_week = set()
                    for existing_g in weeks[w]:
                        teams_in_week.add(existing_g[0])
                        teams_in_week.add(existing_g[1])
                    if ht not in teams_in_week and at not in teams_in_week:
                        candidates.append((len(weeks[w]), w))
                        
            if candidates:
                candidates.sort() # pick week with fewest games
                weeks[candidates[0][1]].append(g)
            else:
                success = False
                break
                
        if success:
            return weeks
            
    print("Could not find perfect 17-week distribution, using closest fit.")
    weeks = [[] for _ in range(17)]
    for g in games_list:
        ht, at = g
        placed = False
        candidates = []
        for w in range(17):
             teams_in_week = set()
             for existing_g in weeks[w]:
                 teams_in_week.add(existing_g[0])
                 teams_in_week.add(existing_g[1])
             if ht not in teams_in_week and at not in teams_in_week:
                 candidates.append((len(weeks[w]), w))
        if candidates:
            candidates.sort()
            weeks[candidates[0][1]].append(g)
        else:
            lengths = [(len(weeks[w]), w) for w in range(17)]
            lengths.sort()
            weeks[lengths[0][1]].append(g)
    return weeks

def build_schedule():
    with open(r"debug\2026 NFL season_ Team-by-team opponents for every game.htm", 'r', encoding='utf-8') as f:
        text = f.read()

    clean_text = re.sub(r'<[^>]+>', ' ', text)
    clean_text = ' '.join(clean_text.replace('\xa0', ' ').replace('\u2008', ' ').split())

    teams = [
        "Arizona Cardinals", "Atlanta Falcons", "Baltimore Ravens", "Buffalo Bills",
        "Carolina Panthers", "Chicago Bears", "Cincinnati Bengals", "Cleveland Browns",
        "Dallas Cowboys", "Denver Broncos", "Detroit Lions", "Green Bay Packers",
        "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars", "Kansas City Chiefs",
        "Las Vegas Raiders", "L.A. Chargers", "Los Angeles Chargers", "L.A. Rams", "Los Angeles Rams", "Miami Dolphins",
        "Minnesota Vikings", "New England Patriots", "New Orleans Saints", "N.Y. Giants", "New York Giants",
        "N.Y. Jets", "New York Jets", "Philadelphia Eagles", "Pittsburgh Steelers", "San Francisco 49ers",
        "Seattle Seahawks", "Tampa Bay Buccaneers", "Tennessee Titans", "Washington Commanders"
    ]

    all_games = set()
    
    start = 0
    while True:
        h = clean_text.find('Home: ', start)
        if h == -1: break
        
        a = clean_text.find('Away: ', h)
        if a == -1: break
        
        best_team = None
        best_idx = -1
        search_window = clean_text[max(0, h-1000):h]
        for t in teams:
            idx = search_window.rfind(t)
            if idx > best_idx:
                best_idx = idx
                best_team = t
                
        if best_team:
            opps = clean_text[h+6:a].split(',')
            for o in opps:
                c = clean_team(o.strip())
                if len(c) <= 3:
                    all_games.add((clean_team(best_team), c))
        
        start = a + 5

    print(f"Total unique games found: {len(all_games)}")
    
    for t in teams:
        ct = clean_team(t)
        hg = [g for g in all_games if g[0] == ct]
        if len(hg) < 8 and ct not in ["LA", "LAC", "NYJ", "NYG"]:
            print(f"{ct} is missing home games: {len(hg)}")

    games_list = list(all_games)
    weeks = build_valid_schedule(games_list)

    out_path = "debug/2026_mock_schedule.csv"
    with open(out_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["week", "home_team", "away_team", "game_type"])
        for w, w_games in enumerate(weeks):
            for ht, at in w_games:
                writer.writerow([w+1, ht, at, "REG"])

    print(f"Mock 2026 schedule written to {out_path}")

if __name__ == "__main__":
    build_schedule()
