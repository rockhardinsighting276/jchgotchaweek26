from pydantic import BaseModel
from typing import List


class RegisterIn(BaseModel):
    name: str
    password: str
    admin_secret: str | None = None


class LoginIn(BaseModel):
    name: str
    password: str


class TagIn(BaseModel):
    message: str | None = None


class InitIn(BaseModel):
    players: List[str]
    shuffle: bool = True


class TagOut(BaseModel):
    ok: bool
    new_target: str | None
    score: int
    notification: dict | None = None
    cooldown_seconds: int


class NicknameIn(BaseModel):
    nickname: str | None = None
