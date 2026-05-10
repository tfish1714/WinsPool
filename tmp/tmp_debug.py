import re

def clean_team(name):
    # Mapping
    mapping = {
        "Arizona": "ARI", "Atlanta": "ATL", "Baltimore": "BAL", "Buffalo": "BUF",
        "Carolina": "CAR", "Chicago": "CHI", "Cincinnati": "CIN", "Cleveland": "CLE",
        "Dallas": "DAL", "Denver": "DEN", "Detroit": "DET", "Green Bay": "GB",
        "Houston": "HOU", "Indianapolis": "IND", "Jacksonville": "JAX", "Kansas City": "KC",
        "Las Vegas": "LV", "L.A. Chargers": "LAC", "Los Angeles Chargers": "LAC", 
        "L.A. Rams": "LA", "Los Angeles Rams": "LA", "Miami": "MIA", 
        "Minnesota": "MIN", "New England": "NE", "New Orleans": "NO", 
        "N.Y. Giants": "NYG", "New York Giants": "NYG", "N.Y. Jets": "NYJ", "New York Jets": "NYJ",
        "Philadelphia": "PHI", "Pittsburgh": "PIT", "San Francisco": "SF", 
        "Seattle": "SEA", "Tampa Bay": "TB", "Tampa Bay": "TB", "Tennessee": "TEN", 
        "Washington": "WAS", "L.A. Rams": "LA"
    }

    name = name.replace('\u2008', ' ').strip()
    
    for trps in [" Cardinals", " Falcons", " Ravens", " Bills", " Panthers", " Bears", " Bengals", " Browns", " Cowboys", " Broncos", " Lions", " Packers", " Texans", " Colts", " Jaguars", " Chiefs", " Raiders", " Chargers", " Rams", " Dolphins", " Vikings", " Patriots", " Saints", " Giants", " Jets", " Eagles", " Steelers", " 49ers", " Seahawks", " Buccaneers", " Titans", " Commanders"]:
        if name.endswith(trps):
            name = name.replace(trps, "")
            break
            
    if name in mapping: return mapping[name]
    for k, v in mapping.items():
        if k in name: return v
    return name

with open(r"debug\2026 NFL season_ Team-by-team opponents for every game.htm", 'r', encoding='utf-8') as f:
    text = f.read()

clean_text = re.sub(r'<[^>]+>', ' ', text)
clean_text = ' '.join(clean_text.replace('\xa0', ' ').replace('\u2008', ' ').split())

teams = [
    "Arizona Cardinals", "Atlanta Falcons", "Baltimore Ravens", "Buffalo Bills",
    "Carolina Panthers", "Chicago Bears", "Cincinnati Bengals", "Cleveland Browns",
    "Dallas Cowboys", "Denver Broncos", "Detroit Lions", "Green Bay Packers",
    "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars", "Kansas City Chiefs",
    "Las Vegas Raiders", "L.A. Chargers", "L.A. Rams", "Miami Dolphins",
    "Minnesota Vikings", "New England Patriots", "New Orleans Saints", "N.Y. Giants",
    "N.Y. Jets", "Philadelphia Eagles", "Pittsburgh Steelers", "San Francisco 49ers",
    "Seattle Seahawks", "Tampa Bay Buccaneers", "Tennessee Titans", "Washington Commanders"
]

all_games = set()
for t in teams:
    idx = clean_text.find(t)
    if idx != -1:
        h = clean_text.find('Home: ', idx)
        a = clean_text.find('Away: ', h)
        if h != -1 and a != -1:
            opps = clean_text[h+6:a].split(',')
            for o in opps:
                c = clean_team(o)
                # It should be length 2 or 3!
                if len(c) <= 3:
                    all_games.add((clean_team(t), c))
                else:
                    print(f"FAILED TO MAP {clean_team(t)} OPPONENT: '{o}' -> '{c}'")

for t in teams:
    ct = clean_team(t)
    hg = [g for g in all_games if g[0] == ct]
    if len(hg) < 8:
        print(f"{ct} has {len(hg)} mapped home games.")
