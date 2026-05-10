import os
import sys
import pandas as pd
from pprint import pprint

# Ensure we can import from the root services directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.nn_projection_engine import NNProjectionEngine

def run_test():
    print("Loading mock 2026 schedule...")
    schedule_path = 'debug/2026_mock_schedule.csv'
    
    if not os.path.exists(schedule_path):
        print(f"Error: Could not find {schedule_path}")
        return
        
    schedule_df = pd.read_csv(schedule_path)
    print(f"Loaded {len(schedule_df)} mock games.")
    
    print("Instantiating Neural Network Projection Engine...")
    engine = NNProjectionEngine()
    engine.initialize(2026)
    
    print("Running 10,000 Neural Network Monte Carlo simulations...")
    # Run the simulation using the NN model vs the mocked schedule dataframe
    # We pass n_sims=10000 for high stability
    results = engine.get_team_projected_wins(schedule_df, n_sims=10000)
    
    print("\n==============================================")
    print("   2026 NEURAL NETWORK WIN PROJECTIONS (MOCK)")
    print("==============================================")
    
    # Sort results by projected wins descending
    sorted_results = sorted(results.items(), key=lambda x: x[1], reverse=True)
    
    for i, (team, wins) in enumerate(sorted_results):
        print(f"{i+1:2d}. {team:<4}: {wins:>4.1f} Wins")
        
    print("==============================================")
    
    out_path = 'debug/2026_mock_predictions.csv'
    df_out = pd.DataFrame(sorted_results, columns=['team', 'projected_wins'])
    df_out.to_csv(out_path, index=False)
    print(f"\nSaved raw output to {out_path}")

if __name__ == '__main__':
    run_test()
