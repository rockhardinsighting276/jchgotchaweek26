import secrets, random
from datetime import datetime
from sqlalchemy.orm import Session
from models import Player, Notification

TOKEN_LEN = 24

def mint_token():
    return secrets.token_urlsafe(TOKEN_LEN)


def _notify(db: Session, admin: Player, recipient: Player, message: str, when=None):
    db.add(Notification(
        kind="update", recipient_id=recipient.id, tagger_id=admin.id,
        message=message, created_at=when or datetime.utcnow(),
    ))


def init_circle(db: Session, admin: Player, shuffle=True):
    """Start (or restart) a game with every non-admin player."""
    players = db.query(Player).filter(Player.is_admin == False).order_by(Player.id).all()
    if len(players) < 2:
        raise ValueError("Need at least 2 players to start a game")
    if shuffle:
        random.shuffle(players)

    now = datetime.utcnow()
    for i, player in enumerate(players):
        nxt = players[(i + 1) % len(players)]
        player.target_id = nxt.id
        player.active = True
        player.eliminated = False
        player.score = 0
        player.last_tag_at = None
        player.score_last_updated = now
        _notify(db, admin, player, f"A new game has started. Your target is '{nxt.name}'.", now)

    db.commit()
    for player in players:
        db.refresh(player)
    return players


def insert_player(db: Session, admin: Player, newcomer: Player, anchor: Player):
    """Insert an inactive player as the new target of `anchor`.

    The newcomer takes over anchor's old target, so the loop stays unbroken:
        anchor -> old_target   becomes   anchor -> newcomer -> old_target
    Only anchor and newcomer are notified; old_target is deliberately left alone.
    """
    if newcomer.is_admin or newcomer.active or newcomer.eliminated:
        raise ValueError("Only inactive players can be inserted into the game")
    if anchor.is_admin or not anchor.active:
        raise ValueError("The chosen player is not active in the game")

    old_target = db.get(Player, anchor.target_id) if anchor.target_id else None
    if old_target is None or not old_target.active:
        old_target = anchor  # anchor was the only one left, so the pair target each other

    now = datetime.utcnow()
    newcomer.active = True
    newcomer.eliminated = False
    newcomer.score = 0
    newcomer.last_tag_at = None
    newcomer.score_last_updated = now
    newcomer.target_id = old_target.id
    anchor.target_id = newcomer.id

    if old_target.id == anchor.id:
        anchor_msg = f"A new player has joined the game. Your target is now '{newcomer.name}'."
    else:
        anchor_msg = f"Your target has changed: you are now after '{newcomer.name}' instead of '{old_target.name}'."
    _notify(db, admin, anchor, anchor_msg, now)
    _notify(db, admin, newcomer, f"You have been added to the game. Your target is '{old_target.name}'.", now)

    db.commit()
    db.refresh(newcomer)
    db.refresh(anchor)
    return newcomer, anchor


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def do_tag(db: Session, tagger: Player):
    if not tagger.active:
        if tagger.eliminated:
            raise ValueError("You have been eliminated and can't tag anyone")
        raise ValueError("You are not in the current game")
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
    target.eliminated = True
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
