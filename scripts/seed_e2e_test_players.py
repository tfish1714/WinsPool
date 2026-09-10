"""scripts/seed_e2e_test_players.py — One-time setup: create the 10 dedicated
Firestore-backed test player accounts used by the tests_e2e/ Playwright suite.

Run once against production Firestore. Safe to re-run — skips any test
account whose email already exists. Prints the generated password once;
copy it into .env as E2E_TEST_PLAYER_PASSWORD (all 10 accounts share one
password — they're fixture accounts, not real credentials to protect
individually).

Usage: python scripts/seed_e2e_test_players.py
"""
import os
import secrets
import sys

os.environ["USE_LOCAL_DATA"] = "False"

from services.db_service import (
    add_player, get_password_hash, get_player_by_email,
    update_player_credentials, update_player_profile,
)

NUM_TEST_PLAYERS = 10
EMAIL_DOMAIN = "winspool.internal"


def main():
    # First pass: scan all 10 accounts to detect partial-failure reruns.
    # If some exist and some don't, we must refuse to proceed with a new
    # password to maintain the invariant that all 10 accounts share one.
    existing_ids = []
    missing_indices = []

    for i in range(1, NUM_TEST_PLAYERS + 1):
        email = f"e2e-test-{i:02d}@{EMAIL_DOMAIN}"
        existing = get_player_by_email(email)
        if existing:
            existing_ids.append(int(existing["playerId"]))
        else:
            missing_indices.append(i)

    # Partial-failure case: some exist, some don't.
    # Refuse to proceed — new accounts would get a different password than existing ones.
    if existing_ids and missing_indices:
        print(
            "ERROR: Partial failure detected. Some test accounts exist, but not all:\n"
            f"  Existing: {len(existing_ids)} accounts (IDs: {existing_ids})\n"
            f"  Missing: {len(missing_indices)} accounts (indices: {missing_indices})\n\n"
            "To maintain password consistency across all 10 accounts, you must:\n"
            "  Option A: Manually create the missing accounts with the SAME password\n"
            "           as the existing ones (from .env's E2E_TEST_PLAYER_PASSWORD)\n"
            "  Option B: Delete all 10 test accounts and re-run this script from scratch\n"
        )
        sys.exit(1)

    # All accounts already exist: idempotent skip.
    if existing_ids and not missing_indices:
        print(f"All {NUM_TEST_PLAYERS} test accounts already exist. Nothing to do.")
        return

    # Fresh run: generate password and create all 10 accounts.
    password = secrets.token_urlsafe(16)
    password_hash = get_password_hash(password)
    created_ids = []

    for i in range(1, NUM_TEST_PLAYERS + 1):
        email = f"e2e-test-{i:02d}@{EMAIL_DOMAIN}"
        role = "admin" if i == 1 else "user"
        player_id = add_player(
            full_name=f"E2E Test Player {i:02d}",
            nick_name=f"E2E{i:02d}",
            email=email,
        )
        update_player_credentials(str(player_id), password_hash)
        update_player_profile(str(player_id), {"is_test_account": True, "role": role})
        created_ids.append(player_id)
        print(f"Created {email} — playerId={player_id}, role={role}")

    print(f"\nDone. Player IDs: {created_ids}")
    print(f"Shared password (save to .env as E2E_TEST_PLAYER_PASSWORD): {password}")
    print("Run `python scripts/refresh_local_pkls.py` to pull these into .local_db/.")


if __name__ == "__main__":
    main()
