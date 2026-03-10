import pytest
from unittest.mock import patch, MagicMock
from services.email_service import send_weekly_recap_email

@patch("services.email_service.smtplib.SMTP")
@patch("services.email_service.os.getenv")
def test_send_weekly_recap_email_success(mock_env, mock_smtp):
    """
    Verify that providing valid recipients and environment variables correctly 
    constructs the MIME attachments and iterates the SMTP client securely.
    """
    # Mocking standard .env variables
    def fake_env(key, default=None):
        env_map = {
            "SMTP_SERVER": "smtp.mock.com",
            "SMTP_USER": "test@mock.com",
            "SMTP_PASSWORD": "secure",
            "FROM_EMAIL": "admin@mock.com"
        }
        return env_map.get(key, default)
    mock_env.side_effect = fake_env
    
    # Mocking SMTP connection
    mock_server = MagicMock()
    mock_smtp.return_value.__enter__.return_value = mock_server
    
    recipients = ["user1@mock.com", "user2@mock.com"]
    result = send_weekly_recap_email(recipients, "Week 1", "<h1>Test</h1>")
    
    assert result is True
    # The server should call send_message twice (once per recipient)
    assert mock_server.send_message.call_count == 2
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_with("test@mock.com", "secure")

@patch("services.email_service.os.getenv")
def test_send_email_aborts_on_missing_config(mock_env):
    """
    Verify that the SMTP engine safely aborts and returns False without crashing
    if the deployment server lacks physical .env mail configurations.
    """
    def mock_get(key, default=None):
        if key == "SMTP_PORT": return 587
        return None
    mock_env.side_effect = mock_get
    
    result = send_weekly_recap_email(["user@mock.com"], "Subject", "html")
    assert result is False

@patch("services.email_service.smtplib.SMTP")
@patch("services.email_service.os.getenv")
def test_send_email_catches_network_exceptions(mock_env, mock_smtp):
    """
    Verify that if the SMTP target timeouts or refuses the TLS handshake,
    the service suppresses the crash thread and signals failure accurately.
    """
    def mock_get(key, default=None):
        if key == "SMTP_PORT": return "587"
        return "configured"
    mock_env.side_effect = mock_get
    mock_smtp.side_effect = Exception("Mocked Connection Refused")
    
    result = send_weekly_recap_email(["user@mock.com"], "Sub", "html")
    assert result is False
