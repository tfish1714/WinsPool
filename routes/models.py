"""routes/models.py — Pydantic request body schemas for all POST endpoints."""
from typing import Annotated, Any, Dict, List, Optional
from pydantic import BaseModel, Field, StringConstraints, model_validator


# --- Auth ---

class SetPasswordRequest(BaseModel):
    email: str
    password: str
    confirm_password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class UpdateProfileRequest(BaseModel):
    playerId: str
    fullName: str = ""
    nickName: str = ""
    email: str = ""
    currentPassword: str
    newPassword: Optional[str] = None
    mfaEnabled: bool = False


class MfaVerifyRequest(BaseModel):
    playerId: str
    code: str


# --- Admin ---

class NewSeasonRequest(BaseModel):
    season: int
    playerIds: List[int] = []


class MemberPaidRequest(BaseModel):
    targetPlayerId: int
    season: int
    paid: bool


class PreviewDraftOrderRequest(BaseModel):
    playerIds: List[int] = []


class CreatePlayerRequest(BaseModel):
    fullName: str
    nickName: str
    email: str
    phone: str = ""


class UpdatePlayerRequest(BaseModel):
    targetPlayerId: int
    fields: Dict[str, Any] = {}


class TargetPlayerRequest(BaseModel):
    targetPlayerId: int


class SetTempPasswordRequest(BaseModel):
    targetPlayerId: int
    tempPassword: str


class SeasonRequest(BaseModel):
    season: int


class RecapWeekRequest(BaseModel):
    year: int
    week: int


class RecapYearRequest(BaseModel):
    year: int


class GenerateRecapRequest(BaseModel):
    prompt_data: str


class SaveBroadcastRecapRequest(BaseModel):
    year: int
    week: int
    summary: str


# --- Predictions ---

# --- Mock Draft ---

class MockDraftPickRequest(BaseModel):
    season: int
    availableTeams: List[str]
    wildcardsSoFar: int = 0
    botPicksRemaining: int = 1


class MockDraftResultsRequest(BaseModel):
    season: int
    rosters: Dict[str, List[str]]


class PushBroadcastRequest(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class PoolPayoutItem(BaseModel):
    place: int = Field(ge=1)
    pct: float = Field(ge=0, le=100)


class PoolConfigRequest(BaseModel):
    entryFee: float = Field(ge=0, le=1_000_000)
    payouts: List[PoolPayoutItem] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _payouts_total(self):
        if sum(p.pct for p in self.payouts) > 100.0001:
            raise ValueError("payout percentages must not exceed 100 in total")
        return self
