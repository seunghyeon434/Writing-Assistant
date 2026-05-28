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


class UserSetting(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    default_dark_mode = Column(Boolean, nullable=False, default=False)
    history_enabled = Column(Boolean, nullable=False, default=False)
    input_mode = Column(String(20), nullable=False, default="clipboard")
    replace_mode = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=local_now, onupdate=local_now)


class ToneFavorite(Base):
    __tablename__ = "tone_favorites"
    __table_args__ = (UniqueConstraint("user_id", "tone", name="uq_tone_favorite_user_tone"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    tone = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=local_now)

    user = relationship("User", back_populates="tone_favorites")
