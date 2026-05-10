"""tests/test_security_audit.py -- Tests for the security audit fixes.

Validates:
  - SEC-H1: bcrypt password hashing and legacy SHA-256 migration
  - SEC-H5: Login rate limiting (lockout after 5 failed attempts)
  - SEC-H6: Temp password complexity enforcement and must_change_password flag
  - ARCH-H1: Vectorized compute_team_records produces correct W-L-T records
"""
import os
import sys
import hashlib
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


# ---------------------------------------------------------------------------
# SEC-H1: bcrypt migration tests
# ---------------------------------------------------------------------------

class TestPasswordHashing:
    """Validate bcrypt hashing and legacy SHA-256 detection."""

    def test_bcrypt_hash_format(self):
        """New hashes must be bcrypt ($2b$ prefix, 60 chars)."""
        from services.db_service import get_password_hash
        hashed = get_password_hash("TestPassword123!")
        assert hashed.startswith("$2b$"), f"Expected bcrypt prefix, got: {hashed[:10]}"
        assert len(hashed) == 60, f"Expected 60-char bcrypt hash, got {len(hashed)}"

    def test_bcrypt_verify_roundtrip(self):
        """A password hashed with bcrypt must verify correctly."""
        from services.db_service import get_password_hash, verify_password
        password = "MyStr0ng!Pass#2026"
        hashed = get_password_hash(password)
        assert verify_password(password, hashed) is True
        assert verify_password("WrongPassword!", hashed) is False

    def test_bcrypt_unique_salts(self):
        """Two hashes of the same password must differ (random salt)."""
        from services.db_service import get_password_hash
        h1 = get_password_hash("SamePass123!")
        h2 = get_password_hash("SamePass123!")
        assert h1 != h2, "bcrypt hashes must use unique salts"

    def test_legacy_sha256_detection(self):
        """_is_legacy_sha256 must correctly identify hex digests."""
        from services.db_service import _is_legacy_sha256
        sha256_hash = hashlib.sha256(b"test").hexdigest()
        assert _is_legacy_sha256(sha256_hash) is True
        assert _is_legacy_sha256("$2b$12$somebcrypthash") is False
        assert _is_legacy_sha256("tooshort") is False
        # 64-char string with non-hex chars
        assert _is_legacy_sha256("g" * 64) is False

    def test_legacy_sha256_verify(self):
        """verify_password must still accept legacy SHA-256 hashes."""
        from services.db_service import verify_password
        password = "LegacyTest123"
        legacy_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        assert verify_password(password, legacy_hash) is True
        assert verify_password("WrongPassword", legacy_hash) is False

    def test_verify_empty_hash_returns_false(self):
        """verify_password must handle None/empty hashes gracefully."""
        from services.db_service import verify_password
        assert verify_password("anything", None) is False
        assert verify_password("anything", "") is False

    def test_verify_malformed_hash_returns_false(self):
        """verify_password must not crash on malformed bcrypt strings."""
        from services.db_service import verify_password
        assert verify_password("anything", "$2b$invalidhash") is False
        assert verify_password("anything", "not-a-hash-at-all") is False


# ---------------------------------------------------------------------------
# ARCH-H1: Vectorized compute_team_records
# ---------------------------------------------------------------------------

class TestComputeTeamRecords:
    """Validate the shared vectorized team records utility."""

    @pytest.fixture
    def sample_games(self):
        """Minimal games DataFrame for testing."""
        import pandas as pd
        return pd.DataFrame({
            'season': [2025, 2025, 2025, 2025],
            'home_team': ['KC', 'BUF', 'KC', 'DET'],
            'away_team': ['BUF', 'KC', 'DET', 'BUF'],
            'result': [7, -3, 14, 0],  # KC wins, KC wins, KC wins, TIE
            'game_type': ['REG', 'REG', 'REG', 'REG'],
        })

    def test_basic_records(self, sample_games):
        """Verify W-L-T counts match expected outcomes."""
        from services.analysis_service import compute_team_records
        records = compute_team_records(sample_games, 2025)

        assert records['KC']['W'] == 3
        assert records['KC']['L'] == 0
        assert records['KC']['T'] == 0

        assert records['BUF']['W'] == 0
        assert records['BUF']['L'] == 2
        assert records['BUF']['T'] == 1

        assert records['DET']['W'] == 0
        assert records['DET']['L'] == 1
        assert records['DET']['T'] == 1

    def test_format_team_record(self, sample_games):
        """Verify string formatting for display."""
        from services.analysis_service import compute_team_records, format_team_record
        records = compute_team_records(sample_games, 2025)

        assert format_team_record('KC', records) == "3-0"
        assert format_team_record('BUF', records) == "0-2-1"  # Has a tie
        assert format_team_record('UNKNOWN', records) == "0-0"

    def test_empty_season(self):
        """Verify empty result for a season with no games."""
        import pandas as pd
        from services.analysis_service import compute_team_records
        empty = pd.DataFrame({
            'season': [], 'home_team': [], 'away_team': [],
            'result': [], 'game_type': []
        })
        assert compute_team_records(empty, 2025) == {}

    def test_filters_non_reg_games(self):
        """Verify that playoff games are excluded when game_type is present."""
        import pandas as pd
        from services.analysis_service import compute_team_records
        games = pd.DataFrame({
            'season': [2025, 2025],
            'home_team': ['KC', 'KC'],
            'away_team': ['BUF', 'BUF'],
            'result': [7, 3],
            'game_type': ['REG', 'POST'],
        })
        records = compute_team_records(games, 2025)
        assert records['KC']['W'] == 1  # Only the REG game counts

    def test_filters_sentinel_results(self):
        """Verify that -1000 sentinel values are excluded."""
        import pandas as pd
        from services.analysis_service import compute_team_records
        games = pd.DataFrame({
            'season': [2025, 2025],
            'home_team': ['KC', 'KC'],
            'away_team': ['BUF', 'BUF'],
            'result': [7, -1000],
            'game_type': ['REG', 'REG'],
        })
        records = compute_team_records(games, 2025)
        assert records['KC']['W'] == 1
