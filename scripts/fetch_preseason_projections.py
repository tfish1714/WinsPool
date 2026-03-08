"""
fetch_preseason_projections.py — Uses Gemini AI to scrape and summarize win totals for next season.
Usage:
    python scripts/fetch_preseason_projections.py --year 2025
"""
import sys
import os
import json
import argparse
import pathlib
import requests
from bs4 import BeautifulSoup

# Ensure project root is on the path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from services.ai_service import generate_generic_content

CONFIG_PATH = pathlib.Path(__file__).parent.parent / 'config' / 'prediction_sources.json'

def get_sources():
    if not CONFIG_PATH.exists():
        return []
    with open(CONFIG_PATH, 'r') as f:
        data = json.load(f)
        return data.get("preseason_projections", [])

def fetch_content(url):
    try:
        print(f"  [fetch] {url}...")
        resp = requests.get(url, timeout=10)
        if resp.ok:
            # Extract text to reduce token usage
            soup = BeautifulSoup(resp.text, 'html.parser')
            # Focus on tables or main content if possible
            return soup.get_text(separator=' ', strip=True)[:15000] # Cap to 15k chars
        return f"Error: Status {resp.status_code}"
    except Exception as e:
        return f"Error: {str(e)}"

def main():
    parser = argparse.ArgumentParser(description="Fetch NFL preseason win projections via AI")
    parser.add_argument('--year', type=int, default=2025, help="Target season")
    args = parser.parse_args()

    sources = get_sources()
    if not sources:
        print("No sources found in config/prediction_sources.json")
        return

    print(f"[AI Predictor] Analyzing {len(sources)} sources for {args.year} projections...")
    
    combined_context = ""
    for url in sources:
        content = fetch_content(url)
        combined_context += f"--- SOURCE: {url} ---\n{content}\n\n"

    system_instr = (
        "You are a data analyst focusing on NFL preseason win total projections. "
        "Extract the projected win totals for all 32 NFL teams for the specified season. "
        "Return the data as a clean JSON list of objects: {'team': 'TM', 'wins': 9.5}. "
        "Use official 3-letter team codes (e.g. KC, SF, PHI). "
        "If multiple projections exist for a team, calculate an average. "
        "Return ONLY the raw JSON array."
    )
    
    prompt = f"Target Season: {args.year}\n\nContext Data:\n{combined_context}"
    
    print("[AI Predictor] Consulting Gemini for the consensus summary...")
    result = generate_generic_content(prompt, system_instruction=system_instr)
    
    # Try to parse and save
    try:
        # Clean markdown wrappers if present
        cleaned = result.replace('```json', '').replace('```', '').strip()
        projections = json.loads(cleaned)
        
        output_file = pathlib.Path(__file__).parent.parent / 'data' / f'projections_{args.year}.json'
        with open(output_file, 'w') as f:
            json.dump(projections, f, indent=4)
            
        print(f"\n[OK] Consensus projections saved to {output_file}")
        print(f"Total teams matched: {len(projections)}")
    except Exception as e:
        print("\n[ERR] Failed to parse AI response into JSON:")
        print(result)
        print(f"Error: {e}")

if __name__ == '__main__':
    main()
