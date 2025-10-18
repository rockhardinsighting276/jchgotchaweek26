import secrets, random
from datetime import datetime
from sqlalchemy.orm import Session
from models import Player

TOKEN_LEN = 24

def mint_token():
    return secrets.token_urlsafe(TOKEN_LEN)


def init_circle(db: Session, names: list[str], shuffle=True):
    players = []
    for name in names:
        player = db.query(Player).filter_by(name=name).first()
        if not player:
            raise ValueError(f"Player '{name}' is not registered")
        players.append(player)
    if shuffle:
        random.shuffle(players)

    for i, player in enumerate(players):
        nxt = players[(i + 1) % len(players)] if len(players) > 1 else None
        player.target_id = nxt.id if nxt and nxt.id != player.id else None
        player.active = True
        player.score = 0
        player.last_tag_at = None
        player.score_last_updated = datetime.utcnow()

    others = (
        db.query(Player)
        .filter(Player.is_admin == False)
        .filter(~Player.id.in_([p.id for p in players]))
        .all()
    )
    for other in others:
        other.active = False
        other.target_id = None
        other.last_tag_at = None
        other.score_last_updated = datetime.utcnow()

    db.commit()
    for player in players:
        db.refresh(player)
    return players




def do_tag(db: Session, tagger: Player):
    if not tagger.active:
        raise ValueError("Tagger inactive")
    if tagger.target_id is None:
        raise ValueError("No target available (game likely ended)")


    target = db.get(Player, tagger.target_id)
    if not target or not target.active:
        raise ValueError("Target invalid or inactive")


    # next target after the eliminated player
    next_after_target = db.get(Player, target.target_id) if target.target_id else None


    # Update tagger
    tagger.score += 1
    tagger.target_id = next_after_target.id if next_after_target and next_after_target.id != tagger.id else None


    # Eliminate target
    target.active = False
    target.target_id = None

    # Check if only one active remains
    active_count = db.query(Player).filter_by(active=True).count()
    if active_count <= 1:
        # Last player has no target
        only = db.query(Player).filter_by(active=True).first()
        if only:
            only.target_id = None


    now = datetime.utcnow()
    tagger.last_tag_at = now
    tagger.score_last_updated = now

    db.commit()
    db.refresh(tagger)
    db.refresh(target)
    return tagger, next_after_target, target
