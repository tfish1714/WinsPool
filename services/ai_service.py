import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# The genai module is loaded lazily at call time to prevent the server
# from hanging on startup.  Both google-generativeai (old) and
# google-genai (new) SDKs can block during import on machines with
# restricted network access due to deprecation / version checks.

_genai = None


def _load_genai():
    """Lazy-load whichever Google Generative AI SDK is installed."""
    global _genai
    if _genai is not None:
        return _genai

    # Try the new SDK first, then fall back to the legacy one.
    try:
        from google import genai as _mod
        _genai = {"sdk": "new", "mod": _mod}
        logger.info("Loaded google-genai (new SDK).")
        return _genai
    except ImportError:
        pass

    try:
        import google.generativeai as _mod
        _genai = {"sdk": "old", "mod": _mod}
        logger.info("Loaded google-generativeai (legacy SDK).")
        return _genai
    except ImportError:
        pass

    logger.warning("No Google Generative AI SDK found. AI features will be unavailable.")
    _genai = {"sdk": None, "mod": None}
    return _genai


SYSTEM_INSTRUCTION = (
    "You are a witty, slightly sarcastic, and high-energy NFL sports commentator. "
    "Your job is to summarize the results of our friend groups 'Wins Pool' (where people draft 3 NFL teams "
    "and their total wins determine the winner). "
    "Use the provided data to roast the losers (especially bad beats) and hype up the winners. "
    "Keep it concise and make it feel like a professional sports recap with a touch of friendly banter. "
    "Always note the overall standings at the end of the recap. "
    "CRITICAL: Do NOT use any emojis in your response."
)

DRAFT_RECAP_INSTRUCTION = (
    "You are a witty, highly analytical NFL sports commentator. "
    "Your job is to grade the 'Wins Pool' draft that just concluded. "
    "You will be given the users' personal preseason predictions, the actual draft results, "
    "and the internet consensus/Vegas odds. "
    "Compare the players' picks against objective reality. Roast the terrible value picks, "
    "praise the steals, and give an overall assessment of who had the best and worst draft. "
    "Keep it concise, engaging, and professional. "
    "CRITICAL: Do NOT use any emojis in your response."
)


def get_recap_prompt(prompt_data: str) -> str:
    """Combines the system instruction with the week's data."""
    return f"{SYSTEM_INSTRUCTION}\n\nHere is the data for the week:\n{prompt_data}"

def get_draft_recap_prompt(predictions_data: str, draft_results_data: str) -> str:
    """Combines prediction data, draft results, and consensus data."""
    return (
        f"{DRAFT_RECAP_INSTRUCTION}\n\n"
        f"--- USER PREDICTIONS & CONSENSUS ---\n{predictions_data}\n\n"
        f"--- DRAFT RESULTS ---\n{draft_results_data}"
    )

def generate_generic_content(prompt: str, system_instruction: str = None) -> str:
    """
    Generic Gemini wrapper for non-recap tasks (e.g. projections, data analysis).
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "Error: GEMINI_API_KEY not found."

    info = _load_genai()
    if info["mod"] is None:
        return "Error: No Google Generative AI SDK installed."

    try:
        if info["sdk"] == "new":
            from google.genai import types
            client = info["mod"].Client(api_key=api_key)
            config = types.GenerateContentConfig()
            if system_instruction:
                config.system_instruction = system_instruction
            response = client.models.generate_content(
                model='gemini-1.5-flash',
                contents=prompt,
                config=config
            )
        else:
            genai = info["mod"]
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(
                'gemini-1.5-flash',
                system_instruction=system_instruction
            )
            response = model.generate_content(prompt)

        return response.text
    except Exception as e:
        logger.error("AI generation failed: %s", e)
        return f"Error: {str(e)}"

def generate_weekly_summary(prompt_data: str) -> str:
    """
    Existing specialized recap generation using the defined sports commentator persona.
    """
    full_prompt = get_recap_prompt(prompt_data)
    return generate_generic_content(full_prompt)
