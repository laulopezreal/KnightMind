"""Builders for valid test rows.

Test-only, and importable (rather than fixtures) because the seeding helpers
that need it are plain module-level functions taking a session.

Exists because the schema has real foreign keys and the fixtures kept ignoring
them: puzzle_reviews.puzzle_id and puzzle_stats.puzzle_id reference puzzles,
and puzzles(source_game_id, username) references games. SQLite did not enforce
any of that, so modules independently grew seeding code that inserted children
with no parents -- passing while asserting against rows production cannot hold.
Each module then needed its own near-identical fix, which is the duplication
this module replaces.

Values are deliberately minimal: enough to satisfy NOT NULL and the foreign
keys, nothing that a test might want to assert on.
"""

from services.api.models import Game, Puzzle

PLACEHOLDER_FEN = "6k1/pp3ppp/8/3q4/8/8/PP3PPP/3Q2K1 w - - 0 1"

# Puzzles are uniquely indexed on (username, source_game_id, ply), so fixtures
# that create several puzzles against one game need a distinct ply per row.
_PLY = iter(range(1, 1_000_000))


def ensure_game(db, game_id, username, **overrides):
    """Get-or-create the games row a puzzle's composite FK points at."""
    existing = db.get(Game, (game_id, username))
    if existing is not None:
        return existing
    game = Game(
        game_id=game_id,
        url=overrides.pop("url", ""),
        username=username,
        white_username=overrides.pop("white_username", username),
        black_username=overrides.pop("black_username", ""),
        white_result=overrides.pop("white_result", ""),
        black_result=overrides.pop("black_result", ""),
        time_control=overrides.pop("time_control", ""),
        end_time=overrides.pop("end_time", 0),
        **overrides,
    )
    db.add(game)
    db.flush()
    return game


def ensure_puzzle(db, puzzle_id, username, **overrides):
    """Get-or-create a puzzle, and the game it hangs off.

    Use wherever a test needs a PuzzleStats or PuzzleReview row to be legal:
    both reference puzzles.id, and inserting them without this is the exact
    shape of the 38 defects SQLite was hiding.
    """
    existing = db.get(Puzzle, puzzle_id)
    if existing is not None:
        return existing
    game_id = overrides.pop("source_game_id", f"g-{puzzle_id}")
    ensure_game(db, game_id, username)
    puzzle = Puzzle(
        id=puzzle_id,
        username=username,
        source_game_id=game_id,
        ply=overrides.pop("ply", next(_PLY)),
        fen=overrides.pop("fen", PLACEHOLDER_FEN),
        side_to_move=overrides.pop("side_to_move", "white"),
        played_move_uci=overrides.pop("played_move_uci", "d1d2"),
        best_move_uci=overrides.pop("best_move_uci", "d1d5"),
        eval_before=overrides.pop("eval_before", 0.5),
        eval_after=overrides.pop("eval_after", -3.0),
        swing=overrides.pop("swing", 3.0),
        **overrides,
    )
    db.add(puzzle)
    db.flush()
    return puzzle
