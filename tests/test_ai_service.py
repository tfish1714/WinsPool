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


# --- Recap output format: light markdown that renders well on the site ------

def test_prompt_asks_for_blank_lines_between_paragraphs():
    from services.ai_service import SYSTEM_INSTRUCTION
    assert "BLANK LINE between every paragraph" in SYSTEM_INSTRUCTION


def test_prompt_asks_for_markdown_headline_bold_names_and_bullet_standings():
    from services.ai_service import SYSTEM_INSTRUCTION
    assert "'## '" in SYSTEM_INSTRUCTION                  # headline
    assert "**bold**" in SYSTEM_INSTRUCTION               # names
    assert "- [Name] - [X] wins" in SYSTEM_INSTRUCTION    # bullet standings


def test_prompt_no_longer_bans_headings_or_asks_for_indented_standings():
    from services.ai_service import SYSTEM_INSTRUCTION
    assert "no '###'" not in SYSTEM_INSTRUCTION
    assert "indented plain-text list" not in SYSTEM_INSTRUCTION


def test_prompt_still_forbids_tables_html_and_emojis():
    from services.ai_service import SYSTEM_INSTRUCTION
    for needle in ("tables", "HTML", "emojis"):
        assert needle in SYSTEM_INSTRUCTION


def test_recap_prompt_and_draft_prompt_share_the_format_rules():
    from services.ai_service import get_recap_prompt, get_draft_recap_prompt
    assert "BLANK LINE between every paragraph" in get_recap_prompt("x")
    assert "BLANK LINE between every paragraph" in get_draft_recap_prompt("x")


def test_recap_in_the_requested_format_renders_as_headline_paragraphs_and_list():
    from services.recap_render import render_recap_html
    text = (
        "## Week 4: Sean Bailey Runs the Table\n\n"
        "**Sean Bailey** is on a different astral plane.\n\n"
        "**Chris Martino** needs a wellness check.\n\n"
        "**Season standings (through week 4)**\n\n"
        "- Sean Bailey - 10 wins\n- Chris Oesterheld - 8 wins\n- Chris Martino - 2 wins"
    )
    out = str(render_recap_html(text))
    assert out.startswith("<h4>Week 4: Sean Bailey Runs the Table</h4>")
    assert out.count("<p>") == 3                      # two body paragraphs + standings label
    assert "<strong>Sean Bailey</strong>" in out
    assert out.count("<li>") == 3 and "<ul>" in out
