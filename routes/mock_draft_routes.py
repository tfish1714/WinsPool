"""routes/mock_draft_routes.py — Solo mock draft: setup, bot picks, end-of-draft ranking.

Fully stateless and unauthenticated -- no session, no DB writes. Team win
projections are only ever included in a response when the requester's
session resolves to an admin (services.session_service.get_is_admin);
everyone else gets picks/ranks with the underlying numbers never
serialized, not merely hidden client-side.
"""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from routes.models import MockDraftPickRequest, MockDraftResultsRequest
from services.data_service import get_season_projection_legacy_shape
from services.mock_draft_service import (
    NFL_TEAMS, bot_pick, get_pick_sequence, get_projection_season, rank_rosters,
)
from services.response_helpers import error_response, server_error
from services.session_service import get_is_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mock-draft")

page_router = APIRouter()
templates = Jinja2Templates(directory="templates")


@page_router.get("/mock-draft", include_in_schema=False)
async def serve_mock_draft(request: Request):
    return templates.TemplateResponse(request, "mock_draft.html", {})


@router.get("/setup")
async def mock_draft_setup(is_admin: bool = Depends(get_is_admin)):
    try:
        pick_sequence = get_pick_sequence()
        season = get_projection_season()
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception:
        logger.exception("Unhandled error building mock draft setup")
        return server_error("Failed to build mock draft setup.")

    content = {"pickSequence": pick_sequence, "teams": NFL_TEAMS, "season": season}
    if is_admin:
        content["projections"] = get_season_projection_legacy_shape(season)
    return content


@router.post("/pick")
async def mock_draft_pick(body: MockDraftPickRequest):
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
async def mock_draft_results(body: MockDraftResultsRequest, is_admin: bool = Depends(get_is_admin)):
    try:
        rankings = rank_rosters(body.season, body.rosters)
    except Exception:
        logger.exception("Unhandled error ranking mock draft rosters")
        return server_error("Failed to rank rosters.")

    if not is_admin:
        rankings = [{"slot": r["slot"], "rank": r["rank"]} for r in rankings]
    return {"rankings": rankings}
