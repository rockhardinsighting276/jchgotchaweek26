import secrets, random
from datetime import datetime
from sqlalchemy.orm import Session
from models import Player, Notification

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




def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def do_tag(db: Session, tagger: Player):
    if not tagger.active:
        raise ValueError("Tagger inactive")
    if tagger.target_id is None:
        raise ValueError("No target available (game likely ended)")

    target = db.get(Player, tagger.target_id)
    if not target or not target.active:
        raise ValueError("Target invalid or inactive")

    # Players alive before this elimination (admins never play). Counted up front:
    # autoflush is off, so nothing is visible to queries until commit. The target
    # finishes in this position (10 alive -> 10th), and 1 left means a winner.
    alive_before = (
        db.query(Player)
        .filter(Player.active == True, Player.is_admin == False)
        .count()
    )
    remaining = alive_before - 1

    # next target after the eliminated player
    next_after_target = db.get(Player, target.target_id) if target.target_id else None

    tagger.score += 1
    if remaining <= 1 or next_after_target is None or next_after_target.id == tagger.id:
        tagger.target_id = None
    else:
        tagger.target_id = next_after_target.id

    target.active = False
    target.target_id = None

    now = datetime.utcnow()
    tagger.last_tag_at = now
    tagger.score_last_updated = now

    new_target = db.get(Player, tagger.target_id) if tagger.target_id else None

    if remaining <= 1:
        tag_msg = f"You have tagged '{target.name}'. No players remain, you finished 1st!"
    elif new_target:
        tag_msg = f"You have tagged '{target.name}', your next target is '{new_target.name}'."
    else:
        tag_msg = f"You have tagged '{target.name}'."
    tagged_msg = f"You have been tagged by '{tagger.name}', you finished {ordinal(alive_before)}."

    tag_note = Notification(
        kind="tag", recipient_id=tagger.id, tagger_id=tagger.id,
        target_name=target.name, message=tag_msg, created_at=now,
    )
    db.add(tag_note)
    db.add(Notification(
        kind="tagged", recipient_id=target.id, tagger_id=tagger.id,
        message=tagged_msg, created_at=now,
    ))

    db.commit()
    db.refresh(tagger)
    db.refresh(target)
    db.refresh(tag_note)
    return tagger, new_target, target, tag_note
