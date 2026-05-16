"""routes/models.py — Pydantic request body schemas for all POST endpoints."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


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
    targetPlayerId: str
    fields: Dict[str, Any] = {}


class TargetPlayerRequest(BaseModel):
    targetPlayerId: str


class SetTempPasswordRequest(BaseModel):
    targetPlayerId: str
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

class PredictionConfigRequest(BaseModel):
    elo_weight: float = 0.7
    simulations: int = 1000
