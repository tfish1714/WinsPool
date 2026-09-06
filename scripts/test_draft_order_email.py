"""One-off manual check: render (and optionally send) the draft-order
announcement email exactly as send_draft_order_email() builds it, so you can
verify the draft-room link and layout before a real `POST /api/admin/new_season`
triggers it for real. Does not touch Firestore or local pkl data.

Usage:
    python scripts/test_draft_order_email.py                    # render only, print HTML
    python scripts/test_draft_order_email.py --season 2027       # render for a specific season
    python scripts/test_draft_order_email.py --send              # also send to yourself via Resend
    python scripts/test_draft_order_email.py --send --to someone@example.com
    python scripts/test_draft_order_email.py --players-from-season 2025   # use a real season's draft order/players instead of placeholders
"""
import argparse
import html
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.email_service import _app_base_url  # noqa: E402

# Resend's sandbox mode (no verified domain) only delivers to the exact
# address registered on the account -- see docs/... memory project_resend_email.
# No hardcoded address here (keeps this file free of personal info) -- pass
# --to explicitly, or set ALERT_EMAIL in .env like the rest of the app does.


def _load_players_from_season(season: int) -> list[dict]:
    """Real draft order + player names for `season`, read from local data
    (read-only -- does not touch Firestore or local pkl data)."""
    from services.data_service import load_data
    from services.db_service import get_collection_df

    order_df = get_collection_df("draft_order")
    order_df = order_df[order_df["season"] == season].sort_values("draftOrder")
    if order_df.empty:
        raise ValueError(f"No draft_order rows found for season {season}.")

    _, _, _, players_df, _, _, _ = load_data()
    ordered = []
    for _, row in order_df.iterrows():
        match = players_df[players_df["playerId"].astype(int) == int(row["playerId"])]
        name = str(match.iloc[0]["fullName"]) if not match.empty else f"Player {row['playerId']}"
        ordered.append({"position": int(row["draftOrder"]), "name": name})
    return ordered


def render(season: int, ordered_players: list[dict] | None = None) -> tuple[str, str]:
    """Build (subject, html_body) exactly as send_draft_order_email() does."""
    if ordered_players is None:
        ordered_players = [
            {"position": 1, "name": "Test Player One"},
            {"position": 2, "name": "Test Player Two"},
            {"position": 3, "name": "Test Player Three"},
        ]
    rows = "".join(
        f"<li>Pick {p['position']}: {html.escape(p['name'])}</li>"
        for p in ordered_players
    )
    draft_room_url = f"{_app_base_url()}/draft?season={season}"
    body = f"""
    <p>The draft order for the {season} Wins Pool season has been set:</p>
    <ol>{rows}</ol>
    <p><a href="{draft_room_url}">Go to the draft room</a></p>
    """
    return f"{season} Wins Pool Draft Order", body


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=2099, help="Season to render (default: 2099)")
    parser.add_argument("--send", action="store_true", help="Actually send via Resend, not just render")
    parser.add_argument(
        "--to", default=None,
        help="Address to send the test email to when --send is passed (default: $ALERT_EMAIL from .env)",
    )
    parser.add_argument(
        "--players-from-season", type=int, default=None,
        help="Use the real draft order/players from this season instead of placeholder names",
    )
    args = parser.parse_args()
    if args.send:
        args.to = args.to or os.getenv("ALERT_EMAIL")
        if not args.to:
            print("No recipient: pass --to someone@example.com, or set ALERT_EMAIL in .env.")
            return

    ordered_players = None
    if args.players_from_season is not None:
        ordered_players = _load_players_from_season(args.players_from_season)

    subject, body = render(args.season, ordered_players)
    print(f"APP_BASE_URL resolves to: {_app_base_url()}")
    print(f"Subject: [TEST] {subject}")
    print("--- HTML body ---")
    print(body)

    if not args.send:
        print("\n(dry run — pass --send to actually deliver via Resend)")
        return

    import resend

    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        print("RESEND_API_KEY not set — aborting send.")
        return
    resend.api_key = api_key

    from_email = os.getenv("FROM_EMAIL", "onboarding@resend.dev")
    reply_to = os.getenv("ALERT_EMAIL") or args.to
    test_subject = f"[TEST] {subject}"
    debug_block = f"""
    <hr>
    <h3>TEST SEND — envelope</h3>
    <ul>
        <li><b>To:</b> {html.escape(args.to)}</li>
        <li><b>From:</b> {html.escape(from_email)}</li>
        <li><b>Reply-To:</b> {html.escape(reply_to)}</li>
        <li><b>Subject:</b> {html.escape(test_subject)}</li>
    </ul>
    <p><b>Body (this is what real recipients would see, below):</b></p>
    {body}
    """

    payload = {
        "from": from_email,
        "to": [args.to],
        "subject": test_subject,
        "html": debug_block,
        "reply_to": [reply_to],
    }

    try:
        resend.Emails.send(payload)
        print("\nSent.")
    except Exception as e:
        print(f"\nFAILED to send: {e}")
        return

    print(f"  to={args.to}")
    print(f"  from={from_email}")
    print(f"  reply_to={reply_to}")
    print(f"  subject={test_subject}")


if __name__ == "__main__":
    main()
