from pydantic import BaseModel
from typing import List


class RegisterIn(BaseModel):
    name: str
    password: str
    admin_secret: str | None = None


class LoginIn(BaseModel):
    name: str
    password: str


class InitIn(BaseModel):
    shuffle: bool = True


class TagOut(BaseModel):
    ok: bool
    new_target: str | None
    score: int
    notification: dict | None = None
    cooldown_seconds: int


class NicknameIn(BaseModel):
    nickname: str | None = None


class AnnounceIn(BaseModel):
    message: str


class CreateUserIn(BaseModel):
    name: str
    password: str


class InsertIn(BaseModel):
    player_id: int
    after_id: int


class WipeIn(BaseModel):
    admin_secret: str


class ReadIn(BaseModel):
    up_to: int


class UndoIn(BaseModel):
    player_id: int
    mode: str                      # "tagger_place" or "insert"
    after_id: int | None = None    # required for mode "insert"


class RulesIn(BaseModel):
    text: str
    notify: bool = True


class WithdrawIn(BaseModel):
    password: str
