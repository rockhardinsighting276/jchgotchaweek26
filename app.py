from fastapi import FastAPI, Depends, HTTPException, Header, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload
import os
import re
import uuid
from pathlib import Path
import bcrypt
from datetime import datetime, timedelta
from models import SessionLocal, Player, Notification
from schema import RegisterIn, LoginIn, InitIn, TagOut, NicknameIn, AnnounceIn, CreateUserIn, InsertIn, WipeIn, ReadIn
from game import init_circle, insert_player, do_tag, mint_token

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


ADMIN_SECRET = os.getenv("ADMIN_SECRET", "changeme")
MAX_PASSWORD_BYTES = 72
AVATAR_DIR = Path("static/avatars")
AVATAR_DIR.mkdir(parents=True, exist_ok=True)
MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2MB
ALLOWED_AVATAR_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
TAG_COOLDOWN = timedelta(seconds=float(os.getenv("TAG_COOLDOWN_SECONDS", "6")))

def cooldown_seconds(player: Player) -> int:
    last = player.last_tag_at
    if not last:
        return 0
    if isinstance(last, str):
        try:
            last = datetime.fromisoformat(last)
        except ValueError:
            return 0
    expires = last + TAG_COOLDOWN
    remaining = expires - datetime.utcnow()
    if remaining.total_seconds() <= 0:
        return 0
    return int(remaining.total_seconds())


# --- DB Dependency ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        
# --- Auth helpers ---


def hash_password(password: str) -> str:
    data = password.encode("utf-8")
    if len(data) > MAX_PASSWORD_BYTES:
        raise ValueError("Password too long")
    return bcrypt.hashpw(data, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    data = password.encode("utf-8")
    if len(data) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(data, hashed.encode("utf-8"))
    except ValueError:
        return False


def auth_player(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db)
) -> Player:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split()[1]
    p = db.query(Player).filter_by(token=token).first()
    if not p:
        raise HTTPException(status_code=401, detail="Invalid token")
    return p

# --- WebSocket Hub for leaderboard ---
class Hub:
    def __init__(self):
        self.conns = set()
        
    async def add(self, ws: WebSocket):
        await ws.accept()
        self.conns.add(ws)
    def remove(self, ws: WebSocket):
        self.conns.discard(ws)
    async def broadcast(self, payload: dict):
        dead = []
        for ws in list(self.conns):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove(ws)


hub = Hub()


async def notify_changed():
    # Content-free ping: each client re-fetches only what it is allowed to see.
    await hub.broadcast({"type": "changed"})


@app.websocket("/ws/leaderboard")
async def ws_leaderboard(ws: WebSocket):
    await hub.add(ws)
    try:
        while True:
            await ws.receive_text() # keepalive; client sends pings
    except WebSocketDisconnect:
        hub.remove(ws)


@app.get("/")
def root():
    return FileResponse("static/index.html")


# --- Public / Auth routes ---
@app.get("/setup")
def setup_status(db: Session = Depends(get_db)):
    has_admin = db.query(Player).filter(Player.is_admin == True).first() is not None
    return {"needs_admin": not has_admin}


@app.post("/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
    # Public registration only exists to create the very first admin.
    if db.query(Player).filter(Player.is_admin == True).first():
        raise HTTPException(403, "Registration is closed. Ask an admin to create your account.")
    if not body.admin_secret or body.admin_secret != ADMIN_SECRET:
        raise HTTPException(403, "Admin secret required to create the first admin")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    if not body.password or len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters long")
    if len(body.password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise HTTPException(400, "Password too long; must be 72 bytes or fewer")
    is_admin = body.admin_secret == ADMIN_SECRET if body.admin_secret else False

    existing = db.query(Player).filter_by(name=name).first()
    if existing:
        if existing.password_hash:
            if not verify_password(body.password, existing.password_hash):
                raise HTTPException(409, "Player already registered")
        else:
            existing.password_hash = hash_password(body.password)
        if is_admin and not existing.is_admin:
            existing.is_admin = True
        if existing.score_last_updated is None:
            existing.score_last_updated = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return {
            "token": existing.token,
            "name": existing.name,
            "is_admin": existing.is_admin,
            "avatar_url": existing.avatar_url(),
            "nickname": existing.nickname,
            "cooldown_seconds": cooldown_seconds(existing),
        }

    player = Player(
        name=name,
        token=mint_token(),
        is_admin=is_admin,
        password_hash=hash_password(body.password),
        score_last_updated=datetime.utcnow(),
    )
    db.add(player)
    db.commit()
    db.refresh(player)
    return {
        "token": player.token,
        "name": player.name,
        "is_admin": player.is_admin,
        "avatar_url": player.avatar_url(),
        "nickname": player.nickname,
        "cooldown_seconds": cooldown_seconds(player),
    }


@app.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    if not body.password:
        raise HTTPException(400, "Password is required")
    if len(body.password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise HTTPException(400, "Password too long; must be 72 bytes or fewer")
    p = db.query(Player).filter_by(name=name).first()
    if not p or not verify_password(body.password, p.password_hash):
        raise HTTPException(401, "Invalid credentials")
    return {
        "token": p.token,
        "name": p.name,
        "is_admin": p.is_admin,
        "avatar_url": p.avatar_url(),
        "nickname": p.nickname,
        "cooldown_seconds": cooldown_seconds(p),
    }


@app.get("/me")
def me(p: Player = Depends(auth_player)):
    return {
        "id": p.id,
        "name": p.name,
        "score": p.score,
        "active": p.active,
        "status": p.status(),
        "is_admin": p.is_admin,
        "avatar_url": p.avatar_url(),
        "nickname": p.nickname,
        "cooldown_seconds": cooldown_seconds(p),
    }


@app.get("/target")
def target(p: Player = Depends(auth_player), db: Session = Depends(get_db)):
    if not p.active or p.target_id is None:
        return {"target": None}
    t = db.get(Player, p.target_id)
    return {"target": t.name if t and t.active else None}


@app.post("/me/nickname")
async def update_nickname(body: NicknameIn, p: Player = Depends(auth_player), db: Session = Depends(get_db)):
    nickname = (body.nickname or "").strip()
    if nickname and len(nickname) > 40:
        raise HTTPException(400, "Nickname must be 40 characters or fewer")
    p.nickname = nickname or None
    db.commit()
    db.refresh(p)
    await notify_changed()
    return {"nickname": p.nickname}


@app.post("/me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    p: Player = Depends(auth_player),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(400, "File name required")
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Only image uploads are allowed")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(400, "Avatar too large (max 2MB)")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_AVATAR_EXTS:
        ext = ".png"
    filename = f"{p.id}_{uuid.uuid4().hex}{ext}"
    relative_path = Path("avatars") / filename
    absolute_path = AVATAR_DIR / filename

    with open(absolute_path, "wb") as out:
        out.write(data)

    if p.avatar_path:
        old_path = Path("static") / p.avatar_path
        try:
            if old_path.is_file():
                old_path.unlink()
        except OSError:
            pass

    p.avatar_path = str(relative_path)
    db.commit()
    db.refresh(p)

    await notify_changed()

    return {"avatar_url": p.avatar_url()}


@app.post("/tag", response_model=TagOut)
async def tag(
    p: Player = Depends(auth_player),
    db: Session = Depends(get_db),
):
    remaining = cooldown_seconds(p)
    if remaining > 0:
        raise HTTPException(
            429,
            {"message": "Cooldown active", "cooldown_seconds": remaining},
        )
    try:
        tagger, new_target, eliminated, note = do_tag(db, p)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await notify_changed()
    return {
        "ok": True,
        "new_target": (new_target.name if new_target else None),
        "score": tagger.score,
        "notification": note.public(),
        "cooldown_seconds": cooldown_seconds(tagger),
    }


@app.get("/leaderboard")
def leaderboard(p: Player = Depends(auth_player), db: Session = Depends(get_db)):
    if not p.is_admin:
        raise HTTPException(403, "Admin only")
    players = db.query(Player).filter(Player.is_admin == False).all()
    order = {"active": 0, "eliminated": 1, "inactive": 2}
    players.sort(key=lambda pl: (order[pl.status()], -(pl.score or 0), pl.score_last_updated or datetime.min, pl.id))
    names = {pl.id: pl.name for pl in players}
    leaders = []
    for pl in players:
        row = pl.public()
        row["target"] = names.get(pl.target_id) if pl.active else None
        leaders.append(row)
    return {"leaders": leaders}


def visible_notes(db: Session, p: Player):
    q = db.query(Notification).filter(Notification.kind.isnot(None))
    if not p.is_admin:
        q = q.filter(or_(Notification.kind == "global", Notification.recipient_id == p.id))
    return q


@app.get("/notifications")
def notifications(p: Player = Depends(auth_player), db: Session = Depends(get_db)):
    limit = 300 if p.is_admin else 50  # admins oversee every notification
    notes = (
        visible_notes(db, p)
        .options(joinedload(Notification.recipient))
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
        .all()
    )
    seen = p.notif_seen_id or 0
    unread = 0
    if not p.is_admin:
        # a player's own "you tagged X" confirmation never counts as unread
        unread = visible_notes(db, p).filter(Notification.id > seen, Notification.kind != "tag").count()
    return {
        "notifications": [n.public(include_recipient=p.is_admin) for n in notes],
        "unread": unread,
        "seen_id": seen,
    }


@app.post("/notifications/read")
def mark_notifications_read(body: ReadIn, p: Player = Depends(auth_player), db: Session = Depends(get_db)):
    latest = db.query(func.max(Notification.id)).scalar() or 0
    up_to = min(body.up_to, latest)
    if up_to > (p.notif_seen_id or 0):
        p.notif_seen_id = up_to
        db.commit()
    return {"ok": True, "seen_id": p.notif_seen_id or 0}


# --- Admin ---
@app.post("/admin/init")
async def admin_init(body: InitIn, p: Player = Depends(auth_player), db: Session = Depends(get_db)):
    if not p.is_admin:
        raise HTTPException(403, "Admin only")
    try:
        init_circle(db, p, shuffle=body.shuffle)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    await notify_changed()
    return {"ok": True}


@app.post("/admin/insert")
async def admin_insert(body: InsertIn, p: Player = Depends(auth_player), db: Session = Depends(get_db)):
    if not p.is_admin:
        raise HTTPException(403, "Admin only")
    newcomer = db.get(Player, body.player_id)
    anchor = db.get(Player, body.after_id)
    if not newcomer or not anchor:
        raise HTTPException(404, "Player not found")
    try:
        insert_player(db, p, newcomer, anchor)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    await notify_changed()
    return {"ok": True}


@app.post("/admin/users")
def admin_create_user(body: CreateUserIn, p: Player = Depends(auth_player), db: Session = Depends(get_db)):
    if not p.is_admin:
        raise HTTPException(403, "Admin only")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters long")
    if len(body.password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise HTTPException(400, "Password too long; must be 72 bytes or fewer")
    if db.query(Player).filter_by(name=name).first():
        raise HTTPException(409, "An account with that name already exists")
    player = Player(
        name=name,
        token=mint_token(),
        is_admin=body.is_admin,
        active=False,  # accounts start out of the game until an admin starts it or inserts them
        password_hash=hash_password(body.password),
        score_last_updated=datetime.utcnow(),
    )
    db.add(player)
    db.commit()
    return {"ok": True, "name": player.name, "is_admin": player.is_admin}


@app.post("/admin/announce")
async def admin_announce(body: AnnounceIn, p: Player = Depends(auth_player), db: Session = Depends(get_db)):
    if not p.is_admin:
        raise HTTPException(403, "Admin only")
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "Message is required")
    if len(message) > 1000:
        raise HTTPException(400, "Message too long (max 1000 characters)")
    note = Notification(kind="global", recipient_id=None, tagger_id=p.id, message=message)
    db.add(note)
    db.commit()
    db.refresh(note)
    await notify_changed()
    return {"ok": True, "notification": note.public()}


AVATAR_FILE = re.compile(r"^\d+_[0-9a-f]{32}\.[a-z]+$")


@app.post("/admin/wipe")
async def admin_wipe(body: WipeIn, p: Player = Depends(auth_player), db: Session = Depends(get_db)):
    if not p.is_admin:
        raise HTTPException(403, "Admin only")
    if not body.admin_secret or body.admin_secret != ADMIN_SECRET:
        raise HTTPException(403, "Incorrect admin secret")
    db.query(Notification).delete()
    db.query(Player).update({Player.target_id: None})
    db.query(Player).delete()
    db.commit()
    for f in AVATAR_DIR.iterdir():
        if f.is_file() and AVATAR_FILE.match(f.name):
            try:
                f.unlink()
            except OSError:
                pass
    await notify_changed()
    return {"ok": True}
