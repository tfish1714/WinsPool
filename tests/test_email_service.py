from unittest.mock import patch, MagicMock
from services.email_service import (
    send_weekly_recap_email, send_mfa_code_email, send_alert_email, send_draft_order_email,
    send_on_the_clock_email, send_betting_edge_email,
)


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv", return_value="re_test_key")
def test_send_weekly_recap_email_success(mock_getenv, mock_send):
    """Each recipient gets a separate Resend call."""
    mock_send.return_value = {"id": "abc123"}
    result = send_weekly_recap_email(["a@x.com", "b@x.com"], "Week 1", "<h1>Test</h1>")
    assert result is True
    assert mock_send.call_count == 2


@patch("services.email_service.os.getenv", return_value=None)
def test_send_email_aborts_on_missing_api_key(mock_getenv):
    """Returns False without sending when RESEND_API_KEY is absent."""
    result = send_weekly_recap_email(["user@x.com"], "Subject", "html")
    assert result is False


@patch("services.email_service.resend.Emails.send", side_effect=Exception("network error"))
@patch("services.email_service.os.getenv", return_value="re_test_key")
def test_send_email_catches_exceptions(mock_getenv, mock_send):
    """Returns False and logs when Resend raises."""
    result = send_weekly_recap_email(["user@x.com"], "Subject", "html")
    assert result is False


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv", return_value="re_test_key")
def test_send_mfa_code_email(mock_getenv, mock_send):
    """MFA email sends exactly once to the target address."""
    mock_send.return_value = {"id": "mfa123"}
    result = send_mfa_code_email("user@x.com", "123456")
    assert result is True
    mock_send.assert_called_once()
    call_params = mock_send.call_args[0][0]
    assert call_params["to"] == ["user@x.com"]
    assert "123456" in call_params["html"]


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv")
def test_send_alert_email_success(mock_getenv, mock_send):
    """Alert email sends to ALERT_EMAIL with subject/message in the body."""
    def getenv_side_effect(key, default=None):
        return {"RESEND_API_KEY": "re_test_key", "ALERT_EMAIL": "alerts@x.com"}.get(key, default)
    mock_getenv.side_effect = getenv_side_effect
    mock_send.return_value = {"id": "alert123"}

    result = send_alert_email("winspool-sync-daily failed", "Step 'daily_nfl_sync.py' exited 1")

    assert result is True
    mock_send.assert_called_once()
    call_params = mock_send.call_args[0][0]
    assert call_params["to"] == ["alerts@x.com"]
    assert call_params["subject"] == "[WinsPool Alert] winspool-sync-daily failed"
    assert "daily_nfl_sync.py" in call_params["html"]


@patch("services.email_service.os.getenv")
def test_send_alert_email_aborts_without_alert_email(mock_getenv):
    """Returns False without sending when ALERT_EMAIL is not configured."""
    def getenv_side_effect(key, default=None):
        return {"RESEND_API_KEY": "re_test_key"}.get(key, default)
    mock_getenv.side_effect = getenv_side_effect

    result = send_alert_email("subject", "message")

    assert result is False


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv")
def test_send_draft_order_email_single_call_to_all_recipients(mock_getenv, mock_send):
    """One Resend call with every recipient in `to`, and Reply-To set to ALERT_EMAIL."""
    def getenv_side_effect(key, default=None):
        return {"RESEND_API_KEY": "re_test_key", "ALERT_EMAIL": "admin@x.com"}.get(key, default)
    mock_getenv.side_effect = getenv_side_effect
    mock_send.return_value = {"id": "draft123"}

    ordered_players = [
        {"position": 1, "name": "Alice Anderson"},
        {"position": 2, "name": "Bob Brown"},
    ]
    result = send_draft_order_email(["alice@x.com", "bob@x.com"], 2099, ordered_players)

    assert result is True
    mock_send.assert_called_once()
    call_params = mock_send.call_args[0][0]
    assert call_params["to"] == ["alice@x.com", "bob@x.com"]
    assert call_params["reply_to"] == ["admin@x.com"]
    assert "2099" in call_params["subject"]
    assert "Alice Anderson" in call_params["html"]
    assert "Bob Brown" in call_params["html"]


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv", return_value="re_test_key")
def test_send_draft_order_email_no_recipients(mock_getenv, mock_send):
    """Returns False and never calls Resend when there are no recipient emails."""
    result = send_draft_order_email([], 2099, [{"position": 1, "name": "Alice"}])

    assert result is False
    mock_send.assert_not_called()


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv")
def test_send_draft_order_email_includes_draft_room_link(mock_getenv, mock_send):
    """Body links to /draft?season=<season>, built from APP_BASE_URL, for each season."""
    def getenv_side_effect(key, default=None):
        return {
            "RESEND_API_KEY": "re_test_key",
            "APP_BASE_URL": "https://winspool.example.com",
        }.get(key, default)
    mock_getenv.side_effect = getenv_side_effect
    mock_send.return_value = {"id": "draft123"}

    send_draft_order_email(["alice@x.com"], 2031, [{"position": 1, "name": "Alice"}])
    first_call_html = mock_send.call_args[0][0]["html"]
    assert 'href="https://winspool.example.com/draft?season=2031"' in first_call_html

    send_draft_order_email(["alice@x.com"], 2032, [{"position": 1, "name": "Alice"}])
    second_call_html = mock_send.call_args[0][0]["html"]
    assert 'href="https://winspool.example.com/draft?season=2032"' in second_call_html


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv")
def test_send_draft_order_email_link_defaults_to_localhost(mock_getenv, mock_send):
    """Falls back to http://localhost:8000 when APP_BASE_URL is unset (local dev)."""
    def getenv_side_effect(key, default=None):
        return {"RESEND_API_KEY": "re_test_key"}.get(key, default)
    mock_getenv.side_effect = getenv_side_effect
    mock_send.return_value = {"id": "draft123"}

    send_draft_order_email(["alice@x.com"], 2099, [{"position": 1, "name": "Alice"}])

    call_params = mock_send.call_args[0][0]
    assert 'href="http://localhost:8000/draft?season=2099"' in call_params["html"]


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv")
def test_send_on_the_clock_email(mock_getenv, mock_send):
    """Names the player and pick number, and links to the draft room."""
    def getenv_side_effect(key, default=None):
        return {
            "RESEND_API_KEY": "re_test_key",
            "APP_BASE_URL": "https://winspool.example.com",
        }.get(key, default)
    mock_getenv.side_effect = getenv_side_effect
    mock_send.return_value = {"id": "clock123"}

    result = send_on_the_clock_email("alice@x.com", "Alice", 2026, 14)

    assert result is True
    mock_send.assert_called_once()
    call_params = mock_send.call_args[0][0]
    assert call_params["to"] == ["alice@x.com"]
    assert "14" in call_params["subject"]
    assert "Alice" in call_params["html"]
    assert 'href="https://winspool.example.com/draft?season=2026"' in call_params["html"]


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv")
def test_send_on_the_clock_email_aborts_on_missing_api_key(mock_getenv, mock_send):
    """Returns False without sending when RESEND_API_KEY is absent."""
    mock_getenv.side_effect = lambda key, default=None: default if key != "RESEND_API_KEY" else None
    result = send_on_the_clock_email("alice@x.com", "Alice", 2026, 14)
    assert result is False


@patch("services.email_service.resend.Emails.send")
def test_send_disabled_via_env_var_never_calls_resend(mock_send, monkeypatch):
    """DISABLE_OUTBOUND_EMAIL=true short-circuits before any Resend call, even with a valid API key."""
    monkeypatch.setenv("DISABLE_OUTBOUND_EMAIL", "true")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")

    result = send_weekly_recap_email(["user@x.com"], "Subject", "html")

    assert result is True
    mock_send.assert_not_called()


@patch("services.email_service.resend.Emails.send")
def test_send_multi_disabled_via_env_var_never_calls_resend(mock_send, monkeypatch):
    """Same gate applies to the multi-recipient send path (send_draft_order_email)."""
    monkeypatch.setenv("DISABLE_OUTBOUND_EMAIL", "true")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")

    result = send_draft_order_email(["a@x.com", "b@x.com"], 3000, [{"position": 1, "name": "Test"}])

    assert result is True
    mock_send.assert_not_called()


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv", return_value="re_test_key")
def test_send_betting_edge_email_includes_both_tiers(mock_getenv, mock_send):
    mock_send.return_value = {"id": "bet123"}
    week_summary = {
        "season": 2026, "week": 3,
        "validated_angle_matches": [{
            "metric": "ats",
            "conditions": [{"feature": "elo_diff", "label": "Elo Diff", "min": 50.0}],
            "train_rate": 0.62, "train_n": 120, "test_rate": 0.58, "test_n": 30,
            "games": [{"home_team": "KC", "away_team": "SF", "matched_sides": ["home"]}],
        }],
        "raw_edge_outliers": [{
            "home_team": "BUF", "away_team": "MIA", "edge_vs_vegas": 4.5,
            "model_spread": 3.0, "vegas_line": -1.5, "ats_pick": "BUF",
        }],
    }

    result = send_betting_edge_email("owner@x.com", week_summary)

    assert result is True
    mock_send.assert_called_once()
    call_params = mock_send.call_args[0][0]
    assert call_params["to"] == ["owner@x.com"]
    assert call_params["subject"] == "[WinsPool] Week 3 betting edges"
    assert "Elo Diff" in call_params["html"]
    assert "KC" in call_params["html"] and "SF" in call_params["html"]
    assert "BUF" in call_params["html"] and "MIA" in call_params["html"]
    assert "4.5" in call_params["html"]


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv", return_value="re_test_key")
def test_send_betting_edge_email_handles_empty_angle_matches(mock_getenv, mock_send):
    """Caller (the script) decides whether to send at all when both tiers are
    empty -- this function itself must not crash if given an empty tier."""
    mock_send.return_value = {"id": "bet124"}
    week_summary = {
        "season": 2026, "week": 3,
        "validated_angle_matches": [],
        "raw_edge_outliers": [{
            "home_team": "BUF", "away_team": "MIA", "edge_vs_vegas": -4.5,
            "model_spread": -3.0, "vegas_line": 1.5, "ats_pick": "MIA",
        }],
    }
    result = send_betting_edge_email("owner@x.com", week_summary)
    assert result is True
    call_params = mock_send.call_args[0][0]
    assert "Validated angle matches" not in call_params["html"]
    assert "Raw edge outliers" in call_params["html"]


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv", return_value="re_test_key")
def test_send_betting_edge_email_escapes_html_in_user_fields(mock_getenv, mock_send):
    """HTML-significant characters in user-sourced fields (team names, condition labels,
    ats_pick) are escaped to prevent injection. Regression test: verifies html.escape()
    is not dropped from any interpolated field."""
    mock_send.return_value = {"id": "bet125"}
    week_summary = {
        "season": 2026, "week": 3,
        "validated_angle_matches": [{
            "metric": "ats",
            "conditions": [{"feature": "test", "label": "Label with <script> tag", "min": 50.0}],
            "train_rate": 0.62, "train_n": 120, "test_rate": 0.58, "test_n": 30,
            "games": [{"home_team": "K&C", "away_team": "S<F>", "matched_sides": ["home"]}],
        }],
        "raw_edge_outliers": [{
            "home_team": "BUF & MIA", "away_team": "DEN<test>", "edge_vs_vegas": 4.5,
            "model_spread": 3.0, "vegas_line": -1.5, "ats_pick": "BUF & Co",
        }],
    }

    result = send_betting_edge_email("owner@x.com", week_summary)

    assert result is True
    call_params = mock_send.call_args[0][0]
    html_output = call_params["html"]

    # Verify escaped versions ARE present
    assert "&lt;script&gt;" in html_output
    assert "&lt;test&gt;" in html_output
    assert "K&amp;C" in html_output
    assert "S&lt;F&gt;" in html_output
    assert "BUF &amp; MIA" in html_output
    assert "BUF &amp; Co" in html_output

    # Verify raw unescaped versions are NOT present (malicious content)
    assert "<script>" not in html_output
    assert "<test>" not in html_output
    assert "K&C" not in html_output or "K&amp;C" in html_output  # & alone is ambiguous
    assert "S<F>" not in html_output
