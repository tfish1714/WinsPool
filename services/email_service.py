import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

def send_mfa_code_email(to_email: str, code: str) -> bool:
    """Send a 6-digit MFA verification code to a single recipient."""
    html_content = f"""
    <p>Your WinsPool verification code is:</p>
    <h2 style="letter-spacing:4px;">{code}</h2>
    <p>This code expires in 10 minutes. Do not share it with anyone.</p>
    """
    return send_weekly_recap_email([to_email], "WinsPool Login Verification Code", html_content)


def send_weekly_recap_email(to_emails: list, subject: str, html_content: str):
    """
    Sends a weekly recap email to a list of recipients using SMTP.
    """
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("FROM_EMAIL", smtp_user)

    if not all([smtp_server, smtp_user, smtp_password]):
        logger.error("SMTP configuration missing in environment.")
        return False

    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['Subject'] = subject

        # Attach HTML content
        msg.attach(MIMEText(html_content, 'html'))

        # Connect to server and send
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()  # Secure the connection
            server.login(smtp_user, smtp_password)
            
            # Send to each recipient individually to avoid BCC issues or leaking lists
            for recipient in to_emails:
                msg['To'] = recipient
                server.send_message(msg)
                # Reset 'To' header for next recipient
                del msg['To']
                
        return True
    except Exception as e:
        logger.error("Error sending email: %s", e)
        return False
