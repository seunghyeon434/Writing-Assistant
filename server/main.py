from datetime import datetime, timezone
from sqlalchemy import inspect, text

from fastapi import FastAPI, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from database import SessionLocal, engine, Base
from models import User, UsageLog, UserSetting, ToneFavorite
from schemas import (
    SignupRequest,
    LoginRequest,
    CorrectResponse,
    TokenResponse,
    CorrectRequest,
    UsageLogCreateRequest,
    UsageLogResponse,
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

    corrected = ai_service.correct_text(data.text)

    log = UsageLog(
        user_id=current_user.id,
        input_text=data.text,
        output_text=corrected,
        feature_type=2,
        feature_label=feature_label_for(2),
    )
    db.add(log)
    db.commit()

    return CorrectResponse(corrected_text=corrected)


@app.post("/logs", response_model=UsageLogResponse)
def create_usage_log(
    data: UsageLogCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.feature_type not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="Unsupported feature_type.")
    if not data.input_text.strip():
        raise HTTPException(status_code=400, detail="input_text is required.")
    if data.score is not None and not 0 <= int(data.score) <= 100:
        raise HTTPException(status_code=400, detail="score must be between 0 and 100.")

    feature_label = data.feature_label or feature_label_for(data.feature_type)

    if data.feature_type == 1:
        existing_log = (
            db.query(UsageLog)
            .filter(
                UsageLog.user_id == current_user.id,
                UsageLog.feature_type == 1,
                UsageLog.input_text == data.input_text,
            )
            .order_by(UsageLog.created_at.desc())
            .first()
        )
        if existing_log:
            existing_log.feature_label = feature_label
            if data.title is not None:
                existing_log.title = data.title
            if data.score is not None:
                existing_log.score = data.score
            if data.evaluation_reason is not None:
                existing_log.evaluation_reason = data.evaluation_reason
            existing_log.output_text = data.output_text or existing_log.output_text or ""
            db.commit()
            db.refresh(existing_log)
            return existing_log

    log = UsageLog(
        user_id=current_user.id,
        input_text=data.input_text,
        output_text=data.output_text or "",
        feature_type=data.feature_type,
        feature_label=feature_label,
        title=data.title,
        score=data.score,
        tone=data.tone,
        spelling_feedback=data.spelling_feedback,
        evaluation_reason=data.evaluation_reason,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@app.get("/logs", response_model=list[UsageLogResponse])
def list_usage_logs(
    feature_type: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(UsageLog).filter(UsageLog.user_id == current_user.id)
    if feature_type is not None:
        query = query.filter(UsageLog.feature_type == feature_type)
    return query.order_by(UsageLog.created_at.desc()).all()


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
    settings = db.query(UserSetting).filter(UserSetting.user_id == current_user.id).first()
    if not settings:
        settings = UserSetting(user_id=current_user.id)
        db.add(settings)

    settings.default_dark_mode = bool(data.default_dark_mode)
    settings.history_enabled = bool(data.history_enabled)
    settings.input_mode = input_mode
    settings.replace_mode = replace_mode
    settings.updated_at = datetime.now()
    db.commit()
    db.refresh(settings)
    return UserSettingsResponse(
        has_settings=True,
        default_dark_mode=settings.default_dark_mode,
        history_enabled=settings.history_enabled,
        input_mode=settings.input_mode,
        replace_mode=settings.replace_mode,
        updated_at=settings.updated_at,
    )
