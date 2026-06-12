import os
from datetime import datetime, timezone
from sqlalchemy import inspect, text

from fastapi import FastAPI, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from database import SessionLocal, engine, Base
from models import (
    User,
    UsageLog,
    UserSetting,
    ToneFavorite,
    AnalysisRequest,
    SpellingResult,
    SummaryResult,
    ToneResult,
    EvaluationResult,
    TitleResult,
)
from schemas import (
    SignupRequest,
    LoginRequest,
    CorrectResponse,
    TokenResponse,
    CorrectRequest,
    SummaryRequest,
    SummaryResponse,
    EvaluationRequest,
    EvaluationResponse,
    TitleRequest,
    TitleResponse,
    ToneRequest,
    ToneResponse,
    UsageLogCreateRequest,
    UsageLogResponse,
    HistoryRequestResponse,
    UserSettingsRequest,
    UserSettingsResponse,
    ToneFavoriteCreateRequest,
    ToneFavoriteResponse,
    AccountVerifyRequest,
    AccountUpdateRequest,
    AccountResponse,
)
from auth import hash_password, verify_password, create_access_token, create_remember_access_token, decode_access_token
from ai_service import AIService

Base.metadata.create_all(bind=engine)


def ensure_usage_log_columns():
    inspector = inspect(engine)
    column_names = {column["name"] for column in inspector.get_columns("usage_logs")}
    statements = []
    if "feature_type" not in column_names:
        statements.append("ALTER TABLE usage_logs ADD COLUMN feature_type INTEGER NOT NULL DEFAULT 2")
    if "feature_label" not in column_names:
        statements.append("ALTER TABLE usage_logs ADD COLUMN feature_label VARCHAR(50)")
    if "title" not in column_names:
        statements.append("ALTER TABLE usage_logs ADD COLUMN title VARCHAR(255)")
    if "score" not in column_names:
        statements.append("ALTER TABLE usage_logs ADD COLUMN score INTEGER")
    if "tone" not in column_names:
        statements.append("ALTER TABLE usage_logs ADD COLUMN tone VARCHAR(100)")
    if "spelling_feedback" not in column_names:
        statements.append("ALTER TABLE usage_logs ADD COLUMN spelling_feedback TEXT")
    if "evaluation_reason" not in column_names:
        statements.append("ALTER TABLE usage_logs ADD COLUMN evaluation_reason TEXT")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


ensure_usage_log_columns()


def ensure_user_columns():
    inspector = inspect(engine)
    column_names = {column["name"] for column in inspector.get_columns("users")}
    statements = []
    if "display_name" not in column_names:
        statements.append("ALTER TABLE users ADD COLUMN display_name VARCHAR(100)")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


ensure_user_columns()


def ensure_user_setting_columns():
    inspector = inspect(engine)
    column_names = {column["name"] for column in inspector.get_columns("user_settings")}
    statements = []
    if "spell_scope" not in column_names:
        statements.append("ALTER TABLE user_settings ADD COLUMN spell_scope VARCHAR(30) NOT NULL DEFAULT 'current_sentence'")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


ensure_user_setting_columns()

app = FastAPI(title="AI 문서 보조 서버") # 서버본체
ai_service = AIService()

FEATURE_LABELS = {
    1: "\ud14d\uc2a4\ud2b8 \uae30\ub85d",
    2: "\uad50\uc815 \uae30\ub85d",
    3: "\uc694\uc57d \uae30\ub85d",
    4: "\ubb38\uccb4 \ubcc0\uacbd \uae30\ub85d",
}

def feature_label_for(feature_type):
    return FEATURE_LABELS.get(int(feature_type or 0), "\uae30\ub85d")


def get_or_create_request(db: Session, user_id: int, input_text: str) -> AnalysisRequest:
    normalized = str(input_text or "").strip()
    request_row = (
        db.query(AnalysisRequest)
        .filter(AnalysisRequest.user_id == user_id, AnalysisRequest.input_text == normalized)
        .order_by(AnalysisRequest.created_at.desc())
        .first()
    )
    if request_row is not None:
        return request_row
    request_row = AnalysisRequest(user_id=user_id, input_text=normalized)
    db.add(request_row)
    db.flush()
    return request_row


def encode_log_id(feature_type: int, row_id: int) -> int:
    return int(feature_type) * 1_000_000 + int(row_id)


def decode_log_id(log_id: int) -> tuple[int | None, int]:
    value = int(log_id)
    feature_type = value // 1_000_000
    row_id = value % 1_000_000
    if feature_type in (1, 2, 3, 4, 5) and row_id > 0:
        return feature_type, row_id
    return None, value


def serialize_spelling_result(row: SpellingResult) -> UsageLogResponse:
    return UsageLogResponse(
        id=encode_log_id(2, row.id),
        feature_type=2,
        feature_label=feature_label_for(2),
        input_text=row.request.input_text if row.request else "",
        request_id=row.request_id,
        output_text=row.corrected_text or "",
        spelling_feedback=row.spelling_feedback,
        created_at=row.created_at,
    )


def serialize_summary_result(row: SummaryResult) -> UsageLogResponse:
    return UsageLogResponse(
        id=encode_log_id(3, row.id),
        feature_type=3,
        feature_label=feature_label_for(3),
        input_text=row.request.input_text if row.request else "",
        request_id=row.request_id,
        output_text=row.summary_text or "",
        created_at=row.created_at,
    )


def serialize_tone_result(row: ToneResult) -> UsageLogResponse:
    return UsageLogResponse(
        id=encode_log_id(4, row.id),
        feature_type=4,
        feature_label=feature_label_for(4),
        input_text=row.request.input_text if row.request else "",
        request_id=row.request_id,
        output_text=row.changed_text or "",
        tone=row.requested_tone,
        created_at=row.created_at,
    )


def serialize_evaluation_result(row: EvaluationResult) -> UsageLogResponse:
    return UsageLogResponse(
        id=encode_log_id(5, row.id),
        feature_type=1,
        feature_label=feature_label_for(1),
        input_text=row.request.input_text if row.request else "",
        request_id=row.request_id,
        output_text=row.score_text or "",
        score=row.score,
        evaluation_reason=row.evaluation_reason,
        created_at=row.created_at,
    )


def serialize_title_result(row: TitleResult) -> UsageLogResponse:
    return UsageLogResponse(
        id=encode_log_id(1, row.id),
        feature_type=1,
        feature_label=feature_label_for(1),
        input_text=row.request.input_text if row.request else "",
        request_id=row.request_id,
        output_text=row.title_text or "",
        title=row.title_text or "",
        created_at=row.created_at,
    )


def serialize_history_request(request_row: AnalysisRequest) -> HistoryRequestResponse:
    spelling_row = max(request_row.spelling_results, key=lambda row: row.created_at) if request_row.spelling_results else None
    summary_row = max(request_row.summary_results, key=lambda row: row.created_at) if request_row.summary_results else None
    tone_row = max(request_row.tone_results, key=lambda row: row.created_at) if request_row.tone_results else None
    evaluation_row = max(request_row.evaluation_results, key=lambda row: row.created_at) if request_row.evaluation_results else None
    title_row = max(request_row.title_results, key=lambda row: row.created_at) if request_row.title_results else None
    return HistoryRequestResponse(
        request_id=request_row.id,
        input_text=request_row.input_text,
        created_at=request_row.created_at,
        spelling=(
            {
                "corrected_text": spelling_row.corrected_text,
                "spelling_feedback": spelling_row.spelling_feedback,
                "created_at": spelling_row.created_at.isoformat(),
            }
            if spelling_row
            else None
        ),
        summary=(
            {
                "summary_text": summary_row.summary_text,
                "created_at": summary_row.created_at.isoformat(),
            }
            if summary_row
            else None
        ),
        tone=(
            {
                "requested_tone": tone_row.requested_tone,
                "changed_text": tone_row.changed_text,
                "created_at": tone_row.created_at.isoformat(),
            }
            if tone_row
            else None
        ),
        evaluation=(
            {
                "score": evaluation_row.score,
                "score_text": evaluation_row.score_text,
                "evaluation_reason": evaluation_row.evaluation_reason,
                "created_at": evaluation_row.created_at.isoformat(),
            }
            if evaluation_row
            else None
        ),
        title=(
            {
                "title_text": title_row.title_text,
                "created_at": title_row.created_at.isoformat(),
            }
            if title_row
            else None
        ),
    )


def migrate_legacy_usage_logs():
    db = SessionLocal()
    try:
        for log in db.query(UsageLog).order_by(UsageLog.created_at.asc()).all():
            if not str(log.input_text or "").strip():
                continue
            request_row = get_or_create_request(db, log.user_id, log.input_text)
            if log.feature_type == 1 and log.title:
                exists = db.query(TitleResult).filter(TitleResult.request_id == request_row.id, TitleResult.title_text == log.title).first()
                if not exists:
                    db.add(TitleResult(request_id=request_row.id, title_text=log.title, created_at=log.created_at))
            elif log.feature_type == 1:
                exists = db.query(EvaluationResult).filter(EvaluationResult.request_id == request_row.id, EvaluationResult.score_text == (log.output_text or "")).first()
                if not exists:
                    db.add(
                        EvaluationResult(
                            request_id=request_row.id,
                            score=log.score,
                            score_text=log.output_text or (f"{log.score}점" if log.score is not None else ""),
                            evaluation_reason=log.evaluation_reason,
                            created_at=log.created_at,
                        )
                    )
            elif log.feature_type == 2:
                exists = db.query(SpellingResult).filter(SpellingResult.request_id == request_row.id, SpellingResult.corrected_text == (log.output_text or "")).first()
                if not exists:
                    db.add(
                        SpellingResult(
                            request_id=request_row.id,
                            corrected_text=log.output_text or "",
                            spelling_feedback=log.spelling_feedback,
                            created_at=log.created_at,
                        )
                    )
            elif log.feature_type == 3:
                exists = db.query(SummaryResult).filter(SummaryResult.request_id == request_row.id, SummaryResult.summary_text == (log.output_text or "")).first()
                if not exists:
                    db.add(SummaryResult(request_id=request_row.id, summary_text=log.output_text or "", created_at=log.created_at))
            elif log.feature_type == 4:
                exists = db.query(ToneResult).filter(ToneResult.request_id == request_row.id, ToneResult.changed_text == (log.output_text or "")).first()
                if not exists:
                    db.add(
                        ToneResult(
                            request_id=request_row.id,
                            requested_tone=log.tone,
                            changed_text=log.output_text or "",
                            created_at=log.created_at,
                        )
                    )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


migrate_legacy_usage_logs()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db)
) -> User:
    if not authorization.startswith("Bearer "):#
        raise HTTPException(status_code=401, detail="인증 토큰이 없습니다.")

    token = authorization.replace("Bearer ", "").strip()
    payload = decode_access_token(token)

    if not payload:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")

    expire_at = payload.get("exp")
    if expire_at is not None:
        expire_dt = datetime.fromtimestamp(float(expire_at), tz=timezone.utc).astimezone()
        print(
            f"[auth] token expires at {expire_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="토큰 정보가 올바르지 않습니다.")

    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다.")

    return user


@app.get("/")
def root():
    return {"message": "server is running 2021810064"}


@app.get("/ai-status")
def ai_status():
    return {
        "openai_key_loaded": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "correction_model": ai_service.model_for("correction"),
        "prompt_version": ai_service.PROMPT_VERSION,
    }


@app.post("/signup")
def signup(data: SignupRequest, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.username == data.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="이미 존재하는 사용자입니다.")

    user = User(
        username=data.username,
        password_hash=hash_password(data.password)
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {"message": "회원가입 완료"}


@app.post("/login", response_model=TokenResponse)#
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == data.username).first()
    if not user:
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 잘못되었습니다.")

    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 잘못되었습니다.")

    token_factory = create_remember_access_token if data.remember_me else create_access_token
    token = token_factory({"sub": user.username})
    return TokenResponse(access_token=token)


@app.post("/account/verify")
def verify_account(
    data: AccountVerifyRequest,
    current_user: User = Depends(get_current_user),
):
    if not verify_password(data.password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="비밀번호가 잘못되었습니다.")
    return {"verified": True}


@app.get("/account", response_model=AccountResponse)
def get_account(current_user: User = Depends(get_current_user)):
    return AccountResponse(
        username=current_user.username,
        display_name=current_user.display_name,
    )


@app.put("/account", response_model=AccountResponse)
def update_account(
    data: AccountUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    new_username = (data.username or current_user.username).strip()
    access_token = None

    if not new_username:
        raise HTTPException(status_code=400, detail="아이디를 입력해 주세요.")

    if new_username != current_user.username:
        existing_user = db.query(User).filter(User.username == new_username).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="이미 존재하는 아이디입니다.")
        current_user.username = new_username
        access_token = create_access_token({"sub": current_user.username})

    if data.display_name is not None:
        current_user.display_name = data.display_name.strip() or None
    if data.password:
        if len(data.password) < 4:
            raise HTTPException(status_code=400, detail="비밀번호는 4자 이상으로 입력해 주세요.")
        current_user.password_hash = hash_password(data.password)

    db.commit()
    db.refresh(current_user)
    return AccountResponse(
        username=current_user.username,
        display_name=current_user.display_name,
        access_token=access_token,
    )


@app.delete("/account")
def delete_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(UsageLog).filter(UsageLog.user_id == current_user.id).delete()
    db.query(AnalysisRequest).filter(AnalysisRequest.user_id == current_user.id).delete()
    db.query(UserSetting).filter(UserSetting.user_id == current_user.id).delete()
    db.query(ToneFavorite).filter(ToneFavorite.user_id == current_user.id).delete()
    db.delete(current_user)
    db.commit()
    return {"message": "계정이 삭제되었습니다."}


@app.post("/correct", response_model=CorrectResponse)
def correct_text(
    data: CorrectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="교정할 텍스트가 비어 있습니다.")

    result = run_ai_feature("correction", lambda: ai_service.correct_text(data.text))
    corrected = result["corrected_text"]

    log = UsageLog(
        user_id=current_user.id,
        input_text=data.text,
        output_text=corrected,
        feature_type=2,
        feature_label=feature_label_for(2),
        spelling_feedback=result.get("feedback"),
    )
    db.add(log)
    db.commit()

    return CorrectResponse(
        corrected_text=corrected,
        spelling_feedback=result.get("feedback"),
        corrections=result.get("corrections") or [],
    )


@app.post("/correct-public", response_model=CorrectResponse)
def correct_text_public(data: CorrectRequest):
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="Text is required.")
    result = run_ai_feature("correction", lambda: ai_service.correct_text(data.text))
    return CorrectResponse(
        corrected_text=result["corrected_text"],
        spelling_feedback=result.get("feedback"),
        corrections=result.get("corrections") or [],
    )


@app.post("/summary-public", response_model=SummaryResponse)
def summarize_text_public(data: SummaryRequest):
    result = run_ai_feature("summary", lambda: ai_service.summarize_text(data.text, data.style))
    return SummaryResponse(summary=result["summary"])


@app.post("/evaluation-public", response_model=EvaluationResponse)
def evaluate_text_public(data: EvaluationRequest):
    result = run_ai_feature("evaluation", lambda: ai_service.evaluate_text(data.text))
    return EvaluationResponse(
        score=int(result["score"]),
        feedback=str(result.get("feedback") or ""),
    )


@app.post("/title-public", response_model=TitleResponse)
def recommend_title_public(data: TitleRequest):
    result = run_ai_feature("title", lambda: ai_service.recommend_title(data.text))
    return TitleResponse(title=result["title"])


@app.post("/tone-public", response_model=ToneResponse)
def convert_tone_public(data: ToneRequest):
    result = run_ai_feature("tone", lambda: ai_service.convert_tone(data.text, data.tone))
    return ToneResponse(
        converted_text=result["converted_text"],
        feedback=result.get("feedback"),
    )


def run_ai_feature(feature_name: str, callback):
    try:
        return callback()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI {feature_name} failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI {feature_name} failed: {exc}") from exc


@app.post("/logs", response_model=UsageLogResponse)
def create_usage_log(
    data: UsageLogCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.feature_type not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="Unsupported feature_type.")
    if data.request_id is None and not data.input_text.strip():
        raise HTTPException(status_code=400, detail="input_text is required.")
    if data.score is not None and not 0 <= int(data.score) <= 100:
        raise HTTPException(status_code=400, detail="score must be between 0 and 100.")

    request_row = None
    if data.request_id is not None:
        request_row = (
            db.query(AnalysisRequest)
            .filter(AnalysisRequest.id == data.request_id, AnalysisRequest.user_id == current_user.id)
            .first()
        )
        if request_row is None:
            raise HTTPException(status_code=404, detail="request_id not found.")

    if request_row is None:
        request_row = get_or_create_request(db, current_user.id, data.input_text)

    if data.feature_type == 1 and data.title:
        row = (
            db.query(TitleResult)
            .filter(TitleResult.request_id == request_row.id)
            .order_by(TitleResult.created_at.desc())
            .first()
        )
        if row is None:
            row = TitleResult(request_id=request_row.id, title_text=data.title)
            db.add(row)
        else:
            row.title_text = data.title
            row.created_at = datetime.now().replace(tzinfo=None)
        db.commit()
        db.refresh(row)
        return serialize_title_result(row)

    if data.feature_type == 1:
        row = (
            db.query(EvaluationResult)
            .filter(EvaluationResult.request_id == request_row.id)
            .order_by(EvaluationResult.created_at.desc())
            .first()
        )
        if row is None:
            row = EvaluationResult(
                request_id=request_row.id,
                score=int(data.score) if data.score is not None else None,
                score_text=data.output_text or (f"{int(data.score)}점" if data.score is not None else ""),
                evaluation_reason=data.evaluation_reason,
            )
            db.add(row)
        else:
            row.score = int(data.score) if data.score is not None else row.score
            row.score_text = data.output_text or (f"{int(data.score)}점" if data.score is not None else row.score_text)
            row.evaluation_reason = data.evaluation_reason or row.evaluation_reason
            row.created_at = datetime.now().replace(tzinfo=None)
        db.commit()
        db.refresh(row)
        return serialize_evaluation_result(row)

    if data.feature_type == 2:
        row = (
            db.query(SpellingResult)
            .filter(SpellingResult.request_id == request_row.id)
            .order_by(SpellingResult.created_at.desc())
            .first()
        )
        if row is None:
            row = SpellingResult(
                request_id=request_row.id,
                corrected_text=data.output_text or "",
                spelling_feedback=data.spelling_feedback,
            )
            db.add(row)
        else:
            row.corrected_text = data.output_text or ""
            row.spelling_feedback = data.spelling_feedback
            row.created_at = datetime.now().replace(tzinfo=None)
        db.commit()
        db.refresh(row)
        return serialize_spelling_result(row)

    if data.feature_type == 3:
        row = (
            db.query(SummaryResult)
            .filter(SummaryResult.request_id == request_row.id)
            .order_by(SummaryResult.created_at.desc())
            .first()
        )
        if row is None:
            row = SummaryResult(request_id=request_row.id, summary_text=data.output_text or "")
            db.add(row)
        else:
            row.summary_text = data.output_text or ""
            row.created_at = datetime.now().replace(tzinfo=None)
        db.commit()
        db.refresh(row)
        return serialize_summary_result(row)

    if data.feature_type == 4:
        row = (
            db.query(ToneResult)
            .filter(ToneResult.request_id == request_row.id)
            .order_by(ToneResult.created_at.desc())
            .first()
        )
        if row is None:
            row = ToneResult(
                request_id=request_row.id,
                requested_tone=data.tone,
                changed_text=data.output_text or "",
            )
            db.add(row)
        else:
            row.requested_tone = data.tone
            row.changed_text = data.output_text or ""
            row.created_at = datetime.now().replace(tzinfo=None)
        db.commit()
        db.refresh(row)
        return serialize_tone_result(row)

    raise HTTPException(status_code=400, detail="Unsupported feature_type.")


@app.get("/logs", response_model=list[UsageLogResponse])
def list_usage_logs(
    feature_type: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = []
    if feature_type in (None, 0, 2):
        rows.extend(
            serialize_spelling_result(row)
            for row in db.query(SpellingResult)
            .join(AnalysisRequest, SpellingResult.request_id == AnalysisRequest.id)
            .filter(AnalysisRequest.user_id == current_user.id)
            .all()
        )
    if feature_type in (None, 0, 3):
        rows.extend(
            serialize_summary_result(row)
            for row in db.query(SummaryResult)
            .join(AnalysisRequest, SummaryResult.request_id == AnalysisRequest.id)
            .filter(AnalysisRequest.user_id == current_user.id)
            .all()
        )
    if feature_type in (None, 0, 4):
        rows.extend(
            serialize_tone_result(row)
            for row in db.query(ToneResult)
            .join(AnalysisRequest, ToneResult.request_id == AnalysisRequest.id)
            .filter(AnalysisRequest.user_id == current_user.id)
            .all()
        )
    if feature_type in (None, 0, 1):
        rows.extend(
            serialize_evaluation_result(row)
            for row in db.query(EvaluationResult)
            .join(AnalysisRequest, EvaluationResult.request_id == AnalysisRequest.id)
            .filter(AnalysisRequest.user_id == current_user.id)
            .all()
        )
        rows.extend(
            serialize_title_result(row)
            for row in db.query(TitleResult)
            .join(AnalysisRequest, TitleResult.request_id == AnalysisRequest.id)
            .filter(AnalysisRequest.user_id == current_user.id)
            .all()
        )
    return sorted(rows, key=lambda item: item.created_at, reverse=True)


@app.delete("/logs/{log_id}")
def delete_usage_log(
    log_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    encoded_feature, row_id = decode_log_id(log_id)
    models = {
        1: (EvaluationResult,),
        2: (SpellingResult,),
        3: (SummaryResult,),
        4: (ToneResult,),
        5: (TitleResult,),
        None: (SpellingResult, SummaryResult, ToneResult, EvaluationResult, TitleResult),
    }.get(encoded_feature, (SpellingResult, SummaryResult, ToneResult, EvaluationResult, TitleResult))
    deleted = False
    for model in models:
        row = (
            db.query(model)
            .join(AnalysisRequest, model.request_id == AnalysisRequest.id)
            .filter(model.id == row_id, AnalysisRequest.user_id == current_user.id)
            .first()
        )
        if row:
            db.delete(row)
            deleted = True
            break
    if not deleted:
        raise HTTPException(status_code=404, detail="삭제할 기록을 찾을 수 없습니다.")
    db.commit()
    return {"success": True}


@app.delete("/logs")
def delete_usage_logs(
    feature_type: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_ids = [row[0] for row in db.query(AnalysisRequest.id).filter(AnalysisRequest.user_id == current_user.id).all()]
    deleted = 0
    if request_ids:
        if feature_type in (None, 0, 2):
            deleted += db.query(SpellingResult).filter(SpellingResult.request_id.in_(request_ids)).delete(synchronize_session=False)
        if feature_type in (None, 0, 3):
            deleted += db.query(SummaryResult).filter(SummaryResult.request_id.in_(request_ids)).delete(synchronize_session=False)
        if feature_type in (None, 0, 4):
            deleted += db.query(ToneResult).filter(ToneResult.request_id.in_(request_ids)).delete(synchronize_session=False)
        if feature_type in (None, 0, 1):
            deleted += db.query(EvaluationResult).filter(EvaluationResult.request_id.in_(request_ids)).delete(synchronize_session=False)
            deleted += db.query(TitleResult).filter(TitleResult.request_id.in_(request_ids)).delete(synchronize_session=False)
    db.commit()
    return {"success": True, "deleted_count": int(deleted or 0)}


@app.get("/history/requests", response_model=list[HistoryRequestResponse])
def list_history_requests(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_rows = (
        db.query(AnalysisRequest)
        .filter(AnalysisRequest.user_id == current_user.id)
        .order_by(AnalysisRequest.created_at.desc())
        .all()
    )
    return [serialize_history_request(row) for row in request_rows]


@app.delete("/history/requests/{request_id}")
def delete_history_request(
    request_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_row = (
        db.query(AnalysisRequest)
        .filter(AnalysisRequest.id == request_id, AnalysisRequest.user_id == current_user.id)
        .first()
    )
    if not request_row:
        raise HTTPException(status_code=404, detail="삭제할 기록을 찾을 수 없습니다.")
    db.delete(request_row)
    db.commit()
    return {"success": True}


@app.get("/tone-favorites", response_model=list[ToneFavoriteResponse])
def list_tone_favorites(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(ToneFavorite)
        .filter(ToneFavorite.user_id == current_user.id)
        .order_by(ToneFavorite.created_at.desc(), ToneFavorite.id.desc())
        .limit(10)
        .all()
    )


@app.post("/tone-favorites", response_model=ToneFavoriteResponse)
def create_tone_favorite(
    data: ToneFavoriteCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tone = str(data.tone or "").strip()
    if not tone:
        raise HTTPException(status_code=400, detail="tone is required.")
    tone = tone[:100]
    existing = (
        db.query(ToneFavorite)
        .filter(ToneFavorite.user_id == current_user.id, ToneFavorite.tone == tone)
        .first()
    )
    if existing:
        return existing
    count = db.query(ToneFavorite).filter(ToneFavorite.user_id == current_user.id).count()
    if count >= 10:
        oldest = (
            db.query(ToneFavorite)
            .filter(ToneFavorite.user_id == current_user.id)
            .order_by(ToneFavorite.created_at.asc(), ToneFavorite.id.asc())
            .first()
        )
        if oldest:
            db.delete(oldest)
            db.flush()
    favorite = ToneFavorite(user_id=current_user.id, tone=tone)
    db.add(favorite)
    db.commit()
    db.refresh(favorite)
    return favorite


@app.delete("/tone-favorites/{favorite_id}")
def delete_tone_favorite(
    favorite_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    favorite = (
        db.query(ToneFavorite)
        .filter(ToneFavorite.id == favorite_id, ToneFavorite.user_id == current_user.id)
        .first()
    )
    if not favorite:
        raise HTTPException(status_code=404, detail="favorite not found.")
    db.delete(favorite)
    db.commit()
    return {"deleted": True}


@app.get("/settings", response_model=UserSettingsResponse)
def get_user_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings = db.query(UserSetting).filter(UserSetting.user_id == current_user.id).first()
    if not settings:
        return UserSettingsResponse(has_settings=False)
    return UserSettingsResponse(
        has_settings=True,
        default_dark_mode=settings.default_dark_mode,
        history_enabled=settings.history_enabled,
        input_mode=settings.input_mode,
        replace_mode=settings.replace_mode,
        spell_scope=settings.spell_scope,
        updated_at=settings.updated_at,
    )


@app.put("/settings", response_model=UserSettingsResponse)
def update_user_settings(
    data: UserSettingsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    input_mode = data.input_mode if data.input_mode in {"clipboard", "drag", "realtime"} else "clipboard"
    replace_mode = bool(data.replace_mode)
    spell_scope = data.spell_scope if data.spell_scope in {"current_sentence", "current_paragraph", "full_text"} else "current_sentence"
    settings = db.query(UserSetting).filter(UserSetting.user_id == current_user.id).first()
    if not settings:
        settings = UserSetting(user_id=current_user.id)
        db.add(settings)

    settings.default_dark_mode = bool(data.default_dark_mode)
    settings.history_enabled = bool(data.history_enabled)
    settings.input_mode = input_mode
    settings.replace_mode = replace_mode
    settings.spell_scope = spell_scope
    settings.updated_at = datetime.now()
    db.commit()
    db.refresh(settings)
    return UserSettingsResponse(
        has_settings=True,
        default_dark_mode=settings.default_dark_mode,
        history_enabled=settings.history_enabled,
        input_mode=settings.input_mode,
        replace_mode=settings.replace_mode,
        spell_scope=settings.spell_scope,
        updated_at=settings.updated_at,
    )
