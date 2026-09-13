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
import pathlib
import secrets
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

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
    existing_password_hash = None

    for i in range(1, NUM_TEST_PLAYERS + 1):
        email = f"e2e-test-{i:02d}@{EMAIL_DOMAIN}"
        existing = get_player_by_email(email)
        if existing:
            existing_ids.append(int(existing["playerId"]))
            if existing_password_hash is None:
                existing_password_hash = existing.get("password_hash")
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

    # All accounts already exist: idempotent skip for the base 10 — but the
    # lifecycle accounts below still need to be checked/created, so we must
    # NOT return here. A prior version of this script returned early in this
    # branch, which meant that on any real re-run (the base 10 almost always
    # already exist in practice) the lifecycle-accounts block below was never
    # reached at all.
    if existing_ids and not missing_indices:
        print(f"All {NUM_TEST_PLAYERS} test accounts already exist. Nothing to do for the base accounts.")
        if not existing_password_hash:
            print(
                "ERROR: Base accounts exist but none has a password_hash set, so there's "
                "no password to share with the new lifecycle accounts.\n"
                "Fix the base accounts' credentials first (see .env's E2E_TEST_PLAYER_PASSWORD)."
            )
            sys.exit(1)
        password = None
        password_hash = existing_password_hash
        created_ids = existing_ids
    else:
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

    # Dedicated single-purpose accounts for tests_e2e/test_account_claim.py,
    # test_mfa.py, test_lockout.py, and test_forced_password_change.py.
    # Each owns exactly one lifecycle state so those tests never depend on
    # execution order or share mutable state with each other or with the
    # 10 drafting accounts above. This block runs regardless of which branch
    # above ran (fresh / all-10-already-exist) so it's never skipped by the
    # base-10 idempotent-skip path.
    lifecycle_accounts = [
        ("e2e-test-11-claim", "E2E Claim Test", False),   # no password_hash: claim-flow target
        ("e2e-test-12-mfa", "E2E MFA Test", True),         # gets a real password below
        ("e2e-test-13-lockout", "E2E Lockout Test", True),
        ("e2e-test-14-tempword", "E2E Tempword Test", True),
    ]
    lifecycle_ids = {}

    for local_part, full_name, needs_password in lifecycle_accounts:
        email = f"{local_part}@{EMAIL_DOMAIN}"
        existing = get_player_by_email(email)
        if existing:
            print(f"Skipping {email} — already exists (playerId={existing['playerId']})")
            lifecycle_ids[local_part] = int(existing["playerId"])
            continue

        player_id = add_player(full_name=full_name, nick_name=local_part, email=email)
        update_player_profile(str(player_id), {"is_test_account": True, "role": "user"})
        if needs_password:
            update_player_credentials(str(player_id), password_hash)
        lifecycle_ids[local_part] = player_id
        print(f"Created {email} — playerId={player_id}, has_password={needs_password}")

    # e2e-test-12-mfa additionally needs mfa_enabled=True.
    update_player_profile(str(lifecycle_ids["e2e-test-12-mfa"]), {"mfa_enabled": True})

    print(f"\nLifecycle account IDs: {lifecycle_ids}")
    print(f"\nDone. Player IDs: {created_ids}")
    if password is not None:
        print(f"Shared password (save to .env as E2E_TEST_PLAYER_PASSWORD): {password}")
    else:
        print(
            "Base 10 accounts already existed; reused their existing password_hash for "
            "the lifecycle accounts. No new password was generated — .env's "
            "E2E_TEST_PLAYER_PASSWORD should already hold the correct shared password."
        )
    print("Run `python scripts/refresh_local_pkls.py` to pull these into .local_db/.")


if __name__ == "__main__":
    main()
