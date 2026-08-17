"""routes/mock_draft_routes.py — Solo mock draft: setup, bot picks, end-of-draft ranking.

Fully stateless and unauthenticated -- no session, no DB writes. Team win
projections are only ever included in a response when the requester's
session resolves to an admin (services.session_service.get_is_admin);
everyone else gets picks/ranks with the underlying numbers never
serialized, not merely hidden client-side.

The whole feature is gated behind the mock_draft_active config flag (same
config/settings doc draft_active lives on) so it can be turned off once the
real draft starts -- admins bypass the gate so they can preview/test it
before flipping it on. Also rate-limited per client IP, in-memory, since
this is a fully public, login-free surface.
"""
import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from routes.models import MockDraftPickRequest, MockDraftResultsRequest
from services.data_service import get_season_projection_dual, get_season_projection_legacy_shape
from services.db_service import get_config_settings
from services.mock_draft_service import (
    NFL_TEAMS, bot_pick, get_pick_sequence, get_projection_season, get_team_schedules, rank_rosters,
)
from services.response_helpers import error_response, server_error
from services.session_service import get_is_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mock-draft")

page_router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Rate limiting: in-memory, per-process, fixed-window per client IP. Same
# tradeoff draft_routes.py's connected_players/ConnectionManager already
# accepts (resets on restart, not shared across instances) -- fine here too,
# this is abuse protection, not a correctness guarantee. One shared bucket
# across all three endpoints per IP: a real playthrough makes ~29 calls
# (1 setup + up to 27 bot picks + 1 results), so 40/min leaves headroom for
# a restart or two without allowing scripted hammering.
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_REQUESTS = 40
_rate_limit_buckets: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(request: Request) -> bool:
    """True if this client has exceeded the mock-draft rate limit.

    Records the request (bucket append) as a side effect when NOT limited,
    so this must be called at most once per handled request.
    """
    ip = _client_ip(request)
    now = time.time()
    cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
    bucket = [t for t in _rate_limit_buckets.get(ip, []) if t >= cutoff]
    limited = len(bucket) >= _RATE_LIMIT_MAX_REQUESTS
    if not limited:
        bucket.append(now)
    if bucket:
        _rate_limit_buckets[ip] = bucket
    else:
        _rate_limit_buckets.pop(ip, None)
    return limited


def _mock_draft_active(is_admin: bool) -> bool:
    """Admins always pass (so they can preview/test before flipping it on
    for everyone else); everyone else needs the config flag on."""
    return is_admin or get_config_settings().get("mock_draft_active") is True


@page_router.get("/mock-draft", include_in_schema=False)
async def serve_mock_draft(request: Request, is_admin: bool = Depends(get_is_admin)):
    active = _mock_draft_active(is_admin)
    return templates.TemplateResponse(request, "mock_draft.html", {"active": active})


@router.get("/setup")
async def mock_draft_setup(request: Request, is_admin: bool = Depends(get_is_admin)):
    if not _mock_draft_active(is_admin):
        return error_response("Mock draft is not currently available.", 403)
    if _rate_limited(request):
        return error_response("Too many requests — please slow down.", 429)

    try:
        pick_sequence = get_pick_sequence()
        season = get_projection_season()
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception:
        logger.exception("Unhandled error building mock draft setup")
        return server_error("Failed to build mock draft setup.")

    content = {
        "pickSequence": pick_sequence,
        "teams": NFL_TEAMS,
        "season": season,
        "teamSchedules": get_team_schedules(season),
    }
    if is_admin:
        content["projections"] = get_season_projection_legacy_shape(season)
        content["projectionsDetail"] = get_season_projection_dual(season)
    return content


@router.post("/pick")
async def mock_draft_pick(body: MockDraftPickRequest, request: Request, is_admin: bool = Depends(get_is_admin)):
    if not _mock_draft_active(is_admin):
        return error_response("Mock draft is not currently available.", 403)
    if _rate_limited(request):
        return error_response("Too many requests — please slow down.", 429)
    if not body.availableTeams:
        return error_response("availableTeams must not be empty.", 400)
    try:
        team, was_wildcard = bot_pick(
            body.season, body.availableTeams, body.wildcardsSoFar, body.botPicksRemaining
        )
        return {"team": team, "wasWildcard": was_wildcard}
    except Exception:
        logger.exception("Unhandled error computing mock draft bot pick")
        return server_error("Failed to compute bot pick.")


@router.post("/results")
async def mock_draft_results(body: MockDraftResultsRequest, request: Request, is_admin: bool = Depends(get_is_admin)):
    if not _mock_draft_active(is_admin):
        return error_response("Mock draft is not currently available.", 403)
    if _rate_limited(request):
        return error_response("Too many requests — please slow down.", 429)

    try:
        rankings = rank_rosters(body.season, body.rosters)
    except Exception:
        logger.exception("Unhandled error ranking mock draft rosters")
        return server_error("Failed to rank rosters.")

    if not is_admin:
        rankings = [{"slot": r["slot"], "rank": r["rank"], "graded": r["graded"]} for r in rankings]
    return {"rankings": rankings}
