import os
import sys
import pathlib
import json

# Ensure project root is on the path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

os.environ['USE_LOCAL_DATA'] = 'False'

from services.cache_service import get_cached

def inspect():
    print("[inspect] Reading prediction_snapshot_2026_0 from remote...")
    snapshot = get_cached('prediction_snapshot', 2026, 0)
    if snapshot:
        print("[inspect] SUCCESS: Snapshot found.")
        print(f"[inspect] Keys: {list(snapshot.keys())}")
        if 'team_projections' in snapshot:
            tp = snapshot['team_projections']
            print(f"[inspect] Team Projections (first 5): {list(tp.items())[:5]}")
        else:
            print("[inspect] FAILURE: 'team_projections' missing from snapshot.")
    else:
        print("[inspect] FAILURE: Snapshot not found in cache.")

if __name__ == '__main__':
    inspect()
