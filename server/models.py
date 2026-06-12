from sqlalchemy import Boolean, Column, Integer, String, Text, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from zoneinfo import ZoneInfo

from database import Base


_KST = ZoneInfo("Asia/Seoul")

def local_now():
    return datetime.now(_KST).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)#
    username = Column(String(100), unique=True, nullable=False, index=True)
    display_name = Column(String(100), nullable=True)
    password_hash = Column(String(255), nullable=False)

    logs = relationship("UsageLog", back_populates="user")#
    analysis_requests = relationship("AnalysisRequest", back_populates="user", cascade="all, delete-orphan")
    tone_favorites = relationship("ToneFavorite", back_populates="user")


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    input_text = Column(Text, nullable=False)
    output_text = Column(Text, nullable=False)
    feature_type = Column(Integer, nullable=False, default=2)
    feature_label = Column(String(50), nullable=True)
    title = Column(String(255), nullable=True)
    score = Column(Integer, nullable=True)
    tone = Column(String(100), nullable=True)
    spelling_feedback = Column(Text, nullable=True)
    evaluation_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=local_now)

    user = relationship("User", back_populates="logs")#


class AnalysisRequest(Base):
    __tablename__ = "analysis_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    input_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=local_now, nullable=False)

    user = relationship("User", back_populates="analysis_requests")
    spelling_results = relationship("SpellingResult", back_populates="request", cascade="all, delete-orphan")
    summary_results = relationship("SummaryResult", back_populates="request", cascade="all, delete-orphan")
    tone_results = relationship("ToneResult", back_populates="request", cascade="all, delete-orphan")
    evaluation_results = relationship("EvaluationResult", back_populates="request", cascade="all, delete-orphan")
    title_results = relationship("TitleResult", back_populates="request", cascade="all, delete-orphan")


class SpellingResult(Base):
    __tablename__ = "spelling_results"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(Integer, ForeignKey("analysis_requests.id"), nullable=False, index=True)
    corrected_text = Column(Text, nullable=False, default="")
    spelling_feedback = Column(Text, nullable=True)
    created_at = Column(DateTime, default=local_now, nullable=False)

    request = relationship("AnalysisRequest", back_populates="spelling_results")


class SummaryResult(Base):
    __tablename__ = "summary_results"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(Integer, ForeignKey("analysis_requests.id"), nullable=False, index=True)
    summary_text = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=local_now, nullable=False)

    request = relationship("AnalysisRequest", back_populates="summary_results")


class ToneResult(Base):
    __tablename__ = "tone_results"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(Integer, ForeignKey("analysis_requests.id"), nullable=False, index=True)
    requested_tone = Column(String(100), nullable=True)
    changed_text = Column(Text, nullable=False, default="")
    feedback = Column(Text, nullable=True)
    created_at = Column(DateTime, default=local_now, nullable=False)

    request = relationship("AnalysisRequest", back_populates="tone_results")


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(Integer, ForeignKey("analysis_requests.id"), nullable=False, index=True)
    score = Column(Integer, nullable=True)
    score_text = Column(String(20), nullable=True)
    evaluation_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=local_now, nullable=False)

    request = relationship("AnalysisRequest", back_populates="evaluation_results")


class TitleResult(Base):
    __tablename__ = "title_results"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(Integer, ForeignKey("analysis_requests.id"), nullable=False, index=True)
    title_text = Column(String(255), nullable=False, default="")
    created_at = Column(DateTime, default=local_now, nullable=False)

    request = relationship("AnalysisRequest", back_populates="title_results")


class UserSetting(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    default_dark_mode = Column(Boolean, nullable=False, default=False)
    history_enabled = Column(Boolean, nullable=False, default=False)
    input_mode = Column(String(20), nullable=False, default="clipboard")
    replace_mode = Column(Boolean, nullable=False, default=False)
    spell_scope = Column(String(30), nullable=False, default="current_sentence")
    updated_at = Column(DateTime, default=local_now, onupdate=local_now)


class ToneFavorite(Base):
    __tablename__ = "tone_favorites"
    __table_args__ = (UniqueConstraint("user_id", "tone", name="uq_tone_favorite_user_tone"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    tone = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=local_now)

    user = relationship("User", back_populates="tone_favorites")
