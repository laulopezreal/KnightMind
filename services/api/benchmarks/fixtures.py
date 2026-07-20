"""Deterministic synthetic fixture generator for benchmarks.

Everything here is generated from a fixed seed with ``random.Random`` and
python-chess. NO production data is ever read or written. The synthetic games
are real, parseable PGNs (random-but-seeded legal moves) so the code under test
(tree builder, puzzle generator, PGN batch loader, Elo extraction) exercises the
same parsing/serialization work it would on real data.

Scales are expressed as a target game count; puzzle / review / session counts are
derived proportionally but capped so even the ``large`` scale stays practical.
"""

import hashlib
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import chess
import chess.pgn

from services.api.models import (
    Game,
    Puzzle,
    PuzzleReview,
    PuzzleStats,
    RatingSnapshot,
    TrainingSession,
)

# The single synthetic tenant every benchmark runs against.
BENCH_USERNAME = "bench_user"

# Plies per synthetic game. 30 half-moves ~ a short-but-realistic opening +
# middlegame; enough for the tree builder (max_ply up to ~20) and the generator's
# ply window (8..80) to have material to chew on, without making 20k games slow.
PLIES_PER_GAME = 30

# A fixed pool of tactical motifs so the motif-aggregation benchmarks group into
# a realistic handful of buckets rather than thousands of singletons.
_MOTIFS = [
    "fork",
    "pin",
    "skewer",
    "discovered_attack",
    "back_rank",
    "hanging_piece",
    "mate_in_2",
]

# Base epoch for synthetic end_time values; games count DOWN from here so game 0
# is the most recent (matches get_all_metadata's end_time-desc ordering).
_BASE_END_TIME = 1_700_000_000


@dataclass
class Fixtures:
    """Handle to a populated benchmark database plus in-memory PGN corpus."""

    username: str
    game_ids: list[str]
    pgns: list[str]  # in the same order as game_ids (for the tree builder)
    puzzle_ids: list[str]
    scale_name: str
    counts: dict[str, int]


@dataclass(frozen=True)
class Scale:
    """A named fixture size."""

    name: str
    games: int
    # Multipliers/caps derived from the game count.
    puzzles_per_game: float = 1.0
    reviews_per_puzzle: int = 3
    sessions: int = 60
    max_puzzles: int = 4000  # hard cap so aggregation stays bounded at large scale


def _game_id_from_url(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _random_pgn(
    rng: random.Random,
    white: str,
    black: str,
    result: str,
    white_elo: int,
    black_elo: int,
    plies: int = PLIES_PER_GAME,
) -> str:
    """Build one valid PGN of seeded random legal moves."""
    board = chess.Board()
    game = chess.pgn.Game()
    game.headers["Event"] = "KnightMind Benchmark"
    game.headers["White"] = white
    game.headers["Black"] = black
    game.headers["Result"] = result
    game.headers["WhiteElo"] = str(white_elo)
    game.headers["BlackElo"] = str(black_elo)
    game.headers["TimeControl"] = "600"

    node = game
    for _ in range(plies):
        legal = list(board.legal_moves)
        if not legal:
            break
        move = rng.choice(legal)
        board.push(move)
        node = node.add_variation(move)

    return str(game)


def _batched_commit(session, rows, chunk: int = 500) -> None:
    """Add rows in chunks with periodic flush+commit to bound memory."""
    for i in range(0, len(rows), chunk):
        session.add_all(rows[i : i + chunk])
        session.commit()


def generate(session_factory, scale: Scale, seed: int = 1234) -> Fixtures:
    """Populate a fresh benchmark DB deterministically and return a handle.

    Args:
        session_factory: a SQLAlchemy sessionmaker bound to the benchmark engine.
        scale: the target fixture size.
        seed: fixed RNG seed (do not vary between runs you want to compare).
    """
    rng = random.Random(seed)
    username = BENCH_USERNAME

    game_ids: list[str] = []
    pgns: list[str] = []
    game_rows: list[Game] = []
    results_pool = ["1-0", "0-1", "1/2-1/2"]

    for i in range(scale.games):
        # Alternate the tenant between White and Black so the tree builder counts
        # every game (it only includes games where the username is a player).
        user_is_white = i % 2 == 0
        opp = f"opp_{i % 512}"
        white = username if user_is_white else opp
        black = opp if user_is_white else username
        result = results_pool[i % 3]
        white_elo = rng.randint(1000, 2000)
        black_elo = rng.randint(1000, 2000)

        pgn = _random_pgn(rng, white, black, result, white_elo, black_elo)
        url = f"https://example.org/bench/{seed}/game/{i}"
        game_id = _game_id_from_url(url)

        # Map PGN result to per-player result strings the app expects.
        if result == "1-0":
            white_res, black_res = "win", "loss"
        elif result == "0-1":
            white_res, black_res = "loss", "win"
        else:
            white_res, black_res = "draw", "draw"

        game_rows.append(
            Game(
                game_id=game_id,
                url=url,
                username=username.lower(),
                white_username=white,
                black_username=black,
                white_result=white_res,
                black_result=black_res,
                time_control="600",  # classify_time_control("600") -> "rapid"
                end_time=_BASE_END_TIME - i,  # game 0 is most recent
                rated=True,
                pgn_blob=pgn,
                imported_at=datetime.now(timezone.utc),
            )
        )
        game_ids.append(game_id)
        pgns.append(pgn)

    with session_factory() as session:
        _batched_commit(session, game_rows)

    # --- Puzzles + stats + reviews for the aggregation benchmarks ---
    n_puzzles = min(scale.max_puzzles, int(scale.games * scale.puzzles_per_game))
    puzzle_rows: list[Puzzle] = []
    stats_rows: list[PuzzleStats] = []
    review_rows: list[PuzzleReview] = []
    puzzle_ids: list[str] = []
    now = datetime.now(timezone.utc)

    for p in range(n_puzzles):
        pid = f"bench-puzzle-{p:07d}"
        source_game_id = game_ids[p % len(game_ids)] if game_ids else None
        fen = chess.Board().fen()  # a valid FEN; content is irrelevant to aggregation
        puzzle_rows.append(
            Puzzle(
                id=pid,
                username=username.lower(),
                source_game_id=source_game_id,
                ply=8 + (p % 40),
                fen=fen,
                side_to_move="white" if p % 2 == 0 else "black",
                played_move_uci="e2e4",
                best_move_uci="d2d4",
                accept_moves_uci="d2d4",
                eval_before=1.5,
                eval_after=-1.0,
                swing=2.5,
                confirmed_depth=18,
                created_at=now - timedelta(days=p % 60),
            )
        )
        motif = _MOTIFS[p % len(_MOTIFS)]
        attempts = 1 + (p % 8)
        pass_count = p % (attempts + 1)
        fail_count = attempts - pass_count
        stats_rows.append(
            PuzzleStats(
                puzzle_id=pid,
                username=username.lower(),
                title=f"{motif.title()} #{p}",
                primary_motif=motif,
                attempts=attempts,
                pass_count=pass_count,
                fail_count=fail_count,
                last_reviewed_at=now - timedelta(hours=p % 240),
                last_result="pass" if pass_count > fail_count else "fail",
                next_due_at=now + timedelta(days=(p % 5) - 2),  # some due, some future
                interval_days=1 + (p % 10),
                ease_factor=2.0,
            )
        )
        # A handful of reviews per puzzle, spread across the window.
        for r in range(scale.reviews_per_puzzle):
            review_rows.append(
                PuzzleReview(
                    id=f"{pid}-r{r}",
                    puzzle_id=pid,
                    username=username.lower(),
                    reviewed_at=now - timedelta(days=(p + r) % 45, hours=r),
                    result="pass" if (p + r) % 3 else "fail",
                    time_spent_ms=1000 + (p % 5000),
                    verified=True,
                    source="server_verified",
                )
            )
        puzzle_ids.append(pid)

    # --- Training sessions (for streak / dashboard) ---
    session_rows: list[TrainingSession] = []
    for s in range(scale.sessions):
        # One completed session per day going back, so calculate_training_streak
        # walks a long consecutive run.
        day = now - timedelta(days=s)
        session_rows.append(
            TrainingSession(
                id=f"bench-session-{s:05d}",
                username=username.lower(),
                created_at=day,
                completed_at=day,
                requested_n=5,
                pass_count=3,
                fail_count=2,
                total_time_ms=120000,
            )
        )

    # --- Rating snapshots (for ratings-explain reference anchor) ---
    snapshot_rows: list[RatingSnapshot] = []
    for s in range(min(scale.sessions, 30)):
        snapshot_rows.append(
            RatingSnapshot(
                id=f"bench-snap-{s:05d}",
                username=username.lower(),
                source="chesscom",
                time_control="rapid",
                rating=1400 + (s % 100),
                recorded_at=now - timedelta(days=s),
            )
        )

    with session_factory() as session:
        _batched_commit(session, puzzle_rows)
        _batched_commit(session, stats_rows)
        _batched_commit(session, review_rows)
        _batched_commit(session, session_rows)
        _batched_commit(session, snapshot_rows)

    counts = {
        "games": len(game_rows),
        "puzzles": len(puzzle_rows),
        "puzzle_stats": len(stats_rows),
        "reviews": len(review_rows),
        "sessions": len(session_rows),
        "rating_snapshots": len(snapshot_rows),
    }

    return Fixtures(
        username=username,
        game_ids=game_ids,
        pgns=pgns,
        puzzle_ids=puzzle_ids,
        scale_name=scale.name,
        counts=counts,
    )
