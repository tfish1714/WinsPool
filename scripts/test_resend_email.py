"""scripts/test_resend_email.py — Send a test email via Resend.

Usage:
    python scripts/test_resend_email.py

Requires:
    RESEND_API_KEY env var (or set it in .env)

Notes:
    - From address uses onboarding@resend.dev (Resend's shared test sender).
    - Once a custom domain is verified in Resend (e.g. winspool.app),
      change FROM_EMAIL to noreply@winspool.app.
"""

import os
import sys
from dotenv import load_dotenv
import resend

load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
TO_EMAIL = "fischerthomasg@gmail.com"
FROM_EMAIL = "onboarding@resend.dev"

if not RESEND_API_KEY:
    print("ERROR: RESEND_API_KEY not set. Add it to .env or export it.")
    sys.exit(1)

resend.api_key = RESEND_API_KEY

params: resend.Emails.SendParams = {
    "from": FROM_EMAIL,
    "to": [TO_EMAIL],
    "subject": "WinsPool — Resend test email",
    "html": """
        <p>Hello from <strong>WinsPool</strong>!</p>
        <p>If you're reading this, Resend is wired up correctly.</p>
    """,
}

response = resend.Emails.send(params)
print(f"Sent! Email ID: {response['id']}")
