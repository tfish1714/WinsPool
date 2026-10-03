"""routes/models.py — Pydantic request body schemas for all POST endpoints."""
from typing import Annotated, Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator


# --- Auth ---

# Lenient email shape check (surrounding whitespace is tolerated because the routes
# strip and lowercase). A regex rather than pydantic's EmailStr, which would add the
# email-validator dependency.
EMAIL_PATTERN = r"^\s*[^@\s]+@[^@\s]+\.[^@\s]+\s*$"
# Optional-email variant for profile updates, where "" means "leave unchanged".
OPTIONAL_EMAIL_PATTERN = r"^(\s*|\s*[^@\s]+@[^@\s]+\.[^@\s]+\s*)$"

# Password length/complexity (12+ chars, mixed classes) is deliberately enforced by the
# routes, not by a min_length here: a 422 would bypass their lockout accounting (weak
# set_password attempts count toward the setup lockout, failed logins toward the login
# lockout) and legacy accounts may hold passwords shorter than the current rules, which
# must fail login as 401. Only an upper bound is declared here.
MAX_PASSWORD_LENGTH = 256


class SetPasswordRequest(BaseModel):
    email: str = Field(..., pattern=EMAIL_PATTERN, max_length=254,
                       description="Account email the password is being set for.")
    password: str = Field(..., max_length=MAX_PASSWORD_LENGTH,
                          description="New password (the route enforces length and complexity and counts weak attempts toward lockout).")
    confirm_password: str = Field(..., max_length=MAX_PASSWORD_LENGTH,
                                  description="Must match password.")


class LoginRequest(BaseModel):
    email: str = Field(..., pattern=EMAIL_PATTERN, max_length=254,
                       description="Account email address.")
    password: str = Field(..., max_length=MAX_PASSWORD_LENGTH, description="Account password.")


class UpdateProfileRequest(BaseModel):
    playerId: str = Field(..., description="ID of the player whose profile is updated; must match the session.")
    fullName: str = Field("", max_length=100, description="Display full name; empty leaves it unchanged.")
    nickName: str = Field("", max_length=50, description="Short display name; empty leaves it unchanged.")
    email: str = Field("", pattern=OPTIONAL_EMAIL_PATTERN, max_length=254,
                       description="New email address; empty leaves it unchanged.")
    currentPassword: str = Field(..., max_length=MAX_PASSWORD_LENGTH,
                                 description="Current password, required to authorize any change.")
    newPassword: Optional[str] = Field(None, max_length=MAX_PASSWORD_LENGTH,
                                       description="New password (the route enforces complexity); omit or send empty to keep the current one.")
    mfaEnabled: bool = Field(False, description="Whether email-code MFA is required at login.")



class MfaVerifyRequest(BaseModel):
    playerId: str
    code: str


# --- Admin ---

class NewSeasonRequest(BaseModel):
    season: int = Field(..., ge=2000, le=2100, description="Season year to create.")
    playerIds: List[int] = Field([], description="Players entered in the new season's pool.")


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
    season: int = Field(..., ge=2000, le=2100, description="Season whose projections drive the bot pick.")
    availableTeams: List[str] = Field(..., description="Team abbreviations still undrafted.")
    wildcardsSoFar: int = Field(0, ge=0, description="Wildcard (uniform-random) bot picks made so far.")
    botPicksRemaining: int = Field(1, ge=0, description="Bot picks left in the whole draft, including this one.")


class MockDraftResultsRequest(BaseModel):
    season: int
    rosters: Dict[str, List[str]]


class PushBroadcastRequest(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class PoolPayoutItem(BaseModel):
    place: Union[int, Literal["last"]]
    amount: float = Field(ge=0, le=10_000_000)

    @field_validator("place")
    @classmethod
    def _place_positive(cls, v):
        if isinstance(v, int) and not isinstance(v, bool) and v < 1:
            raise ValueError("place must be >= 1 or 'last'")
        return v


class PoolConfigRequest(BaseModel):
    season: int = Field(ge=2000, le=2100)
    entryFee: float = Field(ge=0, le=1_000_000)
    payouts: List[PoolPayoutItem] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def _no_duplicate_places(self):
        places = [p.place for p in self.payouts]
        if len(set(places)) != len(places):
            raise ValueError("duplicate payout places")
        return self
