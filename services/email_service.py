import html
import logging
import os
from dotenv import load_dotenv
import resend

load_dotenv()

logger = logging.getLogger(__name__)


def _app_base_url() -> str:
    """Base URL for links embedded in outbound emails.

    Defaults to local dev; production sets APP_BASE_URL to the deployed
    Cloud Run URL (see DEPLOY.md). Trailing slash stripped so callers can
    always append a leading-slash path without a double slash.
    """
    return os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")


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


def send_draft_order_email(to_emails: list, season: int, ordered_players: list) -> bool:
    """Announce a new season's draft order to the whole group in a single email.

    ordered_players: list of {"position": int, "name": str} in draft order.
    One Resend call with every recipient in `to` (not one send per player) --
    the point is a group-visible, everyone-sees-the-same-list announcement for
    draft-order transparency. Reply-To is ALERT_EMAIL (the admin's own address),
    not the Resend default sender.
    """
    if not to_emails:
        logger.warning("No recipient emails for %s draft order announcement — not sent.", season)
        return False

    rows = "".join(
        f"<li>Pick {p['position']}: {html.escape(p['name'])}</li>"
        for p in ordered_players
    )
    draft_room_url = f"{_app_base_url()}/draft?season={season}"
    html_body = f"""
    <p>The draft order for the {season} Wins Pool season has been set:</p>
    <ol>{rows}</ol>
    <p><a href="{draft_room_url}">Go to the draft room</a></p>
    """
    reply_to = os.getenv("ALERT_EMAIL")
    return _send_multi(to_emails, f"{season} Wins Pool Draft Order", html_body, reply_to=reply_to)


def send_alert_email(subject: str, message: str) -> bool:
    """Send a job-failure alert to the address in ALERT_EMAIL. Returns False (no-op) if unconfigured.

    Suppressed on any non-final Cloud Run Job retry attempt -- a job with
    maxRetries=3 (Cloud Run's default) makes 4 attempts total on a real
    failure, and without this every attempt would send its own copy of the
    same alert. CLOUD_RUN_TASK_ATTEMPT (0-indexed) is injected automatically
    by Cloud Run Jobs; MAX_RETRIES is NOT auto-injected -- it must be set as
    a job env var matching that job's --max-retries (see deploy docs), or
    this fails open and sends on every attempt rather than risk silently
    swallowing a real alert because the env var was never configured.
    """
    # Prefixed once, here, so both the actual Subject header and the body's
    # repeated subject line (below) pick it up -- lets the inbox be
    # filtered/searched on one fixed string regardless of which job sent it.
    subject = f"[WinsPool Alert] {subject}"

    attempt = os.getenv("CLOUD_RUN_TASK_ATTEMPT")
    max_retries = os.getenv("MAX_RETRIES")
    if attempt is not None and max_retries is not None and int(attempt) < int(max_retries):
        logger.info(
            "Suppressing alert email on attempt %s/%s (not the final retry). Subject: %s",
            attempt, max_retries, subject,
        )
        return False

    to_email = os.getenv("ALERT_EMAIL")
    if not to_email:
        logger.error("ALERT_EMAIL not set — alert email not sent. Subject: %s", subject)
        return False
    # Escape both -- nearly every Python traceback contains
    # `File "...", line N, in <module>`, and an unescaped `<module>` gets
    # silently eaten as an unknown HTML tag by mail clients, degrading the
    # most important content of a failure alert in the common case.
    body_html = f"<p>{html.escape(subject)}</p><pre>{html.escape(message)}</pre>"
    # Reply-To, not From: Resend can't send *as* an arbitrary address (only
    # a verified domain), and gmail.com's own DMARC policy would bounce or
    # spam-filter a spoofed From anyway. Reply-To has no such restriction --
    # hitting "reply" on an alert lands back in the same inbox it alerted,
    # without touching the sending identity at all.
    return _send(to_email, subject, body_html, reply_to=to_email)


def _send(to_email: str, subject: str, html: str, reply_to: str = None) -> bool:
    """Send a single transactional email via Resend. Returns True on success."""
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        logger.error("RESEND_API_KEY not set — email not sent.")
        return False

    resend.api_key = api_key
    from_email = os.getenv("FROM_EMAIL", "onboarding@resend.dev")

    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": html,
    }
    if reply_to:
        payload["reply_to"] = [reply_to]

    try:
        resend.Emails.send(payload)
        return True
    except Exception as e:
        logger.error("Error sending email to %s: %s", to_email, e)
        return False


def _send_multi(to_emails: list, subject: str, html: str, reply_to: str = None) -> bool:
    """Send one transactional email to multiple recipients (all in `to`) via Resend."""
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        logger.error("RESEND_API_KEY not set — email not sent.")
        return False

    resend.api_key = api_key
    from_email = os.getenv("FROM_EMAIL", "onboarding@resend.dev")

    payload = {
        "from": from_email,
        "to": to_emails,
        "subject": subject,
        "html": html,
    }
    if reply_to:
        payload["reply_to"] = [reply_to]

    try:
        resend.Emails.send(payload)
        return True
    except Exception as e:
        logger.error("Error sending email to %s: %s", to_emails, e)
        return False
