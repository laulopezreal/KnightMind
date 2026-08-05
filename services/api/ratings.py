"""Rating snapshots, history, and the rating-drivers explainer.

Extracted from main.py, which had grown to 4,188 lines: ~40 route handlers, ~60
Pydantic models and their helpers interleaved with no ordering, so every backend
change touched the same file and every concurrent backend change conflicted in
it. The router split already existed for ops, sessions, dashboard and auth; this
follows it.

Everything here is rating-domain: the Elo maths, the PGN Elo-header extraction
Chess.com writes per game, the thresholds that decide when a difference is worth
reporting as a driver, and the three /ratings routes.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.analytics_confidence import (
    MIN_GAMES_FOR_RATING_DRIVERS,
    rating_confidence,
)
from services.api.db import get_db
from services.api.identity import assert_owns_username, require_account
from services.api.models import Account, RatingSnapshot, TrainingSession
from services.api.ratelimit import rate_limit
from services.api.ratings_auto import auto_snapshot_throttled
from services.api.storage import GameRepository
from services.api.time_control import classify_time_control
from services.api.usernames import Username, canonical_username
from services.ingest import NetworkError, UserNotFoundError, get_player_stats

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ratings"])

# Per-principal rate limit (audit gate 10); see services/api/ratelimit.py.
RATE_LIMIT_RATINGS_SNAPSHOT = 10  # outbound Chess.com call per request

# Rating explain thresholds
PERFORMANCE_DIFF_THRESHOLD = 0.5
RATING_DIFFERENCE_THRESHOLD = 100
SIGNIFICANT_WINS_VS_HIGHER_THRESHOLD = 2
SIGNIFICANT_LOSSES_VS_LOWER_THRESHOLD = 2
OPPONENT_RATING_STD_DEV_THRESHOLD = 150

# Chess.com draw result values
DRAW_RESULTS = frozenset(
    [
        "repetition",
        "agreed",
        "timevsinsufficient",
        "stalemate",
        "insufficient",
        "50move",
    ]
)


def _rating_from_pgn(pgn: str, tag: str) -> int | None:
    """Extract a numeric Elo header (WhiteElo/BlackElo) from PGN headers."""
    match = re.search(f'\\[{tag} "(\\d+)"\\]', pgn)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def get_opponent_rating_from_pgn(pgn: str, user_is_white: bool) -> int | None:
    """Extract opponent rating from PGN headers."""
    return _rating_from_pgn(pgn, "BlackElo" if user_is_white else "WhiteElo")


def get_player_rating_from_pgn(pgn: str, user_is_white: bool) -> int | None:
    """Extract the player's own rating from PGN headers.

    Chess.com writes each side's post-game rating into the Elo headers, so
    across a window of games these values form the player's actual rating
    trajectory — no manual snapshots required.
    """
    return _rating_from_pgn(pgn, "WhiteElo" if user_is_white else "BlackElo")


def calculate_expected_score(player_rating: int, opponent_rating: int) -> float:
    """Calculates the expected score for a player based on Elo ratings."""
    return 1 / (1 + 10 ** ((opponent_rating - player_rating) / 400))


class SnapshotRequest(BaseModel):
    username: Username
    time_control: Literal["rapid", "blitz", "bullet"]


class SnapshotResponse(BaseModel):
    rating: int
    recorded_at: datetime


@router.post(
    "/ratings/snapshot",
    response_model=SnapshotResponse,
    dependencies=[
        Depends(
            rate_limit("ratings_snapshot", default_limit=RATE_LIMIT_RATINGS_SNAPSHOT)
        )
    ],
)
async def create_rating_snapshot(
    request: SnapshotRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Fetch current rating from Chess.com and store a snapshot."""
    assert_owns_username(account, request.username, db)
    try:
        stats = await get_player_stats(request.username)

        # Parse rating: { "chess_rapid": { "last": { "rating": ... } } }
        tc_key = f"chess_{request.time_control}"
        if not (rating := stats.get(tc_key, {}).get("last", {}).get("rating")):
            raise HTTPException(
                status_code=502,
                detail=f"Could not find rating for {request.time_control} in Chess.com response",
            )

        # Same-rating dedupe, mirroring ratings_auto.auto_snapshot: a repeat
        # call with an unchanged rating answers from the stored row instead of
        # writing a duplicate flat entry into the history the chart reads.
        latest_stmt = (
            select(RatingSnapshot)
            .where(
                RatingSnapshot.username == request.username,
                RatingSnapshot.time_control == request.time_control,
            )
            .order_by(RatingSnapshot.recorded_at.desc())
            .limit(1)
        )
        latest = db.scalars(latest_stmt).first()
        if latest and latest.rating == rating:
            return SnapshotResponse(
                rating=latest.rating, recorded_at=latest.recorded_at
            )

        snapshot = RatingSnapshot(
            username=request.username,
            source="chesscom",
            time_control=request.time_control,
            rating=rating,
            recorded_at=datetime.now(timezone.utc),
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)

        return SnapshotResponse(
            rating=snapshot.rating, recorded_at=snapshot.recorded_at
        )

    except (UserNotFoundError, NetworkError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except HTTPException:
        # Domain-specific HTTPExceptions (e.g. the 502 above) are already safe.
        raise
    except Exception as e:
        # Unexpected failure: log the real error server-side but return a generic
        # message so raw exception/DB text (connection strings, SQL, etc.) never
        # reaches the caller (dim 23).
        logger.exception("Unexpected error creating rating snapshot")
        raise HTTPException(status_code=500, detail="Internal server error") from e


class SnapshotHistoryItem(BaseModel):
    rating: int
    recorded_at: datetime


@router.get("/ratings/history", response_model=list[SnapshotHistoryItem])
def get_rating_history(
    username: Username,
    time_control: str = "rapid",
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Return chronological rating snapshot history for charting.

    Fetches the most recent `limit` snapshots (desc) then reverses to
    chronological order so the frontend chart always shows the latest window.
    """
    assert_owns_username(account, username, db)
    stmt = (
        select(RatingSnapshot)
        .where(
            RatingSnapshot.username == username,
            RatingSnapshot.time_control == time_control,
        )
        .order_by(RatingSnapshot.recorded_at.desc())
        .limit(limit)
    )
    snapshots = list(reversed(db.scalars(stmt).all()))
    return [
        SnapshotHistoryItem(rating=s.rating, recorded_at=s.recorded_at)
        for s in snapshots
    ]


class HighlightGame(BaseModel):
    opponent_rating: int | None
    opponent_username: str | None = None
    result: str
    expected_score: float
    rating_diff: int | None
    game_id: str
    played_at: datetime
    url: str


class Highlights(BaseModel):
    best_surprises: list[HighlightGame]
    worst_surprises: list[HighlightGame]


class RatingWindow(BaseModel):
    start: datetime
    end: datetime
    source: str


class RatingInfo(BaseModel):
    start: int | None
    end: int | None
    net_change: int | None
    # True when start/end were estimated from the player's own per-game Elo
    # headers (post-game ratings) rather than recorded snapshots. Kept for
    # older clients; new clients should read the per-anchor flags below.
    is_estimated: bool = False
    start_is_estimated: bool = False
    end_is_estimated: bool = False
    reference_rating: int
    reference_is_approx: bool


class TrajectoryPoint(BaseModel):
    played_at: datetime
    rating: int


class ChartPoint(BaseModel):
    at: datetime
    rating: int
    source: Literal["game", "snapshot"]


class DriverStats(BaseModel):
    games: int
    wins: int
    draws: int
    losses: int
    avg_opponent_rating: int | None
    expected_total: float | None
    actual_total: float | None
    actual_minus_expected: float | None
    missing_opponent_rating_games: int
    # In-window games of this time control skipped because they were casual
    # (unrated) — casual games never move the Chess.com rating, so counting
    # them would corrupt the attribution.
    casual_games_excluded: int = 0


class Driver(BaseModel):
    text: str
    severity: Literal["major", "moderate", "minor"]
    direction: Literal["up", "down", "neutral"]


class ExplainResponse(BaseModel):
    time_control: str
    window: RatingWindow
    rating: RatingInfo
    stats: DriverStats
    drivers: list[Driver]
    highlights: Highlights
    # Player's own rating over the window, from per-game PGN Elo headers
    # (chronological). Lets the frontend chart real rating movement without
    # manual snapshots. Kept for older clients; chart_series supersedes it.
    trajectory: list[TrajectoryPoint] = []
    # Chart-ready fusion of per-game Elo points and the snapshot anchors that
    # won the start/end contests, in time order. Its endpoints always match
    # rating.start/rating.end, so clients must render this instead of picking
    # a source themselves. Empty when the window has no game points (clients
    # fall back to recorded snapshot history).
    chart_series: list[ChartPoint] = []
    # Canonical uncertainty signal (rated games in window). Drivers are
    # descriptive, not causal; below MIN_GAMES_FOR_RATING_DRIVERS no directional
    # driver is emitted and insufficient_data is True.
    confidence: Literal["low", "medium", "high"]
    insufficient_data: bool


@router.get("/ratings/explain", response_model=ExplainResponse)
async def explain_rating_changes(
    username: Username,
    time_control: str = "rapid",
    since_session_id: str | None = None,
    since: datetime | None = None,
    limit_games: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Explain rating drivers based on recent games."""

    assert_owns_username(account, username, db)

    # Viewing insights is itself a reason to refresh the rating record:
    # snapshot opportunistically (throttled per username, best-effort) so the
    # end anchor stays fresh without the user ever pressing a button.
    await auto_snapshot_throttled(username, db)

    # 1. Determine Window
    now = datetime.now(timezone.utc)
    window_start: datetime
    source_type: str

    if since_session_id:
        session = db.get(TrainingSession, since_session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        # Don't leak another tenant's session window via a guessed id: 404.
        assert_owns_username(account, session.username, db, status_code=404)
        window_start = session.created_at.replace(tzinfo=timezone.utc)
        source_type = "session"
    elif since:
        window_start = since
        source_type = "since"
    else:
        # Fallback: Last session or 7 days
        stmt = (
            select(TrainingSession)
            .where(TrainingSession.username == username)
            .order_by(TrainingSession.created_at.desc())
        )
        last_session = db.scalars(stmt).first()
        if last_session:
            window_start = last_session.created_at.replace(tzinfo=timezone.utc)
            source_type = "last_session"
        else:
            window_start = now - timedelta(days=7)
            source_type = "fallback_7d"

    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=timezone.utc)

    # 2. Load Games
    game_repository = GameRepository(db)
    all_metadata = game_repository.get_all_metadata(username)

    start_ts = int(window_start.timestamp())
    relevant_games = []
    casual_excluded = 0

    count = 0
    for meta in all_metadata:
        if count >= limit_games:
            break
        if classify_time_control(meta.time_control) != time_control.lower():
            continue
        if meta.end_time < start_ts:
            break
        # Casual games never move the Chess.com rating: excluding them keeps
        # wins/losses, expected-score totals, and drivers about rated play only.
        if not meta.rated:
            casual_excluded += 1
            continue

        relevant_games.append(meta)
        count += 1

    relevant_games.reverse()

    # 3. Process Games
    wins = 0
    draws = 0
    losses = 0
    total_opp_rating = 0
    opp_rating_count = 0
    missing_ratings = 0

    game_details = []
    opp_ratings = []

    # Snapshot closest to (but before or at) window start — best reference anchor
    stmt = (
        select(RatingSnapshot)
        .where(
            RatingSnapshot.username == username,
            RatingSnapshot.time_control == time_control,
            RatingSnapshot.recorded_at <= window_start,
        )
        .order_by(RatingSnapshot.recorded_at.desc())
    )
    pre_window_snapshot = db.scalars(stmt).first()

    # Earliest snapshot inside the window
    stmt = (
        select(RatingSnapshot)
        .where(
            RatingSnapshot.username == username,
            RatingSnapshot.time_control == time_control,
            RatingSnapshot.recorded_at >= window_start,
        )
        .order_by(RatingSnapshot.recorded_at.asc())
    )
    earliest_snapshot = db.scalars(stmt).first()

    reference_rating = 0
    reference_is_approx = False

    if pre_window_snapshot:
        reference_rating = pre_window_snapshot.rating
    elif earliest_snapshot:
        reference_rating = earliest_snapshot.rating

    # Bulk-load PGNs for the selected window in one query (the window is
    # bounded by limit_games) instead of one query per game.
    pgns_by_game_id = game_repository.get_pgns(
        username, [meta.game_id for meta in relevant_games]
    )

    for meta in relevant_games:
        pgn = pgns_by_game_id.get(meta.game_id)
        if not pgn:
            continue

        # A comparison, not a storage key: ``white_username`` is whatever
        # Chess.com put in the game record, so it is folded rather than trusted.
        # Both sides must use the SAME fold or the match silently fails —
        # ``username`` is already canonical (folded at the request boundary), so
        # folding the header with anything else would reintroduce the mismatch.
        user_is_white = canonical_username(meta.white_username) == username

        result_score = 0.0
        if user_is_white:
            if meta.white_result == "win":
                result_score = 1.0
            elif meta.white_result in DRAW_RESULTS:
                result_score = 0.5
        else:
            if meta.black_result == "win":
                result_score = 1.0
            elif meta.black_result in DRAW_RESULTS:
                result_score = 0.5

        if result_score == 1.0:
            wins += 1
        elif result_score == 0.5:
            draws += 1
        else:
            losses += 1

        opp_rating = get_opponent_rating_from_pgn(pgn, user_is_white)
        player_rating = get_player_rating_from_pgn(pgn, user_is_white)

        if opp_rating is None:
            missing_ratings += 1
        else:
            total_opp_rating += opp_rating
            opp_rating_count += 1
            opp_ratings.append(opp_rating)

        game_details.append(
            {
                "meta": meta,
                "opp_rating": opp_rating,
                # Player's own post-game Elo from the PGN — the most accurate
                # per-game reference for expected-score math.
                "player_rating": player_rating,
                "opponent_username": (
                    meta.black_username if user_is_white else meta.white_username
                ),
                "actual": result_score,
            }
        )

    player_ratings = [
        g["player_rating"] for g in game_details if g["player_rating"] is not None
    ]

    if reference_rating == 0:
        if player_ratings:
            # The player's own Elo headers beat any proxy. Averaging opponent
            # ratings instead would force expected scores toward 0.5 by
            # construction (you are your opponents' average only by accident).
            reference_rating = int(sum(player_ratings) / len(player_ratings))
        elif opp_rating_count > 0:
            reference_rating = int(total_opp_rating / opp_rating_count)
        else:
            reference_rating = 1200
        reference_is_approx = True

    # One owner for the per-game self-rating fallback: the player's own Elo at
    # that game when the PGN has it, else the window reference. Used by the
    # expected-score math and both vs-higher/vs-lower driver counts.
    for g in game_details:
        g["r_self"] = g["player_rating"] or reference_rating

    expected_total = 0.0
    actual_total_rated = 0.0
    # (surprise value, game) pairs — ranked on the unrounded value so ties
    # aren't manufactured by the 2-decimal display rounding.
    surprises: list[tuple[float, HighlightGame]] = []

    for item in game_details:
        if item["opp_rating"] is not None:
            r_opp = item["opp_rating"]
            # Prefer the player's own Elo at that game over the single
            # window-wide reference: expected score then reflects the actual
            # matchup, not an anchor that may be days stale.
            r_self = item["r_self"]
            expected = calculate_expected_score(r_self, r_opp)

            expected_total += expected
            actual_total_rated += item["actual"]

            surprises.append(
                (
                    item["actual"] - expected,
                    HighlightGame(
                        opponent_rating=r_opp,
                        opponent_username=item["opponent_username"],
                        result=(
                            "Win"
                            if item["actual"] == 1.0
                            else "Draw" if item["actual"] == 0.5 else "Loss"
                        ),
                        expected_score=round(expected, 2),
                        rating_diff=r_opp - r_self,
                        game_id=item["meta"].game_id,
                        played_at=datetime.fromtimestamp(
                            item["meta"].end_time, tz=timezone.utc
                        ),
                        url=item["meta"].url,
                    ),
                )
            )

    avg_opp = int(total_opp_rating / opp_rating_count) if opp_rating_count > 0 else None

    # Canonical uncertainty signal from the rated-game sample. Below the driver
    # threshold we must not present a small delta as a confident, causal trend.
    confidence = rating_confidence(opp_rating_count)
    insufficient_data = opp_rating_count < MIN_GAMES_FOR_RATING_DRIVERS

    drivers: list[Driver] = []
    diff = actual_total_rated - expected_total

    if insufficient_data:
        # Too few rated games to attribute drivers; stay descriptive + neutral.
        if opp_rating_count > 0:
            drivers.append(
                Driver(
                    text=(
                        f"Only {opp_rating_count} rated "
                        f"{time_control.lower()} game"
                        f"{'s' if opp_rating_count != 1 else ''} in this window — "
                        "not enough to explain rating changes confidently."
                    ),
                    severity="minor",
                    direction="neutral",
                )
            )
    elif diff > PERFORMANCE_DIFF_THRESHOLD:
        severity = (
            "major" if abs(diff) > 2.0 else "moderate" if abs(diff) > 1.0 else "minor"
        )
        drivers.append(
            Driver(
                text=(
                    f"You outperformed expectations by {diff:+.1f} points "
                    f"over {opp_rating_count} rated games (upward pressure)."
                ),
                severity=severity,
                direction="up",
            )
        )
    elif diff < -PERFORMANCE_DIFF_THRESHOLD:
        severity = (
            "major" if abs(diff) > 2.0 else "moderate" if abs(diff) > 1.0 else "minor"
        )
        drivers.append(
            Driver(
                text=(
                    f"You underperformed expectations by {diff:+.1f} points "
                    f"over {opp_rating_count} rated games (downward pressure)."
                ),
                severity=severity,
                direction="down",
            )
        )

    # Attribution drivers below require a sufficient rated sample.
    if not insufficient_data:
        wins_vs_higher = sum(
            1
            for g in game_details
            if g["opp_rating"]
            and g["opp_rating"] >= g["r_self"] + RATING_DIFFERENCE_THRESHOLD
            and g["actual"] == 1.0
        )
        if wins_vs_higher >= SIGNIFICANT_WINS_VS_HIGHER_THRESHOLD:
            drivers.append(
                Driver(
                    text=f"{wins_vs_higher} wins against higher-rated opponents likely offset losses.",
                    severity="moderate" if wins_vs_higher >= 4 else "minor",
                    direction="up",
                )
            )

        losses_vs_lower = sum(
            1
            for g in game_details
            if g["opp_rating"]
            and g["opp_rating"] <= g["r_self"] - RATING_DIFFERENCE_THRESHOLD
            and g["actual"] == 0.0
        )
        if losses_vs_lower >= SIGNIFICANT_LOSSES_VS_LOWER_THRESHOLD:
            drivers.append(
                Driver(
                    text=f"{losses_vs_lower} losses against lower-rated opponents likely drove most of the drop.",
                    severity="moderate" if losses_vs_lower >= 4 else "minor",
                    direction="down",
                )
            )

        if len(opp_ratings) >= 5:
            variance = sum((x - avg_opp) ** 2 for x in opp_ratings) / len(opp_ratings)
            std_dev = variance**0.5
            if std_dev > OPPONENT_RATING_STD_DEV_THRESHOLD:
                drivers.append(
                    Driver(
                        text="Wide opponent rating range increased volatility.",
                        severity="minor",
                        direction="neutral",
                    )
                )

    # Sort drivers by severity (major first)
    severity_order = {"major": 0, "moderate": 1, "minor": 2}
    drivers.sort(key=lambda d: severity_order[d.severity])

    surprises.sort(key=lambda pair: pair[0], reverse=True)
    best_surprises = [g for val, g in surprises if val > 0][:3]
    worst_surprises = [g for val, g in surprises if val < 0][-3:]
    worst_surprises.reverse()

    # Player's own rating over the window from PGN Elo headers (game_details is
    # chronological). Powers the chart + start/end estimates below.
    trajectory = [
        TrajectoryPoint(
            played_at=datetime.fromtimestamp(g["meta"].end_time, tz=timezone.utc),
            rating=g["player_rating"],
        )
        for g in game_details
        if g["player_rating"] is not None
    ]

    def _snapshot_at(snapshot: RatingSnapshot) -> datetime:
        at = snapshot.recorded_at
        return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at

    # Start: prefer the pre-window snapshot (always the earliest evidence),
    # then the earliest in-window snapshot — but only if no game finished
    # before it (a snapshot recorded after games began is not the window's
    # starting rating) — then the first game's own Elo (estimated: it's the
    # rating *after* that game, so the first game's delta is not captured).
    start_is_estimated = False
    start_anchor_snapshot = None
    if pre_window_snapshot is not None:
        start_anchor_snapshot = pre_window_snapshot
    elif earliest_snapshot is not None and (
        not trajectory or _snapshot_at(earliest_snapshot) <= trajectory[0].played_at
    ):
        start_anchor_snapshot = earliest_snapshot

    if start_anchor_snapshot is not None:
        start_rating_val = start_anchor_snapshot.rating
    elif trajectory:
        start_rating_val = trajectory[0].rating
        start_is_estimated = True
    else:
        start_rating_val = (
            earliest_snapshot.rating if earliest_snapshot is not None else None
        )

    # End: latest snapshot within the window period
    stmt = (
        select(RatingSnapshot)
        .where(
            RatingSnapshot.username == username,
            RatingSnapshot.time_control == time_control,
            RatingSnapshot.recorded_at >= window_start,
        )
        .order_by(RatingSnapshot.recorded_at.desc())
    )
    latest_in_window = db.scalars(stmt).first()

    # Use whichever evidence is fresher: an in-window snapshot or the last
    # game's own Elo. A snapshot recorded before the last game is stale.
    end_is_estimated = False
    end_anchor_snapshot = None
    if latest_in_window is not None and (
        not trajectory or _snapshot_at(latest_in_window) >= trajectory[-1].played_at
    ):
        end_rating_val = latest_in_window.rating
        end_anchor_snapshot = latest_in_window
    elif trajectory:
        end_rating_val = trajectory[-1].rating
        end_is_estimated = True
    else:
        end_rating_val = None

    net_change = None
    if start_rating_val is not None and end_rating_val is not None:
        net_change = end_rating_val - start_rating_val

    # Fused, chart-ready series: game points plus the snapshot anchors chosen
    # above, time-ordered. Built from the same anchor decisions as the card so
    # the line's endpoints always equal rating.start/rating.end. Only emitted
    # when the window has game points — with no games there is nothing to
    # fuse, and clients chart their recorded snapshot history instead.
    chart_series: list[ChartPoint] = []
    if trajectory:
        if start_anchor_snapshot is not None:
            chart_series.append(
                ChartPoint(
                    at=_snapshot_at(start_anchor_snapshot),
                    rating=start_anchor_snapshot.rating,
                    source="snapshot",
                )
            )
        chart_series.extend(
            ChartPoint(at=p.played_at, rating=p.rating, source="game")
            for p in trajectory
        )
        # Append even when the same snapshot also won the start contest (a
        # timestamp tie with every game): dropping it would leave the last
        # game's Elo as the series endpoint while the card shows the snapshot
        # rating. A duplicate point at the same timestamp is harmless.
        if end_anchor_snapshot is not None:
            chart_series.append(
                ChartPoint(
                    at=_snapshot_at(end_anchor_snapshot),
                    rating=end_anchor_snapshot.rating,
                    source="snapshot",
                )
            )
        # Stable sort: on equal timestamps the start anchor stays first and
        # the end anchor stays last, preserving endpoint agreement.
        chart_series.sort(key=lambda p: p.at)
        # Self-check the contract clients rely on instead of trusting
        # construction: a divergence here means a card/chart mismatch shipped.
        if chart_series and (
            chart_series[0].rating != start_rating_val
            or chart_series[-1].rating != end_rating_val
        ):
            logger.warning(
                "chart_series endpoints diverge from rating anchors: "
                "series %s..%s vs start=%s end=%s (username=%s tc=%s)",
                chart_series[0].rating,
                chart_series[-1].rating,
                start_rating_val,
                end_rating_val,
                username,
                time_control,
            )

    return ExplainResponse(
        time_control=time_control,
        window=RatingWindow(start=window_start, end=now, source=source_type),
        rating=RatingInfo(
            start=start_rating_val,
            end=end_rating_val,
            net_change=net_change,
            is_estimated=start_is_estimated or end_is_estimated,
            start_is_estimated=start_is_estimated,
            end_is_estimated=end_is_estimated,
            reference_rating=reference_rating,
            reference_is_approx=reference_is_approx,
        ),
        stats=DriverStats(
            games=len(relevant_games),
            wins=wins,
            draws=draws,
            losses=losses,
            avg_opponent_rating=avg_opp,
            expected_total=expected_total if opp_rating_count > 0 else None,
            actual_total=actual_total_rated if opp_rating_count > 0 else None,
            actual_minus_expected=diff if opp_rating_count > 0 else None,
            missing_opponent_rating_games=missing_ratings,
            casual_games_excluded=casual_excluded,
        ),
        drivers=drivers,
        highlights=Highlights(
            best_surprises=best_surprises, worst_surprises=worst_surprises
        ),
        trajectory=trajectory,
        chart_series=chart_series,
        confidence=confidence,
        insufficient_data=insufficient_data,
    )
