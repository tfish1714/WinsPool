import unittest
from services.ai_service import generate_weekly_summary

def test_generate_summary_happy_path(mock_gemini):
    """
    Verify that providing a clean prompt correctly routes through the mocked
    Google Generative AI service and returns the designated output string.
    """
    result = generate_weekly_summary("Analyze this fake week.")
    
    assert result == "Mocked AI Weekly Summary Result."
    
    # Verify the mock was actually called
    # GenerativeModel from mock_gemini called without system_instruction
    mock_gemini.assert_called_once_with('gemini-1.5-flash')
    
def test_generate_summary_handles_exceptions(mock_gemini):
    """
    Verify that if the Gemini API throws a 500 or timeout error, the generator 
    safely catches the exception and returns an actionable error string
    without crashing the main application thread.
    """
    # Force the mock generator to throw a generic Exception simulating an outage
    mock_instance = mock_gemini.return_value
    mock_instance.generate_content.side_effect = Exception("Google AI Overloaded")
    
    result = generate_weekly_summary("Analyze this fake week.")
    
    assert "Error: Google AI Overloaded" in result

def test_get_recap_prompt():
    from services.ai_service import get_recap_prompt
    result = get_recap_prompt("Fake data summary")
    assert "You are a witty, slightly sarcastic" in result
    assert "Fake data summary" in result

def test_get_draft_recap_prompt():
    from services.ai_service import get_draft_recap_prompt
    result = get_draft_recap_prompt("Fake draft data")
    assert "You are a witty, slightly sarcastic" in result
    assert "Fake draft data" in result
    assert "projected total wins" in result
