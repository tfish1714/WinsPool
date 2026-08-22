from unittest.mock import patch, MagicMock
from services.email_service import send_weekly_recap_email, send_mfa_code_email, send_alert_email


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
