from pydantic import BaseModel, Field
from datetime import datetime
# fastapi pydantic에서 json 요청을 자동으로 파싱
# api 입출력 형식 정의

class SignupRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False


class AccountVerifyRequest(BaseModel):
    password: str


class AccountUpdateRequest(BaseModel):
    display_name: str | None = None
    username: str | None = None
    password: str | None = None


class AccountResponse(BaseModel):
    username: str
    display_name: str | None = None
    access_token: str | None = None

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CorrectRequest(BaseModel):
    text: str


class CorrectionIssue(BaseModel):
    id: str = ""
    original: str = ""
    suggestion: str = ""
    category: str = ""
    explanation: str = ""
    confidence: str = ""
    severity: str = ""
    source_start: int | None = None
    source_end: int | None = None
    anchor_text: str = ""
    display_title: str = ""


class CorrectResponse(BaseModel):
    corrected_text: str
    spelling_feedback: str | None = None
    corrections: list[CorrectionIssue] = Field(default_factory=list)


class SummaryRequest(BaseModel):
    text: str
    style: str = "brief"


class SummaryResponse(BaseModel):
    summary: str


class EvaluationRequest(BaseModel):
    text: str


class EvaluationResponse(BaseModel):
    score: int
    feedback: str


class TitleRequest(BaseModel):
    text: str


class TitleResponse(BaseModel):
    title: str


class ToneRequest(BaseModel):
    text: str
    tone: str = ""


class ToneResponse(BaseModel):
    converted_text: str
    feedback: str | None = None


class UsageLogCreateRequest(BaseModel):
    feature_type: int
    feature_label: str | None = None
    input_text: str
    request_id: int | None = None
    output_text: str = ""
    title: str | None = None
    score: int | None = None
    tone: str | None = None
    spelling_feedback: str | None = None
    evaluation_reason: str | None = None


class UsageLogResponse(BaseModel):
    id: int
    feature_type: int
    feature_label: str | None = None
    input_text: str
    request_id: int | None = None
    output_text: str
    title: str | None = None
    score: int | None = None
    tone: str | None = None
    spelling_feedback: str | None = None
    evaluation_reason: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class UserSettingsRequest(BaseModel):
    default_dark_mode: bool = False
    history_enabled: bool = False
    input_mode: str = "clipboard"
    replace_mode: bool = False
    spell_scope: str = "current_sentence"


class UserSettingsResponse(UserSettingsRequest):
    has_settings: bool = True
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class HistoryRequestResponse(BaseModel):
    request_id: int
    input_text: str
    created_at: datetime
    spelling: dict | None = None
    summary: dict | None = None
    tone: dict | None = None
    evaluation: dict | None = None
    title: dict | None = None


class ToneFavoriteCreateRequest(BaseModel):
    tone: str


class ToneFavoriteResponse(BaseModel):
    id: int
    tone: str
    created_at: datetime

    class Config:
        from_attributes = True
