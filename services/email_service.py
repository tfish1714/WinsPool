import logging
import os
from dotenv import load_dotenv
import resend

load_dotenv()

logger = logging.getLogger(__name__)


def send_mfa_code_email(to_email: str, code: str) -> bool:
    """Send a 6-digit MFA verification code to a single recipient."""
    html = f"""
    <p>Your WinsPool verification code is:</p>
    <h2 style="letter-spacing:4px;">{code}</h2>
    <p>This code expires in 10 minutes. Do not share it with anyone.</p>
    """
    return _send(to_email, "WinsPool Login Verification Code", html)


def send_weekly_recap_email(to_emails: list, subject: str, html_content: str) -> bool:
    """Send a weekly recap email to each recipient individually via Resend."""
    return all(_send(email, subject, html_content) for email in to_emails)


def send_alert_email(subject: str, message: str) -> bool:
    """Send a job-failure alert to the address in ALERT_EMAIL. Returns False (no-op) if unconfigured."""
    to_email = os.getenv("ALERT_EMAIL")
    if not to_email:
        logger.error("ALERT_EMAIL not set — alert email not sent. Subject: %s", subject)
        return False
    html = f"<p>{subject}</p><pre>{message}</pre>"
    return _send(to_email, subject, html)


def _send(to_email: str, subject: str, html: str) -> bool:
    """Send a single transactional email via Resend. Returns True on success."""
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        logger.error("RESEND_API_KEY not set — email not sent.")
        return False

    resend.api_key = api_key
    from_email = os.getenv("FROM_EMAIL", "onboarding@resend.dev")

    try:
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "html": html,
        })
        return True
    except Exception as e:
        logger.error("Error sending email to %s: %s", to_email, e)
        return False
