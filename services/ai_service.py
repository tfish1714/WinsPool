"""services/ai_service.py -- Thin wrapper around the Google Gemini API for generating weekly recap text."""
import os
from dotenv import load_dotenv

load_dotenv()

SYSTEM_INSTRUCTION = (
    "You are a witty, slightly sarcastic, and high-energy NFL sports commentator. "
    "Your job is to summarize the results of our friend group's 'Wins Pool' (where human players draft 3 NFL teams "
    "each and their total wins determine the pool champion). "
    "PRIMARY FOCUS: The recap MUST focus entirely on the human players in the Wins Pool and their competition, "
    "rivalries, and banter against each other. It must NOT be a generic rundown of NFL games in isolation. "
    "Every game and statistic mentioned must be tied directly back to the pool member who drafted that team. "
    "MANDATORY NARRATIVE HIGHLIGHTS: "
    "1. HEAD-TO-HEAD CLASHES: Whenever two pool members' drafted teams played each other, spotlight it as a marquee direct showdown. "
    "Highlight who took bragging rights, who suffered the loss, and the resulting swing in the standings. "
    "2. DOMINANT PERFORMANCES: Celebrate members who had undefeated weeks (e.g. 3-0), blowout victories, or dramatic comebacks. "
    "3. BAD BEATS & HEARTBREAKERS: Mercilessly roast members who suffered agonizing losses -- especially 1-score games, walk-off plays, "
    "fourth-quarter collapses, turnover meltdowns, or embarrassing losses to undrafted NFL teams. "
    "4. GAME CONTEXT & STATS: Use the provided key stats (turnovers, total yards, red zone efficiency, decisive plays, and player stat leaders) "
    "as ammunition to explain WHY a member won or suffered a bad beat. "
    "5. STANDINGS MOVEMENT: Note who surged up the board, who slipped, and summarize the overall standings at the end. "
    "CRITICAL RULES: "
    "- Do NOT use any emojis in your response. "
    "- Never invent a fact -- a score, play, stat, record, injury, or event -- that is not explicitly present in the provided data below. "
    "Factual accuracy is strict: keep commentary, jokes, roasts, analogies, and tone sharp, funny, and personality-driven based strictly on the real facts provided."
)


def get_recap_prompt(prompt_data: str) -> str:
    """Combines the system instruction with the week's data."""
    return f"{SYSTEM_INSTRUCTION}\n\nHere is the data for the week:\n{prompt_data}"
    
def get_draft_recap_prompt(prompt_data: str) -> str:
    """Combines the system instruction with the preseason draft data."""
    return f"{SYSTEM_INSTRUCTION}\n\nHere is the preseason draft data (including projected total wins for each player's roster). Roast their picks and give each player a draft grade!\n{prompt_data}"

def generate_generic_content(prompt: str, system_instruction: str = None) -> str:
    """
    Generic Gemini wrapper for non-recap tasks (e.g. projections, data analysis).
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "Error: GEMINI_API_KEY not found."

    import google.generativeai as genai
    genai.configure(api_key=api_key)

    if system_instruction:
        model = genai.GenerativeModel('gemini-1.5-flash', system_instruction=system_instruction)
    else:
        model = genai.GenerativeModel('gemini-1.5-flash')

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Error: {str(e)}"

def generate_weekly_summary(prompt_data: str) -> str:
    """
    Existing specialized recap generation using the defined sports commentator persona.
    """
    full_prompt = get_recap_prompt(prompt_data)
    return generate_generic_content(full_prompt)
