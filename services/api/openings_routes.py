"""The opening tree and the peer-baseline comparison.

Second slice of the main.py split (see services/api/ratings.py for the first).
Named ``openings_routes`` because ``services/api/openings/`` is already the
package holding the tree builder, the ECO table and the explorer client -- the
same reason auth_routes sits beside auth.

/openings is the endpoint with the most performance history in this codebase: it
re-parses every stored PGN for a user on a cache miss, which is why the ECO table
is warmed at startup and why the depth-based min_games floor exists.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

import chess
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.db import get_db
from services.api.identity import assert_owns_username, require_account
from services.api.models import Account, RatingSnapshot
from services.api.openings import OpeningTreeBuilder, min_games_floor
from services.api.openings import make_key as make_openings_cache_key
from services.api.openings import tree_cache as openings_tree_cache
from services.api.openings.explorer import (
    ExplorerUnavailable,
    band_for_rating,
)
from services.api.openings.explorer import cache_key as explorer_cache_key
from services.api.openings.explorer import fetch_stats as fetch_explorer_stats
from services.api.ratelimit import rate_limit
from services.api.storage import GameRepository
from services.api.storage.explorer_repository import ExplorerRepository
from services.api.usernames import Username

logger = logging.getLogger(__name__)

router = APIRouter(tags=["openings"])

# Outbound lichess call on a miss, but the cache is shared across users and
# positions repeat heavily, so most selections never leave the box.
RATE_LIMIT_OPENINGS_BASELINE = 60


@router.get("/openings")
def get_openings(
    username: Annotated[
        Username, Query(description="Username to build opening tree for")
    ],
    color: Literal["white", "black", "both"] = Query(
        "both", description="Filter by player's color"
    ),
    max_ply: int = Query(
        12, ge=1, le=40, description="Maximum number of half-moves to include"
    ),
    min_games: int = Query(
        1,
        ge=1,
        le=100,
        description=(
            "Omit lines played fewer than this many times. At depth, one-off "
            "tails dominate the tree (96% of a measured 40-ply tree) and are "
            "noise rather than repertoire."
        ),
    ),
    since_days: int | None = Query(
        None,
        ge=1,
        le=3650,
        description=(
            "Only include games finished within this many days. Omit for the "
            "whole archive. A repertoire is a moving target: a line fixed in "
            "April still reads as a weakness while two years of losses in it "
            "are pooled with last week's wins."
        ),
    ),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    Get the opening tree for a user's games.

    Builds a tree structure from the user's stored PGN games showing:
    - move_san: The move in Standard Algebraic Notation
    - ply: Half-move number (1 = white's first, 2 = black's first, etc.)
    - games_count: Number of games reaching this position
    - wins/draws/losses: Results from the player's perspective
    - win_rate: Score percentage (wins + 0.5*draws) / games
    - children: Subsequent moves played from this position
    - analysis: How many stored games actually reached the tree, and why the
      rest didn't (colour filter vs. unreadable/not-the-player/unfinished)

    Args:
        username: The username to build the tree for (must have imported games)
        color: Filter games by the player's color ("white", "black", or "both")
        max_ply: Maximum depth in half-moves (default 12 = 6 full moves each side)

    Returns:
        Opening tree as nested JSON structure
    """
    assert_owns_username(account, username, db)
    game_repository = GameRepository(db)

    # Check if user has any games
    game_count = game_repository.get_game_count(username)
    if game_count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}'. Import games first using POST /import/chesscom",
        )

    # Rebuilding re-parses every stored PGN, and the client refetches on mount
    # and on every colour-filter change. The key folds in the game count and the
    # newest game's timestamp, so an import invalidates this by construction.
    # The requested floor is a hint; the depth-based floor is a cost control and
    # wins. Without it `?max_ply=40&min_games=1` still builds and caches the
    # multi-megabyte tree for anyone who asks, which is exactly what the client
    # table was meant to prevent.
    applied_min_games = max(min_games, min_games_floor(max_ply))

    # Resolved once and reused for the key, the filter and the reported window,
    # so the three cannot disagree about where the boundary fell.
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=since_days)
        if since_days is not None
        else None
    )

    cache_key = make_openings_cache_key(
        username=username,
        color=color,
        max_ply=max_ply,
        game_count=game_count,
        latest_game_time=game_repository.get_latest_game_time(username),
        min_games=applied_min_games,
        since=cutoff.date().isoformat() if cutoff else "all",
    )
    cached = openings_tree_cache.get(cache_key)
    if cached is not None:
        return cached

    # Stream all PGNs for the user in bulk batches (one query per batch)
    # instead of one query per game, without holding every blob in memory.
    metadata_list = game_repository.get_all_metadata(username)
    # Filtered here rather than in the query: the count of what the window left
    # out is needed to tell "you have played nothing lately" apart from "you
    # have imported nothing", and those want different things said to them.
    excluded_by_date = 0
    if cutoff is not None:
        cutoff_epoch = int(cutoff.timestamp())
        in_window = [m for m in metadata_list if m.end_time >= cutoff_epoch]
        excluded_by_date = len(metadata_list) - len(in_window)
        metadata_list = in_window
    game_ids = [meta.game_id for meta in metadata_list]
    pgn_count = 0

    # Build the opening tree. The builder (rather than the build_opening_tree
    # convenience wrapper) is used directly so its per-game report survives.
    builder = OpeningTreeBuilder(max_ply=max_ply)
    for pgn in game_repository.iter_pgns(username, game_ids):
        pgn_count += 1
        builder.add_game(pgn, username, color)

    if metadata_list and pgn_count == 0:
        raise HTTPException(
            status_code=503,
            detail="Games found but PGN content is missing. Re-import games to populate PGN data.",
        )

    tree = builder.build_tree(min_games=applied_min_games)
    # Attached to the root node rather than wrapping the response so existing
    # clients keep reading the tree at the top level. `games_stored` comes from
    # the repository, so a tree built from a fraction of a user's games is
    # reportable instead of silently looking complete.
    tree["analysis"] = {
        "games_stored": game_count,
        # Reported alongside `excluded_by_color`, and for the same reason: the
        # user asked for this, so it is a fact to state rather than data loss
        # to warn about.
        "excluded_by_date": excluded_by_date,
        "since_days": since_days,
        # Surfaced rather than applied silently: the client states the filter so
        # a thinner tree reads as a deliberate choice, not missing data.
        "min_games": applied_min_games,
        **builder.report.to_dict(),
    }
    # Stored only once the response is fully composed: the cache hands values
    # back by reference, so anything mutated after this point would corrupt
    # every later hit.
    openings_tree_cache.put(cache_key, tree)
    return tree


class BaselineBand(BaseModel):
    low: int
    high: int | None
    label: str


class OpeningBaselineResponse(BaseModel):
    """What players around this rating score from this position."""

    games: int
    # None when the sample is too thin to say anything. Distinct from 0, which
    # would read as "they score nothing here".
    expected_score: float | None
    # None when the user has no imported rating, in which case the figures are
    # over all ratings and the client must say so.
    band: BaselineBand | None
    source: str = "lichess"


def _latest_rating(db: Session, username: str) -> int | None:
    """The user's most recent rating, preferring the pools the baseline covers.

    Rapid first, then blitz: the explorer query excludes bullet, so a bullet
    rating would place the user in a band the comparison is not drawn from.
    """
    for time_control in ("rapid", "blitz"):
        snapshot = db.execute(
            select(RatingSnapshot)
            .where(
                RatingSnapshot.username == username,
                RatingSnapshot.time_control == time_control,
            )
            .order_by(RatingSnapshot.recorded_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if snapshot is not None:
            return snapshot.rating
    return None


@router.get(
    "/openings/baseline",
    response_model=OpeningBaselineResponse,
    dependencies=[
        Depends(
            rate_limit("openings_baseline", default_limit=RATE_LIMIT_OPENINGS_BASELINE)
        )
    ],
)
async def get_opening_baseline(
    username: Annotated[Username, Query(description="Whose rating sets the band")],
    fen: Annotated[str, Query(max_length=120, description="Position to look up")],
    color: Literal["white", "black"] = Query(
        ..., description="Whose score to report — the side the player had"
    ),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """How players around this user's rating score from a position.

    A line's own score says how the user did; it cannot say whether that was
    good. This supplies the missing half.

    Only ``white`` and ``black`` are accepted, deliberately. Under a "both"
    filter the user's own figure already mixes games from either side of the
    board, so there is no single expectation to compare it against and any
    answer would be a fabrication.
    """
    assert_owns_username(account, username, db)

    # Normalise to an EPD before it is used as a key or sent upstream: this
    # validates the position, and it drops the move counters so two routes into
    # the same position share one cache row and one lookup.
    try:
        epd = chess.Board(fen).epd()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Not a valid position.") from exc

    band = band_for_rating(_latest_rating(db, username))
    key = explorer_cache_key(epd, band)
    repository = ExplorerRepository(db)

    stats = repository.get_fresh(key)
    if stats is None:
        # Hand the pooled connection back before waiting on lichess. Everything
        # above was a read, and SQLAlchemy holds a connection from the first
        # query until the transaction ends — so without this each in-flight
        # miss pins one for the length of an outbound call. The pool is 15
        # deep and this route fires on every line a user selects, so a slow
        # explorer would starve every other endpoint of connections.
        db.rollback()
        try:
            stats = await fetch_explorer_stats(epd, band)
        except ExplorerUnavailable as exc:
            # A stale row beats no answer: these aggregates move at the speed of
            # millions of games, so month-old numbers are still true enough to
            # judge a line by, and the alternative is the baseline blinking out
            # whenever lichess has a bad minute.
            cached = repository.get(key)
            if cached is None:
                logger.info("openings baseline unavailable: %s", exc)
                raise HTTPException(
                    status_code=503, detail="Baseline unavailable right now."
                ) from exc
            stats = cached.stats
        else:
            repository.put(key, epd, stats)

    return OpeningBaselineResponse(
        games=stats.games,
        expected_score=stats.expected_score(color),
        band=(
            BaselineBand(low=band.low, high=band.high, label=band.label)
            if band
            else None
        ),
    )
