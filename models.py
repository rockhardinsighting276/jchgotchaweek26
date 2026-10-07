# python-fastapi/models.py
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, create_engine, text, DateTime
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy import inspect
import os
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
    # Who acted: the tagger for "tag"/"tagged" notes, the sending admin for "global".
    tagger_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    # "tag" | "tagged" | "global". NULL = old public tag message, no longer shown.
    kind = Column(String, nullable=True)
    # Who may see it. NULL only for global announcements.
    recipient_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    message = Column(String(1000), nullable=False)
    target_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tagger = relationship("Player", foreign_keys=[tagger_id])
    recipient = relationship("Player", foreign_keys=[recipient_id])

    def public(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "message": self.message,
            "target": self.target_name,
            "created_at": self.created_at.isoformat() + "Z",
        }


Base.metadata.create_all(bind=engine)


def ensure_notification_columns():
    cols = {col["name"] for col in inspect(engine).get_columns("notifications")}
    with engine.begin() as conn:
        if "kind" not in cols:
            conn.execute(text("ALTER TABLE notifications ADD COLUMN kind TEXT"))
        if "recipient_id" not in cols:
            conn.execute(text("ALTER TABLE notifications ADD COLUMN recipient_id INTEGER REFERENCES players(id)"))


ensure_notification_columns()
