import sys
from bs4 import BeautifulSoup

def parse_html(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        html_content = f.read()

    soup = BeautifulSoup(html_content, 'html.parser')
    # Focus on h3 (often team names) and paragraphs
    elements = soup.find_all(['h3', 'p'])
    
    current_team = None
    
    for e in elements:
        text = e.get_text().strip()
        if not text:
            continue
            
        if e.name == 'h3':
            print(f"TEAM: {text}")
            current_team = text
        elif text.startswith("Home:") or text.startswith("Away:"):
            print(f"  {text}")
            
if __name__ == "__main__":
    parse_html(r"debug\2026 NFL season_ Team-by-team opponents for every game.htm")
