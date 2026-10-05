# python-fastapi/models.py
import os
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, create_engine, text, DateTime
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy import inspect
from datetime import datetime

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./gotcha.db")
engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, index=True)
    token = Column(String, unique=True, index=True)
    is_admin = Column(Boolean, default=False)
    score = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    target_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    target = relationship("Player", remote_side=[id], uselist=False)
    password_hash = Column(String, nullable=True)
    avatar_path = Column(String, nullable=True)
    nickname = Column(String, nullable=True)
    last_tag_at = Column(DateTime, nullable=True)
    score_last_updated = Column(DateTime, nullable=True)

    def public(self):
        return {
            "id": self.id,
            "name": self.name,
            "score": self.score,
            "active": self.active,
            "nickname": self.nickname,
            "avatar_url": self.avatar_url(),
        }

    def avatar_url(self):
        if not self.avatar_path:
            return None
        return f"/static/{self.avatar_path}"


Base.metadata.create_all(bind=engine)


def ensure_columns():
    insp = inspect(engine)
    cols = {col["name"] for col in insp.get_columns("players")}
    if "password_hash" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE players ADD COLUMN password_hash TEXT"))
    if "avatar_path" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE players ADD COLUMN avatar_path TEXT"))
    if "nickname" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE players ADD COLUMN nickname TEXT"))
    if "last_tag_at" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE players ADD COLUMN last_tag_at DATETIME"))
    if "score_last_updated" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE players ADD COLUMN score_last_updated DATETIME"))


ensure_columns()


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    tagger_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    message = Column(String(500), nullable=False)
    target_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tagger = relationship("Player", foreign_keys=[tagger_id])

    def public(self):
        return {
            "id": self.id,
            "tagger": self.tagger.name if self.tagger else None,
            "tagger_nickname": self.tagger.nickname if self.tagger else None,
            "target": self.target_name,
            "message": self.message,
            "created_at": self.created_at.isoformat() + "Z",
            "tagger_avatar": self.tagger.avatar_url() if self.tagger else None,
        }


Base.metadata.create_all(bind=engine)
