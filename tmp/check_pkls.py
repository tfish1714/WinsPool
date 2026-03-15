import pandas as pd
import pathlib

def check_pkl(name):
    path = pathlib.Path(".local_db") / f"{name}.pkl"
    if not path.exists():
        print(f"{name}: Missing")
        return
    try:
        df = pd.read_pickle(path)
        print(f"{name}: {len(df)} rows")
        if not df.empty:
            print(df.head(3))
            if 'season' in df.columns:
                print(f"Seasons: {df['season'].unique()}")
    except Exception as e:
        print(f"{name}: Error reading - {e}")

print("Checking Local DB contents:")
check_pkl("draft_results")
check_pkl("draft_order")
check_pkl("players")
