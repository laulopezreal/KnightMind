"""What names a user's library already holds, and how to avoid landing on one.

Why this exists
---------------
``position_names.disambiguate`` makes a name unique against a ``used`` set, and
every caller built that set from the puzzles it happened to be looking at. That
is uniqueness *within one run*. A second run starts with an empty set, composes
"The f7 Knight Fork" again, and writes a second copy of it — which is exactly
how the live corpus ended up with 103 puzzles called "The Missed Win".

The fix is a real constraint (``uq_puzzle_stats_username_title``). But a
constraint alone converts a cosmetic collision into a failed INSERT, and the
INSERT that collides is the one that saves a freshly generated puzzle during a
game import. Losing a puzzle to a duplicate *name* is much worse than the
duplicate name. So this module is the other half: the DB-aware ``used`` set, so
callers pick a free name before they write rather than after they fail.

Deliberately tiny and deliberately not in ``position_names``: that module is
pure, total and offline by contract (it is what runs when nothing else can), and
it must not grow a Session argument. It is also not in ``identity``, which
``position_names`` imports — that direction is already load-bearing and adding
the reverse edge would close the cycle.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.api.models import PUZZLE_TITLE_UNIQUE_INDEX, PuzzleStats
from services.api.puzzles.position_names import disambiguate
from services.api.usernames import canonical_username


def taken_titles(
    db: Session, username: str, *, exclude_puzzle_id: str | None = None
) -> set[str]:
    """Every title this user's library already shows.

    One query per user rather than an existence check per candidate: a naming
    pass asks about hundreds of names in a row, and the whole answer for one
    user is a few hundred short strings.

    ``exclude_puzzle_id`` drops a row's own current title, for the caller that
    is about to overwrite it. Without it a re-run would see each puzzle's own
    name as taken and rename every puzzle away from itself, forever.
    """
    stmt = select(PuzzleStats.title).where(
        PuzzleStats.username == canonical_username(username),
        # NULL is not a name anyone can collide with — the unique index treats
        # NULLs as distinct, so untitled rows constrain nothing.
        PuzzleStats.title.is_not(None),
    )
    if exclude_puzzle_id is not None:
        stmt = stmt.where(PuzzleStats.puzzle_id != exclude_puzzle_id)
    return set(db.scalars(stmt))


def unique_title(
    db: Session,
    username: str,
    name: str,
    move_number: int | None = None,
    *,
    exclude_puzzle_id: str | None = None,
) -> str:
    """``name``, adjusted until it is free in this user's library.

    The convenience form for the single-row callers (a save, a first review).
    Callers naming a whole batch should hold their own set across the batch —
    re-reading per row would miss the names the batch itself just handed out.
    """
    return disambiguate(
        name,
        taken_titles(db, username, exclude_puzzle_id=exclude_puzzle_id),
        move_number,
    )


def is_duplicate_title(error: IntegrityError) -> bool:
    """True when ``error`` is the per-user title index rejecting a write.

    Callers use this to tell "somebody took this name between my read and my
    write" (retry with a different name) from every other integrity failure
    (a genuine duplicate puzzle, a missing FK — not ours to swallow). Naming
    the constraint is what makes that distinction possible at all; without it
    the only options are retry-everything or re-raise-everything, and both are
    wrong for one of the two cases.
    """
    orig = getattr(error, "orig", None)
    diag = getattr(orig, "diag", None)
    if getattr(diag, "constraint_name", None) == PUZZLE_TITLE_UNIQUE_INDEX:
        return True
    # A driver that exposes no ``diag`` (or a wrapped error) is not a reason to
    # lose the retry — the index name is in the message text either way. Kept
    # as a fallback, not the primary check, because message text is not API.
    return PUZZLE_TITLE_UNIQUE_INDEX in str(orig or "")
