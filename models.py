# python-fastapi/models.py
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, create_engine, text, DateTime, Text
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
    eliminated = Column(Boolean, default=False)      # tagged out of the game
    notif_seen_id = Column(Integer, default=0)       # newest notification id this player has read
    eliminated_at = Column(DateTime, nullable=True)  # when they were tagged out (drives placement)
    eliminated_by_id = Column(Integer, nullable=True)  # who tagged them out

    def public(self):
        return {
            "id": self.id,
            "name": self.name,
            "score": self.score,
            "active": self.active,
            "status": self.status(),
            "nickname": self.nickname,
            "avatar_url": self.avatar_url(),
        }

    def status(self):
        # active = in play; eliminated = tagged out; inactive = not in the current game
        if self.active:
            return "active"
        return "eliminated" if self.eliminated else "inactive"

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
    event = Column(String, nullable=True)  # shared by every note one action produced; admins see them as one event
    target_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tagger = relationship("Player", foreign_keys=[tagger_id])
    recipient = relationship("Player", foreign_keys=[recipient_id])

    def public(self, include_recipient=False):
        data = {
            "id": self.id,
            "kind": self.kind,
            "message": self.message,
            "target": self.target_name,
            "created_at": self.created_at.isoformat() + "Z",
        }
        if include_recipient:
            data["recipient"] = self.recipient.name if self.recipient else None
        return data


Base.metadata.create_all(bind=engine)


def ensure_notification_columns():
    cols = {col["name"] for col in inspect(engine).get_columns("notifications")}
    with engine.begin() as conn:
        if "kind" not in cols:
            conn.execute(text("ALTER TABLE notifications ADD COLUMN kind TEXT"))
        if "recipient_id" not in cols:
            conn.execute(text("ALTER TABLE notifications ADD COLUMN recipient_id INTEGER REFERENCES players(id)"))
        if "event" not in cols:
            conn.execute(text("ALTER TABLE notifications ADD COLUMN event TEXT"))


ensure_notification_columns()


def ensure_game_columns():
    cols = {col["name"] for col in inspect(engine).get_columns("players")}
    with engine.begin() as conn:
        if "eliminated" not in cols:
            conn.execute(text("ALTER TABLE players ADD COLUMN eliminated BOOLEAN DEFAULT 0"))
            # players already knocked out under the old rules received a "tagged" notification
            conn.execute(text(
                "UPDATE players SET eliminated = 1 WHERE active = 0 AND id IN "
                "(SELECT recipient_id FROM notifications WHERE kind = 'tagged')"
            ))
        if "notif_seen_id" not in cols:
            conn.execute(text("ALTER TABLE players ADD COLUMN notif_seen_id INTEGER DEFAULT 0"))
            # existing players start with everything marked as read
            conn.execute(text("UPDATE players SET notif_seen_id = (SELECT COALESCE(MAX(id), 0) FROM notifications)"))
        if "eliminated_at" not in cols:
            conn.execute(text("ALTER TABLE players ADD COLUMN eliminated_at DATETIME"))
            conn.execute(text(
                "UPDATE players SET eliminated_at = (SELECT MAX(created_at) FROM notifications "
                "WHERE kind = 'tagged' AND recipient_id = players.id) WHERE eliminated = 1"
            ))
        if "eliminated_by_id" not in cols:
            conn.execute(text("ALTER TABLE players ADD COLUMN eliminated_by_id INTEGER"))
            conn.execute(text(
                "UPDATE players SET eliminated_by_id = (SELECT tagger_id FROM notifications "
                "WHERE kind = 'tagged' AND recipient_id = players.id ORDER BY id DESC LIMIT 1) WHERE eliminated = 1"
            ))


ensure_game_columns()


class Setting(Base):
    """Small key/value store; currently just the rules text."""
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


Base.metadata.create_all(bind=engine)
