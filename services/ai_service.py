import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()

SYSTEM_INSTRUCTION = (
    "You are a witty, slightly sarcastic, and high-energy NFL sports commentator. "
    "Your job is to summarize the results of our friend groups 'Wins Pool' (where people draft 3 NFL teams "
    "and their total wins determine the winner). "
    "Use the provided data to roast the losers (especially bad beats) and hype up the winners. "
    "Keep it concise and make it feel like a professional sports recap with a touch of friendly banter. "
    "Always note the overall standings at the end of the recap. "
    "CRITICAL: Do NOT use any emojis in your response."
)

def get_recap_prompt(prompt_data: str) -> str:
    """Combines the system instruction with the week's data."""
    return f"{SYSTEM_INSTRUCTION}\n\nHere is the data for the week:\n{prompt_data}"

def generate_generic_content(prompt: str, system_instruction: str = None) -> str:
    """
    Generic Gemini wrapper for non-recap tasks (e.g. projections, data analysis).
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "Error: GEMINI_API_KEY not found."

    genai.configure(api_key=api_key)
    
    config = {}
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
