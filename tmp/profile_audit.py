import os
import glob
import pandas as pd
import json
import warnings
warnings.filterwarnings('ignore')

rawdata_dir = r'C:\Users\fisch\OneDrive\Documents\Code\WinsPool\rawdata'
csv_files = glob.glob(os.path.join(rawdata_dir, '**/*.csv'), recursive=True)

schema = {}
common_cols = {}
for f in csv_files:
    if 'dont use' in f.lower(): continue
    try:
        df = pd.read_csv(f, low_memory=False)
        filename = os.path.basename(f)
        if filename not in schema:
            # Subsample for speed if large
            if len(df) > 5000:
                sample_df = df.sample(5000)
            else:
                sample_df = df
            
            numeric_cols = df.select_dtypes(include=['number']).columns
            profile = {}
            for col in numeric_cols:
                profile[col] = {
                    'min': float(sample_df[col].min()) if not pd.isna(sample_df[col].min()) else None,
                    'max': float(sample_df[col].max()) if not pd.isna(sample_df[col].max()) else None,
                    'mean': float(sample_df[col].mean()) if not pd.isna(sample_df[col].mean()) else None,
                    'missing_pct': float(df[col].isna().mean() * 100)
                }
            
            non_numeric = df.select_dtypes(exclude=['number']).columns
            
            schema[filename] = {
                'numeric_columns': list(numeric_cols),
                'non_numeric_columns': list(non_numeric),
                'numeric_profile': profile,
                'rows': len(df)
            }
            
            for col in df.columns:
                if col not in common_cols:
                    common_cols[col] = []
                common_cols[col].append(filename)
    except Exception as e:
        print(f'Error reading {f}: {e}')

summary = {
    'total_files_processed': len(schema),
    'common_columns': {k: v for k, v in common_cols.items() if len(v) > 1},
    'files': schema
}

out_path = r'C:\Users\fisch\OneDrive\Documents\Code\WinsPool\tmp\rawdata_profile.json'
os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, 'w') as f:
    json.dump(summary, f, indent=2)

print(f"Profile saved to {out_path}")
