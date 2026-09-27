"""Per-player session-currency cache: single-document reads with a bounded TTL.

Remote mode only (USE_LOCAL_DATA=false with a mocked Firestore client); local
mode keeps reading the local frame and is also covered in test_session_revocation.
"""
import threading
import time
from unittest.mock import MagicMock

import jwt
import pandas as pd
import pytest
from fastapi import HTTPException

from services import db_service, session_service
from services.session_service import SessionCheckUnavailable, create_token, require_auth


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeDB:
    """Firestore stand-in. players/{id}.get() returns a snapshot and
    players/{id}.update() applies an Increment; the collection itself must
    never be streamed or read whole."""

    def __init__(self, docs):
        self.docs = dict(docs)       # id(str) -> dict | None (missing) | "boom" | other
        self.gets = []
        self.collection_calls = []
        self.get_hook = None         # optional callable run inside every doc get()
        db = self

        class Doc:
            def __init__(self, pid):
                self.pid = pid

            def get(self):
                db.gets.append(self.pid)
                d = db.docs.get(self.pid)
                if isinstance(d, str) and d == "boom":
                    raise RuntimeError("firestore down")
                snap = MagicMock()
                snap.exists = d is not None
                snap.to_dict.return_value = d
                if db.get_hook:
                    db.get_hook()
                return snap

            def set(self, *args, **kwargs):
                pass  # cache-signal docs written by signal_data_update

            def update(self, payload):
                doc = db.docs[self.pid]
                if "token_version" in payload:
                    doc["token_version"] = doc.get("token_version", 0) + 1

        class Coll:
            def __init__(self, name):
                db.collection_calls.append(name)

            def document(self, pid):
                return Doc(pid)

            def stream(self):
                raise AssertionError("collection must not be streamed")

            def get(self):
                raise AssertionError("collection must not be read whole")

        self.collection = Coll


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv("USE_LOCAL_DATA", "false")
    monkeypatch.setattr(session_service, "_lookup_player", session_service._load_player_from_db)
    clock = Clock()
    monkeypatch.setattr(session_service, "_now", clock)
    fake = FakeDB({"1": {"playerId": 1, "token_version": 0}})
    monkeypatch.setattr(db_service, "get_db", lambda: fake)
    monkeypatch.setattr(db_service, "get_collection_df", lambda name, filters=None: pd.DataFrame())
    session_service._reset_session_cache()
    yield fake, clock
    session_service._reset_session_cache()


def _bearer(pid=1, tv=0):
    return f"Bearer {create_token(pid, 'user', tv)}"


def test_miss_reads_one_document_and_hit_reads_none(remote):
    fake, _ = remote
    assert require_auth(authorization=_bearer())["sub"] == "1"
    assert fake.gets == ["1"]
    assert require_auth(authorization=_bearer())["sub"] == "1"
    assert fake.gets == ["1"]
    assert set(fake.collection_calls) == {"players"}


def test_ttl_expiry_refetches(remote):
    fake, clock = remote
    require_auth(authorization=_bearer())
    clock.t += session_service._SESSION_CACHE_TTL_SECONDS - 1
    require_auth(authorization=_bearer())
    assert fake.gets == ["1"]
    clock.t += 2
    require_auth(authorization=_bearer())
    assert fake.gets == ["1", "1"]


def test_bump_write_through_revokes_immediately_and_fresh_token_works(remote):
    fake, _ = remote
    old = _bearer(tv=0)
    require_auth(authorization=old)                       # cached at version 0
    db_service.update_player_credentials("1", "hash")     # same process, no TTL wait
    with pytest.raises(HTTPException) as e:
        require_auth(authorization=old)
    assert e.value.status_code == 401
    assert e.value.headers["X-Session-State"] == "revoked"
    assert require_auth(authorization=_bearer(tv=1))["sub"] == "1"


def test_non_bump_profile_update_keeps_cache_entry(remote):
    fake, _ = remote
    require_auth(authorization=_bearer())
    db_service.update_player_profile("1", {"nickName": "x"})
    require_auth(authorization=_bearer())
    assert fake.gets == ["1"]


def test_invalidate_drops_only_that_player(remote):
    fake, _ = remote
    fake.docs["2"] = {"playerId": 2, "token_version": 0}
    require_auth(authorization=_bearer(1))
    require_auth(authorization=_bearer(2))
    session_service.invalidate_session_cache(1)
    require_auth(authorization=_bearer(1))
    require_auth(authorization=_bearer(2))
    assert fake.gets == ["1", "2", "1"]


def test_inflight_read_started_before_invalidate_is_not_stored(remote):
    """A read that began before a bump must not repopulate the cache after it."""
    fake, _ = remote
    fake.get_hook = lambda: session_service.invalidate_session_cache(1)  # bump lands mid-flight
    require_auth(authorization=_bearer())                 # stale read, must not be cached
    fake.get_hook = None
    fake.docs["1"]["token_version"] = 1
    with pytest.raises(HTTPException):
        require_auth(authorization=_bearer(tv=0))         # refetched, sees version 1
    assert fake.gets == ["1", "1"]


def test_deleted_player_revoked_and_negative_cache_is_bounded(remote):
    fake, clock = remote
    fake.docs["9"] = None
    for _ in range(2):
        with pytest.raises(HTTPException) as e:
            require_auth(authorization=_bearer(9))
        assert e.value.status_code == 401
        assert e.value.headers["X-Session-State"] == "revoked"
    assert fake.gets == ["9"]
    clock.t += session_service._SESSION_CACHE_TTL_SECONDS + 1
    fake.docs["9"] = {"playerId": 9, "token_version": 0}  # re-created
    assert require_auth(authorization=_bearer(9))["sub"] == "9"
    assert fake.gets == ["9", "9"]


def test_exception_is_unavailable_and_not_cached(remote):
    fake, _ = remote
    fake.docs["1"] = "boom"
    with pytest.raises(HTTPException) as e:
        require_auth(authorization=_bearer())
    assert e.value.status_code == 503
    assert "X-Session-State" not in (e.value.headers or {})
    fake.docs["1"] = {"playerId": 1, "token_version": 0}
    assert require_auth(authorization=_bearer())["sub"] == "1"
    assert fake.gets == ["1", "1"]


def test_no_client_is_unavailable(remote, monkeypatch):
    monkeypatch.setattr(db_service, "get_db", lambda: None)
    with pytest.raises(SessionCheckUnavailable):
        session_service._load_player_from_db(1)
    with pytest.raises(HTTPException) as e:
        require_auth(authorization=_bearer())
    assert e.value.status_code == 503


def test_malformed_document_is_unavailable_and_not_cached(remote):
    fake, _ = remote
    fake.docs["1"] = ["not", "a", "dict"]
    with pytest.raises(SessionCheckUnavailable):
        session_service._load_player_from_db(1)
    fake.docs["1"] = {"playerId": 1, "token_version": 0}
    assert session_service._load_player_from_db(1)["token_version"] == 0


def test_legacy_token_without_tv_and_unset_version(remote):
    fake, _ = remote
    legacy = jwt.encode({"sub": "1", "role": "user", "iat": int(time.time()),
                         "exp": int(time.time()) + 600},
                        session_service._get_secret(), algorithm="HS256")
    assert require_auth(authorization=f"Bearer {legacy}")["sub"] == "1"
    session_service._reset_session_cache()
    fake.docs["1"] = {"playerId": 1}                      # field never set
    assert require_auth(authorization=f"Bearer {legacy}")["sub"] == "1"
    session_service._reset_session_cache()
    fake.docs["1"] = {"playerId": 1, "token_version": 2}
    with pytest.raises(HTTPException):
        require_auth(authorization=f"Bearer {legacy}")


def test_concurrent_access_does_not_raise(remote):
    errors = []

    def work():
        try:
            for _ in range(50):
                require_auth(authorization=_bearer())
                session_service.invalidate_session_cache(1)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_local_mode_reads_local_frame_not_firestore(monkeypatch):
    monkeypatch.setenv("USE_LOCAL_DATA", "true")
    monkeypatch.setattr(session_service, "_lookup_player", session_service._load_player_from_db)

    def no_firestore():
        raise AssertionError("no firestore in local mode")
    monkeypatch.setattr(db_service, "get_db", no_firestore)
    df = pd.DataFrame([{"playerId": 4, "token_version": 3}])
    monkeypatch.setattr(db_service, "_get_players_df", lambda: df)
    assert session_service._load_player_from_db(4)["token_version"] == 3
    assert session_service._load_player_from_db(5) is None


# --- token ahead of cache (stale cache), reset epoch ------------------------

def test_token_ahead_of_cache_rereads_and_accepts_when_fresh_matches(remote):
    fake, _ = remote
    fake.docs["1"]["token_version"] = 1
    require_auth(authorization=_bearer(tv=1))              # cache version 1
    fake.docs["1"]["token_version"] = 2                    # bump landed on another instance
    assert require_auth(authorization=_bearer(tv=2))["sub"] == "1"
    assert fake.gets == ["1", "1"]


def test_token_cannot_outrun_stored_version(remote):
    fake, _ = remote
    fake.docs["1"]["token_version"] = 1
    require_auth(authorization=_bearer(tv=1))
    with pytest.raises(HTTPException) as e:
        require_auth(authorization=_bearer(tv=2))          # fresh doc still says 1
    assert e.value.status_code == 401
    assert e.value.headers["X-Session-State"] == "revoked"
    assert fake.gets == ["1", "1"]


def test_token_equal_or_lower_than_cache_behaves_as_before(remote):
    fake, _ = remote
    fake.docs["1"]["token_version"] = 2
    require_auth(authorization=_bearer(tv=2))
    require_auth(authorization=_bearer(tv=2))
    assert fake.gets == ["1"]
    with pytest.raises(HTTPException):
        require_auth(authorization=_bearer(tv=1))          # lower -> revoked, no re-read
    assert fake.gets == ["1"]


def test_reset_advances_epoch_so_inflight_read_is_not_stored(remote):
    fake, _ = remote
    fake.get_hook = session_service._reset_session_cache
    require_auth(authorization=_bearer())
    fake.get_hook = None
    require_auth(authorization=_bearer())
    assert fake.gets == ["1", "1"]
