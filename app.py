from fastapi import FastAPI, Depends, HTTPException, Header, WebSocket, WebSocketDisconnect, Body, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import os
import uuid
from pathlib import Path
import bcrypt
from models import SessionLocal, Player, Notification
from schema import RegisterIn, LoginIn, TagIn, InitIn, TagOut
from game import init_circle, do_tag, mint_token

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


ADMIN_SECRET = os.getenv("ADMIN_SECRET", "changeme")
MAX_PASSWORD_BYTES = 72
AVATAR_DIR = Path("static/avatars")
AVATAR_DIR.mkdir(parents=True, exist_ok=True)
MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2MB
ALLOWED_AVATAR_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


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
            except WebSocketDisconnect:
                dead.append(ws)
        for ws in dead:
            self.remove(ws)


hub = Hub()


@app.websocket("/ws/leaderboard")
async def ws_leaderboard(ws: WebSocket):
    await hub.add(ws)
    try:
        db = SessionLocal()
        try:
            leaders = [p.public() for p in db.query(Player).order_by(Player.score.desc()).all()]
            notes = [
                n.public()
                for n in db.query(Notification)
                .order_by(Notification.created_at.desc())
                .limit(25)
            ]
        finally:
            db.close()
        await ws.send_json({"type": "leaderboard", "leaders": leaders})
        await ws.send_json({"type": "notifications", "notifications": notes})
        while True:
            await ws.receive_text() # keepalive; client sends pings
    except WebSocketDisconnect:
        hub.remove(ws)


@app.get("/")
def root():
    return FileResponse("static/index.html")


# --- Public / Auth routes ---
@app.post("/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
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
        db.commit()
        db.refresh(existing)
        return {
            "token": existing.token,
            "name": existing.name,
            "is_admin": existing.is_admin,
            "avatar_url": existing.avatar_url(),
        }

    player = Player(
        name=name,
        token=mint_token(),
        is_admin=is_admin,
        password_hash=hash_password(body.password),
    )
    db.add(player)
    db.commit()
    db.refresh(player)
    return {
        "token": player.token,
        "name": player.name,
        "is_admin": player.is_admin,
        "avatar_url": player.avatar_url(),
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
    }


@app.get("/me")
def me(p: Player = Depends(auth_player)):
    return {
        "id": p.id,
        "name": p.name,
        "score": p.score,
        "active": p.active,
        "is_admin": p.is_admin,
        "avatar_url": p.avatar_url(),
    }


@app.get("/target")
def target(p: Player = Depends(auth_player), db: Session = Depends(get_db)):
    if not p.active or p.target_id is None:
        return {"target": None}
    t = db.get(Player, p.target_id)
    return {"target": t.name if t and t.active else None}


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

    leaders = [pl.public() for pl in db.query(Player).order_by(Player.score.desc()).all()]
    await hub.broadcast({"type": "leaderboard", "leaders": leaders})

    return {"avatar_url": p.avatar_url()}


@app.post("/tag", response_model=TagOut)
async def tag(
    body: TagIn | None = Body(default=None),
    p: Player = Depends(auth_player),
    db: Session = Depends(get_db),
):
    try:
        tagger, new_target, eliminated = do_tag(db, p)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # broadcast leaderboard
    leaders = [pl.public() for pl in db.query(Player).order_by(Player.score.desc()).all()]
    await hub.broadcast({"type": "leaderboard", "leaders": leaders})

    notification_payload = None
    if body and body.message:
        message = body.message.strip()
        if message:
            if len(message) > 280:
                raise HTTPException(400, "Message too long (max 280 characters)")
            note = Notification(
                tagger_id=tagger.id,
                message=message,
                target_name=eliminated.name if eliminated else None,
            )
            db.add(note)
            db.commit()
            db.refresh(note)
            notification_payload = note.public()
            await hub.broadcast({"type": "notification", "notification": notification_payload})
    return {
        "ok": True,
        "new_target": (new_target.name if new_target else None),
        "score": tagger.score,
        "notification": notification_payload,
    }


@app.get("/leaderboard")
def leaderboard(db: Session = Depends(get_db)):
    leaders = [p.public() for p in db.query(Player).order_by(Player.score.desc()).all()]
    return {"leaders": leaders}


@app.get("/notifications")
def notifications(db: Session = Depends(get_db)):
    notes = [
        n.public()
        for n in db.query(Notification)
        .order_by(Notification.created_at.desc())
        .limit(50)
    ]
    return {"notifications": notes}
# --- Admin ---
@app.post("/admin/init")
async def admin_init(body: InitIn, p: Player = Depends(auth_player), db: Session = Depends(get_db)):
    if not p.is_admin:
        raise HTTPException(403, "Admin only")
    if len(body.players) < 2:
        raise HTTPException(400, "Need at least 2 players")
    cleaned = [name.strip() for name in body.players if name.strip()]
    if len(cleaned) < 2:
        raise HTTPException(400, "Need at least 2 valid player names")
    if len(set(cleaned)) != len(cleaned):
        raise HTTPException(400, "Player names must be unique")
    try:
        init_circle(db, cleaned, shuffle=body.shuffle)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    leaders = [pl.public() for pl in db.query(Player).order_by(Player.score.desc()).all()]
    await hub.broadcast({"type": "leaderboard", "leaders": leaders})
    return {"ok": True}


@app.get("/admin/mapping")
def admin_mapping(p: Player = Depends(auth_player), db: Session = Depends(get_db)):
    if not p.is_admin:
        raise HTTPException(403, "Admin only")
    
    res = []
    for pl in db.query(Player).order_by(Player.id).all():
        tgt = db.get(Player, pl.target_id) if pl.target_id else None
        res.append({"player": pl.name, "active": pl.active, "target": (tgt.name if tgt else None), "score": pl.score})
    return {"mapping": res}
