"""
Unit tests for puzzle generator.
"""

from contextlib import contextmanager
from unittest.mock import Mock, patch

import chess
import pytest
from sqlalchemy import select

from services.api.engine import (
    EvalResult,
    MoveEval,
    StockfishEngineDeadError,
    StockfishError,
)
from services.api.models import Game, PuzzleStats
from services.api.puzzles.generator import (
    GenerationResult,
    GenerationStatus,
    _is_user_move,
    generate_puzzles,
    get_confirm_depth,
)
from services.api.puzzles.identity import assign_primary_motif, generate_puzzle_title
from services.api.storage.puzzle_repository import PuzzleRepository


@pytest.fixture
def temp_storage(db_session):
    """Patch SessionLocal so generator uses our in-memory test DB."""

    @contextmanager
    def fake_session_local():
        yield db_session

    with patch("services.api.puzzles.generator.SessionLocal", fake_session_local):
        yield db_session


def _store_game(
    db,
    pgn: str,
    username: str = "testuser",
    url: str = "https://chess.com/game/test",
    white_username: str = "testuser",
    black_username: str = "opponent",
    white_result: str = "win",
    black_result: str = "loss",
):
    """Helper to store a game in the DB."""
    import hashlib

    game_id = hashlib.sha256(url.encode()).hexdigest()[:16]
    db.add(
        Game(
            game_id=game_id,
            url=url,
            username=username.lower(),
            white_username=white_username,
            black_username=black_username,
            white_result=white_result,
            black_result=black_result,
            time_control="600",
            end_time=1234567890,
            rated=True,
            pgn_blob=pgn,
        )
    )
    db.commit()


def test_is_user_move_white():
    """Test detecting when it's the user's move as white."""
    board = chess.Board()  # White to move

    assert _is_user_move(board, "player1", "player1", "player2") is True
    assert _is_user_move(board, "player2", "player1", "player2") is False


def test_is_user_move_black():
    """Test detecting when it's the user's move as black."""
    board = chess.Board()
    board.push(chess.Move.from_uci("e2e4"))  # Black to move

    assert _is_user_move(board, "player1", "player1", "player2") is False
    assert _is_user_move(board, "player2", "player1", "player2") is True


def test_is_user_move_case_insensitive():
    """Test that username matching is case-insensitive."""
    board = chess.Board()

    assert _is_user_move(board, "Player1", "player1", "player2") is True
    assert _is_user_move(board, "PLAYER1", "player1", "player2") is True


def test_generate_puzzles_no_games(temp_storage):
    """Test that generating puzzles with no games raises ValueError."""
    with pytest.raises(ValueError, match="No games found"):
        generate_puzzles("nonexistent_user")


# A single long game (30 plies): enough that intra-game heartbeats must fire.
_LONG_GAME_PGN = """[Event "Test Long Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 7. Bb3 d6 \
8. c3 O-O 9. h3 Nb8 10. d4 Nbd7 11. Nbd2 Bb7 12. Bc2 Re8 13. Nf1 Bf8 \
14. Ng3 g6 15. a4 c5"""


@patch("services.api.puzzles.generator.create_engine")
def test_heartbeat_fires_within_single_long_game(mock_create_engine, temp_storage):
    """Regression: the heartbeat must fire WITHIN a single game, not only
    between games. A 30-ply game must invoke cancellation_check more than once
    (once per game + once every HEARTBEAT_PLY_INTERVAL plies), so one deep game
    can't outlast the crash-recovery lease.
    """
    from services.api.puzzles.generator import HEARTBEAT_PLY_INTERVAL

    db = temp_storage
    mock_create_engine.return_value = Mock()
    _store_game(db, _LONG_GAME_PGN)

    calls = {"n": 0}

    def counting_check() -> bool:
        calls["n"] += 1
        return False  # never cancel

    with patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval:
        mock_eval.return_value = EvalResult(best_move_uci="e2e4", eval=0.3)
        generate_puzzles(
            "testuser",
            max_games=1,
            max_puzzles=10,
            cancellation_check=counting_check,
        )

    # 1 per-game check + one every HEARTBEAT_PLY_INTERVAL plies over 30 plies.
    assert calls["n"] > 1  # fired within the game, not just between games
    assert calls["n"] >= 1 + 30 // HEARTBEAT_PLY_INTERVAL


@patch("services.api.puzzles.generator.create_engine")
def test_progress_callback_fires_once_per_game(mock_create_engine, temp_storage):
    """progress_callback reports (done, total) before each game so a caller
    (the job worker) can surface honest movement during a long run. It is
    separate from cancellation_check, so callers without it are unaffected."""
    db = temp_storage
    mock_create_engine.return_value = Mock()
    _store_game(db, _LONG_GAME_PGN, url="https://chess.com/game/p1")
    _store_game(db, _LONG_GAME_PGN, url="https://chess.com/game/p2")

    reports: list[tuple[int, int]] = []

    with patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval:
        mock_eval.return_value = EvalResult(best_move_uci="e2e4", eval=0.3)
        generate_puzzles(
            "testuser",
            max_games=2,
            max_puzzles=10,
            progress_callback=lambda done, total: reports.append((done, total)),
        )

    assert reports == [(0, 2), (1, 2)]


@patch("services.api.puzzles.generator.create_engine")
def test_intra_game_cancellation_stops_generation(mock_create_engine, temp_storage):
    """Cancellation detected mid-game via the intra-game heartbeat halts the
    run promptly instead of grinding through the rest of the game.
    """
    db = temp_storage
    mock_create_engine.return_value = Mock()
    _store_game(db, _LONG_GAME_PGN)

    calls = {"n": 0}

    def cancel_after_first_intra_check() -> bool:
        calls["n"] += 1
        # First call is the per-game check (return False); cancel on the first
        # intra-game heartbeat so we stop well before the game ends.
        return calls["n"] >= 2

    with patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval:
        mock_eval.return_value = EvalResult(best_move_uci="e2e4", eval=0.3)
        result = generate_puzzles(
            "testuser",
            max_games=1,
            max_puzzles=10,
            cancellation_check=cancel_after_first_intra_check,
        )

    # Stopped at the first intra-game heartbeat (~ply 10), not after all 30.
    assert calls["n"] == 2
    assert result.analyzed_positions < 10


@patch("services.api.puzzles.generator.create_engine")
@patch("services.api.puzzles.generator.get_ply_range")
def test_generate_puzzles_swing_calculation(
    mock_get_ply_range, mock_create_engine, temp_storage
):
    """Test that swing is calculated correctly with proper sign."""
    db = temp_storage
    mock_create_engine.return_value = Mock()
    mock_get_ply_range.return_value = (0, 100)

    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6"""

    _store_game(db, pgn)

    # FEN-keyed so the deeper confirmation pass (which re-evaluates the same
    # before/after FENs at confirm depth) returns the SAME evals as the shallow
    # scan -- a genuine, stable blunder that survives confirmation.
    def eval_side_effect(fen, engine=None, cache_stats=None, depth=None):
        board = chess.Board(fen)
        if board.turn == chess.WHITE and board.fullmove_number == 1:
            # Before white's first move: a strong move is available (+0.5).
            return EvalResult(best_move_uci="d2d4", eval=0.5)
        if board.turn == chess.BLACK and board.fullmove_number == 1:
            # After 1.e4 (opponent POV): white threw away the edge (+1.5 -> swing 2.0).
            return EvalResult(best_move_uci="e7e5", eval=1.5)
        return EvalResult(best_move_uci="e2e4", eval=0.3)  # later moves: flat

    with patch(
        "services.api.puzzles.generator.get_or_compute_eval",
        side_effect=eval_side_effect,
    ):
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

        assert result.generated >= 1
        assert result.analyzed_positions >= 1

        # Check that the puzzle was saved with correct swing
        puzzle_repo = PuzzleRepository(db)
        puzzles = puzzle_repo.get_all_puzzles("testuser")
        if len(puzzles) > 0:
            puzzle = puzzles[0]
            assert puzzle.swing >= 2.0


@patch("services.api.puzzles.generator.create_engine")
def test_generate_puzzles_with_mocked_engine(mock_create_engine, temp_storage):
    """Test generation returns empty result when engine creation fails."""
    db = temp_storage
    mock_create_engine.side_effect = RuntimeError("Engine unavailable")

    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6"""

    _store_game(db, pgn)

    result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    assert result.generated == 0
    assert result.skipped == 0
    assert result.analyzed_positions == 0


# ---------------------------------------------------------------------------
# AUDIT GATE 3 reproduction tests (chess/puzzle correctness to SOTA)
# ---------------------------------------------------------------------------

# White is winning; playing Qf7 stalemates black, throwing away the win.
# The after-position is terminal, so the pre-fix code called the engine on a
# game-over FEN, StockfishError was raised and swallowed, and this glaring
# blunder produced no puzzle. FEN header lets us reproduce it in one ply.
_STALEMATE_TRAP_FEN = "7k/8/6K1/8/8/8/8/5Q2 w - - 0 1"
_STALEMATE_TRAP_PGN = f"""[Event "Stalemate trap"]
[White "testuser"]
[Black "opponent"]
[FEN "{_STALEMATE_TRAP_FEN}"]
[SetUp "1"]

1. Qf7 *"""


@patch("services.api.puzzles.generator.get_top_moves")
@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_walking_into_terminal_is_captured_not_dropped(
    mock_create_engine, mock_ply, mock_top, temp_storage
):
    """A move that ends the game as a blunder (stalemating a won position)
    must become a puzzle. Before the terminal-detection fix the engine raised
    on the game-over FEN and the position was silently dropped."""
    db = temp_storage
    mock_create_engine.return_value = Mock()
    mock_ply.return_value = (0, 100)
    mock_top.return_value = []  # acceptance-set lookup irrelevant here
    _store_game(db, _STALEMATE_TRAP_PGN)

    def eval_side_effect(fen, engine=None, cache_stats=None, depth=None):
        if fen == _STALEMATE_TRAP_FEN:
            return EvalResult(best_move_uci="f1f6", eval=9.0)  # winning before
        # Any other FEN here is the terminal after-position; the real engine
        # raises on it, mimicking the pre-fix drop path.
        raise StockfishError("No legal moves available")

    with patch(
        "services.api.puzzles.generator.get_or_compute_eval",
        side_effect=eval_side_effect,
    ):
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    assert result.analyzed_positions == 1
    assert result.generated == 1  # was 0 before the fix
    assert result.failed_positions == 0
    assert result.status == GenerationStatus.SUCCESS.value

    puzzles = PuzzleRepository(db).get_all_puzzles("testuser")
    assert len(puzzles) == 1
    # Won game (eval_before +9) thrown away to a draw (eval_after 0).
    assert puzzles[0].swing >= 2.0


@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_engine_unavailable_is_distinct_from_no_mistakes(
    mock_create_engine, mock_ply, temp_storage
):
    """Engine-unavailable and no-blunders-found used to be the identical
    GenerationResult(0, 0, 0). They must now carry distinct statuses so a job
    does not report engine failure as a successful zero-puzzle run."""
    db = temp_storage
    mock_ply.return_value = (0, 100)
    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d3 d6"""
    _store_game(db, pgn)

    # 1. Engine cannot be created at all.
    mock_create_engine.side_effect = RuntimeError("Engine unavailable")
    unavailable = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    # 2. Engine fine, but no move swings past the threshold.
    mock_create_engine.side_effect = None
    mock_create_engine.return_value = Mock()
    with patch(
        "services.api.puzzles.generator.get_or_compute_eval",
        return_value=EvalResult(best_move_uci="e2e4", eval=0.1),
    ):
        no_mistakes = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    assert unavailable.status == GenerationStatus.ENGINE_UNAVAILABLE.value
    assert no_mistakes.status == GenerationStatus.NO_MISTAKES.value
    assert unavailable.status != no_mistakes.status
    # Both still report zero puzzles, but they are no longer indistinguishable.
    assert unavailable.generated == no_mistakes.generated == 0
    assert no_mistakes.analyzed_positions > 0
    assert unavailable.analyzed_positions == 0


@patch("services.api.puzzles.generator.get_top_moves")
@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_save_error_skips_one_puzzle_not_whole_batch(
    mock_create_engine, mock_ply, mock_top, temp_storage
):
    """A DB error while saving one puzzle must skip that puzzle and let the
    batch continue, rather than aborting the entire generation run."""
    from sqlalchemy.exc import OperationalError

    db = temp_storage
    mock_create_engine.return_value = Mock()
    mock_ply.return_value = (0, 100)
    mock_top.return_value = []
    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6"""
    _store_game(db, pgn)

    real_save = PuzzleRepository.save_puzzle
    save_calls = {"n": 0}

    def flaky_save(self, *args, **kwargs):
        save_calls["n"] += 1
        if save_calls["n"] == 1:
            raise OperationalError("save failed", None, Exception("db"))
        return real_save(self, *args, **kwargs)

    with (
        patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval,
        patch.object(PuzzleRepository, "save_puzzle", flaky_save),
    ):
        mock_eval.side_effect = [
            EvalResult(best_move_uci="d2d4", eval=2.0),
            EvalResult(best_move_uci="e7e5", eval=2.0),
        ] * 20

        # Must not raise despite the first save failing.
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    assert save_calls["n"] >= 2  # kept going past the failed save
    assert result.generated >= 1  # later puzzles were still saved


@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_partial_failure_is_not_reported_as_no_mistakes(
    mock_create_engine, mock_ply, temp_storage
):
    """When some positions fail to evaluate but none of the survivors were
    blunders, the run is degraded (PARTIAL), not a clean NO_MISTAKES."""
    db = temp_storage
    mock_create_engine.return_value = Mock()
    mock_ply.return_value = (0, 100)
    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d3 d6"""
    _store_game(db, pgn)

    calls = {"n": 0}

    def eval_side_effect(fen, engine=None, cache_stats=None):
        calls["n"] += 1
        if calls["n"] == 1:
            # First analyzed position fails to evaluate.
            raise StockfishError("boom")
        # Everything else evaluates flat -> no blunder.
        return EvalResult(best_move_uci="e2e4", eval=0.1)

    with patch(
        "services.api.puzzles.generator.get_or_compute_eval",
        side_effect=eval_side_effect,
    ):
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    assert result.analyzed_positions > 1
    assert 0 < result.failed_positions < result.analyzed_positions
    assert result.generated == 0
    assert result.status == GenerationStatus.PARTIAL.value  # not NO_MISTAKES


@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_all_evals_timing_out_is_not_reported_as_no_mistakes(
    mock_create_engine, mock_ply, temp_storage
):
    """REGRESSION: on a wedged host where every evaluation times out, the engine
    is effectively broken -- the run must NOT masquerade as a clean NO_MISTAKES.

    Before the fix the StockfishEngineDeadError (timeout) recovery path recreated
    the engine and continued WITHOUT counting the failure, so an all-timeout run
    produced failed_positions=0, generated=0, analyzed_positions=N -> NO_MISTAKES
    (a broken engine reported as "no mistakes found"). Timeouts must now feed a
    timed_out counter so the run reports ALL_FAILED (or PARTIAL), never
    NO_MISTAKES."""
    db = temp_storage
    # A fresh engine is always available on recreate, so the batch keeps going
    # position-by-position -- and every single eval times out.
    mock_create_engine.return_value = Mock()
    mock_ply.return_value = (0, 100)
    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d3 d6"""
    _store_game(db, pgn)

    with patch(
        "services.api.puzzles.generator.get_or_compute_eval",
        side_effect=StockfishEngineDeadError("Engine evaluation timed out"),
    ):
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    # Positions were analyzed, but every one timed out -> broken engine.
    assert result.analyzed_positions > 0
    assert result.generated == 0
    assert result.timed_out == result.analyzed_positions
    assert result.failed_positions == 0
    # The whole point: this is NOT a clean no-mistakes run.
    assert result.status != GenerationStatus.NO_MISTAKES.value
    assert result.status == GenerationStatus.ALL_FAILED.value


@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_unrecoverable_engine_death_ends_run_without_spawning_more_engines(
    mock_create_engine, mock_ply, temp_storage
):
    """REGRESSION: when the shared engine dies and cannot be recreated, the run
    must end cleanly. Before the fix the inner move loop broke, but the OUTER
    per-game loop kept iterating with engine=None, silently abandoning the rest
    of the current game and spawning throwaway engines for every remaining game.
    Now a dead, un-recreatable engine ends the whole run at the top of the outer
    loop."""
    db = temp_storage
    mock_ply.return_value = (0, 100)
    # First create() succeeds; every recreate() thereafter fails (returns None).
    creates = {"n": 0}

    def create_side_effect():
        creates["n"] += 1
        return Mock() if creates["n"] == 1 else None

    mock_create_engine.side_effect = create_side_effect

    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d3 d6"""
    # Two distinct games so the outer loop would iterate again if not stopped.
    _store_game(db, pgn, url="https://chess.com/game/one")
    _store_game(db, pgn, url="https://chess.com/game/two")

    with patch(
        "services.api.puzzles.generator.get_or_compute_eval",
        side_effect=StockfishEngineDeadError("Engine evaluation timed out"),
    ):
        result = generate_puzzles("testuser", max_games=2, max_puzzles=10)

    # Exactly one recreate attempt: initial create + one failed recreate. The
    # second game was never touched (no third create). Pre-fix this was >= 3.
    assert creates["n"] == 2
    assert result.generated == 0
    assert result.status == GenerationStatus.ALL_FAILED.value


@patch("services.api.puzzles.generator.get_top_moves")
@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_equivalent_best_moves_are_all_accepted(
    mock_create_engine, mock_ply, mock_top, temp_storage
):
    """When two moves are within the equivalence tolerance of the best, both
    must be stored in the acceptance set. Previously only a single best move
    was kept, so an equally-good alternative was judged wrong."""
    db = temp_storage
    mock_create_engine.return_value = Mock()
    mock_ply.return_value = (0, 100)
    # d1d5 and d1d8 are equal (within 0.3 pawns); a2a3 is not close.
    mock_top.return_value = [
        MoveEval(uci="d1d5", eval=9.0),
        MoveEval(uci="d1d8", eval=8.9),
        MoveEval(uci="a2a3", eval=0.1),
    ]

    fen = "3q3k/6pp/8/8/8/8/PP4PP/3Q2K1 w - - 0 1"
    pgn = f"""[Event "Equiv"]
[White "testuser"]
[Black "opponent"]
[FEN "{fen}"]
[SetUp "1"]

1. a3 *"""
    _store_game(db, pgn)

    def eval_side_effect(f, engine=None, cache_stats=None, depth=None):
        if f == fen:
            return EvalResult(best_move_uci="d1d5", eval=9.0)  # winning capture avail
        return EvalResult(best_move_uci="a2a4", eval=0.0)  # after the weak a3

    with patch(
        "services.api.puzzles.generator.get_or_compute_eval",
        side_effect=eval_side_effect,
    ):
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    assert result.generated == 1
    puzzle = PuzzleRepository(db).get_all_puzzles("testuser")[0]
    motif = assign_primary_motif(puzzle)
    stats = db.scalar(select(PuzzleStats).where(PuzzleStats.puzzle_id == puzzle.id))
    assert stats is not None
    assert stats.username == "testuser"
    assert stats.primary_motif == motif
    assert stats.title == generate_puzzle_title(motif)
    assert stats.attempts == 0
    accepted = set(puzzle.accept_moves_uci.split(","))
    assert "d1d5" in accepted
    assert "d1d8" in accepted  # the equally-good alternative is accepted
    assert "a2a3" not in accepted  # clearly-worse move is not


@patch("services.api.puzzles.generator.create_engine")
def test_generate_puzzles_recovers_from_engine_timeout(
    mock_create_engine, temp_storage
):
    """A mid-batch engine timeout must recreate the shared engine.

    Regression: previously a per-eval timeout killed the shared batch engine
    and raised, but the generator kept it, so every subsequent position ran
    against a dead subprocess (each stalling until the timeout fired again).
    Now the batch recreates the engine and keeps going, so positions after the
    timeout are still evaluated.
    """
    db = temp_storage
    # Distinct engines so a recreation is observable via call_count.
    mock_create_engine.side_effect = [Mock(), Mock(), Mock()]

    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. d3 Bc5 5. O-O O-O 6. c3 d6 7. h3 h6 8. Re1 a6 9. Bb3 Ba7"""

    _store_game(db, pgn)

    call_state = {"n": 0}

    def eval_side_effect(fen, engine=None, cache_stats=None, depth=None):
        call_state["n"] += 1
        # Fail the 'after' eval of the FIRST analyzed position (call #2),
        # simulating a timeout that killed the shared engine mid-batch. The
        # move is already pushed by then, so the board stays consistent.
        if call_state["n"] == 2:
            raise StockfishEngineDeadError("Engine evaluation timed out")
        return EvalResult(best_move_uci="d2d4", eval=2.0)

    with patch(
        "services.api.puzzles.generator.get_or_compute_eval",
        side_effect=eval_side_effect,
    ):
        result = generate_puzzles("testuser", max_games=1, max_puzzles=30)

    # The engine was recreated after the timeout (initial create + >=1 recreate).
    assert mock_create_engine.call_count >= 2
    # Positions k+1..N were still evaluated (and produced puzzles), i.e. the
    # batch was NOT poisoned by the dead engine.
    assert result.analyzed_positions >= 2
    assert result.generated >= 1


@patch("services.api.puzzles.generator.create_engine")
def test_generate_puzzles_only_user_moves(mock_create_engine, temp_storage):
    """Test that only the user's moves are analyzed."""
    db = temp_storage
    mock_create_engine.return_value = Mock()

    pgn = """[Event "Test Game"]
[White "opponent"]
[Black "testuser"]

1. e4 e5 2. Nf3 Nc6"""

    _store_game(
        db,
        pgn,
        white_username="opponent",
        black_username="testuser",
        white_result="loss",
        black_result="win",
    )

    with patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval:
        mock_eval.return_value = EvalResult(best_move_uci="e2e4", eval=0.3)
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

        # With ply range 8-80, no moves should be analyzed in this short game
        assert result.analyzed_positions == 0


@patch("services.api.puzzles.generator.create_engine")
def test_generate_puzzles_respects_max_puzzles(mock_create_engine, temp_storage):
    """Test that generation stops at max_puzzles."""
    db = temp_storage
    mock_create_engine.return_value = Mock()

    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. d3 Bc5 5. O-O O-O 6. c3 d6 7. h3 h6 8. Re1 a6 9. Bb3 Ba7"""

    _store_game(db, pgn)

    with patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval:
        mock_eval.side_effect = [
            EvalResult(best_move_uci="d2d4", eval=2.0),
            EvalResult(best_move_uci="e7e5", eval=2.0),
        ] * 20

        result = generate_puzzles("testuser", max_games=1, max_puzzles=3)

        assert result.generated <= 3


@patch("services.api.puzzles.generator.create_engine")
def test_generate_puzzles_deduplication(mock_create_engine, temp_storage):
    """Test that running generation twice doesn't create duplicate puzzles."""
    db = temp_storage
    mock_create_engine.return_value = Mock()

    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. d3 Bc5 5. O-O O-O"""

    _store_game(db, pgn)

    with patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval:
        mock_eval.side_effect = [
            EvalResult(best_move_uci="d2d4", eval=2.0),
            EvalResult(best_move_uci="e7e5", eval=2.0),
        ] * 20

        result1 = generate_puzzles("testuser", max_games=1, max_puzzles=10)
        generated_first = result1.generated

    with patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval:
        mock_eval.side_effect = [
            EvalResult(best_move_uci="d2d4", eval=2.0),
            EvalResult(best_move_uci="e7e5", eval=2.0),
        ] * 20

        result2 = generate_puzzles("testuser", max_games=1, max_puzzles=10)

        assert result2.skipped >= generated_first
        assert result2.generated == 0


@patch("services.api.puzzles.generator.create_engine")
def test_generate_puzzles_ply_range_filtering(mock_create_engine, temp_storage):
    """Test that moves outside ply range are skipped."""
    db = temp_storage
    mock_create_engine.return_value = Mock()

    # Short game (all moves before ply 8)
    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6"""

    _store_game(db, pgn)

    with patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval:
        mock_eval.return_value = EvalResult(best_move_uci="e2e4", eval=3.0)

        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

        assert result.analyzed_positions == 0
        assert result.generated == 0


# ---------------------------------------------------------------------------
# AUDIT GATE 10: deeper confirmation / stability pass
# ---------------------------------------------------------------------------
#
# A candidate is flagged from a single shallow scan (STOCKFISH_DEPTH). Before it
# is emitted as a puzzle the generator re-analyzes it at STOCKFISH_CONFIRM_DEPTH
# and keeps it ONLY if the swing survives, the solution is stable, and the best
# move is clearly superior (uniqueness margin). These tests drive a one-white-
# move game so there is exactly one analyzed position / one candidate, and mock
# get_or_compute_eval to return DIFFERENT evals at shallow vs confirm depth.

# One analyzable white move (ply range is opened to 0-100 in these tests).
_ONE_MOVE_PGN = """[Event "One move"]
[White "testuser"]
[Black "opponent"]

1. e4 e5"""


def _depth_aware_eval(before_shallow, after_shallow, before_deep, after_deep):
    """Build a get_or_compute_eval double keyed on side-to-move and depth.

    White-to-move is the pre-move (mistake) position; black-to-move is the
    post-move position. ``depth is None`` is the shallow scan; a non-None depth
    is the confirmation pass. Each arg is an (best_move_uci, eval) tuple.
    """

    def _eval(fen, engine=None, cache_stats=None, depth=None):
        board = chess.Board(fen)
        is_before = board.turn == chess.WHITE
        is_deep = depth is not None
        if is_before:
            uci, ev = before_deep if is_deep else before_shallow
        else:
            uci, ev = after_deep if is_deep else after_shallow
        return EvalResult(best_move_uci=uci, eval=ev)

    return _eval


@patch("services.api.puzzles.generator.get_top_moves")
@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_noisy_candidate_discarded_when_swing_collapses_at_depth(
    mock_create_engine, mock_ply, mock_top, temp_storage
):
    """REPRODUCTION: a candidate that clears the shallow threshold but whose
    swing collapses under deeper analysis (engine noise) must be DISCARDED, not
    emitted. The single-shallow-pass generator emitted it as a puzzle."""
    db = temp_storage
    mock_create_engine.return_value = Mock()
    mock_ply.return_value = (0, 100)
    mock_top.return_value = []  # uniqueness not reached; unstable check fires first
    _store_game(db, _ONE_MOVE_PGN)

    # Shallow: +0.5 -> +1.5 => swing 2.0 (>= threshold). Deep: +0.2 -> +0.2 =>
    # swing 0.4 (< threshold): the "blunder" was shallow-depth noise.
    eval_double = _depth_aware_eval(
        before_shallow=("d2d4", 0.5),
        after_shallow=("e7e5", 1.5),
        before_deep=("d2d4", 0.2),
        after_deep=("e7e5", 0.2),
    )
    with patch(
        "services.api.puzzles.generator.get_or_compute_eval", side_effect=eval_double
    ):
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    assert result.candidates_found == 1
    assert result.candidates_confirmed == 0
    assert result.discarded_unstable == 1
    assert result.discarded_low_margin == 0
    assert result.generated == 0
    assert result.validity_rate == 0.0
    assert PuzzleRepository(db).get_all_puzzles("testuser") == []


@patch("services.api.puzzles.generator.get_top_moves")
@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_candidate_discarded_when_best_move_flips_at_depth(
    mock_create_engine, mock_ply, mock_top, temp_storage
):
    """A candidate whose best move FLIPS to a move outside the shallow
    acceptance set under deeper analysis is unstable and must be discarded."""
    db = temp_storage
    mock_create_engine.return_value = Mock()
    mock_ply.return_value = (0, 100)
    # Shallow acceptance set = {d2d4, g1f3} (within 0.3). The deep best (a2a3)
    # is NOT in it -> the solution flipped.
    mock_top.return_value = [
        MoveEval(uci="d2d4", eval=3.0),
        MoveEval(uci="g1f3", eval=2.9),
    ]
    _store_game(db, _ONE_MOVE_PGN)

    eval_double = _depth_aware_eval(
        before_shallow=("d2d4", 3.0),
        after_shallow=("e7e5", 3.0),  # swing 6.0 shallow
        before_deep=("a2a3", 3.0),  # deep best flips; swing still 6.0
        after_deep=("e7e5", 3.0),
    )
    with patch(
        "services.api.puzzles.generator.get_or_compute_eval", side_effect=eval_double
    ):
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    assert result.candidates_found == 1
    assert result.discarded_unstable == 1
    assert result.discarded_low_margin == 0
    assert result.generated == 0


@patch("services.api.puzzles.generator.get_top_moves")
@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_tossup_without_uniqueness_margin_discarded(
    mock_create_engine, mock_ply, mock_top, temp_storage
):
    """A candidate whose best move barely beats the next NON-equivalent move
    (below the uniqueness margin) is a toss-up, not a puzzle, and is discarded
    with the low_margin reason."""
    db = temp_storage
    mock_create_engine.return_value = Mock()
    mock_ply.return_value = (0, 100)
    # Best is +3.0; the next move is +2.6 -> gap 0.4. That is outside the 0.3
    # equivalence tolerance (so not an equally-good alternative) but inside the
    # 0.5 uniqueness margin (so not clearly superior) -> a coin-flip.
    mock_top.return_value = [
        MoveEval(uci="d2d4", eval=3.0),
        MoveEval(uci="c2c4", eval=2.6),
    ]
    _store_game(db, _ONE_MOVE_PGN)

    eval_double = _depth_aware_eval(
        before_shallow=("d2d4", 3.0),
        after_shallow=("e7e5", 3.0),
        before_deep=("d2d4", 3.0),  # swing + stability fine; only margin fails
        after_deep=("e7e5", 3.0),
    )
    with patch(
        "services.api.puzzles.generator.get_or_compute_eval", side_effect=eval_double
    ):
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    assert result.candidates_found == 1
    assert result.discarded_low_margin == 1
    assert result.discarded_unstable == 0
    assert result.generated == 0


@patch("services.api.puzzles.generator.get_top_moves")
@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_stable_blunder_is_kept_with_confirmed_provenance(
    mock_create_engine, mock_ply, mock_top, temp_storage
):
    """A genuine blunder that survives the deeper pass is kept, and the saved
    puzzle carries confirmed provenance: confirmed_depth, the confirm-depth
    swing/evals, and the refined (confirmed) acceptance set."""
    db = temp_storage
    mock_create_engine.return_value = Mock()
    mock_ply.return_value = (0, 100)
    # Deep multi-PV: d2d4 (best) and g1f3 equally good (within 0.3); a2a3 far
    # worse -> clear uniqueness margin. Confirmed accept set = {d2d4, g1f3}.
    mock_top.return_value = [
        MoveEval(uci="d2d4", eval=3.2),
        MoveEval(uci="g1f3", eval=3.1),
        MoveEval(uci="a2a3", eval=0.1),
    ]
    _store_game(db, _ONE_MOVE_PGN)

    eval_double = _depth_aware_eval(
        before_shallow=("d2d4", 3.0),
        after_shallow=("e7e5", 3.0),  # shallow swing 6.0
        before_deep=("d2d4", 3.2),
        after_deep=("e7e5", 3.1),  # confirmed swing 6.3
    )
    with patch(
        "services.api.puzzles.generator.get_or_compute_eval", side_effect=eval_double
    ):
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    assert result.candidates_found == 1
    assert result.candidates_confirmed == 1
    assert result.discarded_unstable == 0
    assert result.discarded_low_margin == 0
    assert result.generated == 1
    assert result.validity_rate == 1.0

    puzzle = PuzzleRepository(db).get_all_puzzles("testuser")[0]
    # Provenance reflects the confirmation pass, not the shallow scan.
    assert puzzle.confirmed_depth == get_confirm_depth()
    assert puzzle.swing == pytest.approx(6.3)  # confirmed swing, not shallow 6.0
    assert puzzle.best_move_uci == "d2d4"
    accepted = set(puzzle.accept_moves_uci.split(","))
    assert accepted == {"d2d4", "g1f3"}  # equally-good alternative kept
    assert "a2a3" not in accepted  # clearly-worse move excluded


@patch("services.api.puzzles.generator.get_top_moves")
@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_confirmation_reanalysis_uses_the_cache_entry_point(
    mock_create_engine, mock_ply, mock_top, temp_storage
):
    """The deeper re-analysis must go through get_or_compute_eval with an
    explicit confirm depth, so it reuses the version-aware cache (#199): a
    repeated confirm pass on the same position is a cache hit, and it never
    reuses (or clobbers) the shallow entry. We assert the confirm-depth calls
    are routed through the cache entry point."""
    db = temp_storage
    mock_create_engine.return_value = Mock()
    mock_ply.return_value = (0, 100)
    mock_top.return_value = [
        MoveEval(uci="d2d4", eval=3.0),
        MoveEval(uci="a2a3", eval=0.1),
    ]
    _store_game(db, _ONE_MOVE_PGN)

    seen_depths = []

    def _eval(fen, engine=None, cache_stats=None, depth=None):
        seen_depths.append(depth)
        board = chess.Board(fen)
        return EvalResult(best_move_uci="d2d4", eval=3.0 if board.turn else 3.0)

    with patch("services.api.puzzles.generator.get_or_compute_eval", side_effect=_eval):
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    assert result.generated == 1
    # Shallow scan calls carry depth=None; the confirmation pass carries the
    # confirm depth -- both routed through the single cached entry point.
    assert None in seen_depths
    assert get_confirm_depth() in seen_depths


def test_validity_rate_property_math():
    """validity_rate = confirmed / found, and is 1.0 when nothing was flagged."""
    assert GenerationResult(0, 0, 0).validity_rate == 1.0
    assert (
        GenerationResult(
            generated=1,
            skipped=0,
            analyzed_positions=4,
            candidates_found=4,
            candidates_confirmed=1,
        ).validity_rate
        == 0.25
    )


@patch("services.api.puzzles.generator.get_top_moves")
@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_confirm_depth_is_never_shallower_than_base(
    mock_create_engine, mock_ply, mock_top, temp_storage, monkeypatch
):
    """Guard: a misconfigured STOCKFISH_CONFIRM_DEPTH below the base scan depth
    is clamped up to the base depth, so the confirmation pass is never weaker
    than the scan that flagged the candidate."""
    monkeypatch.setenv("STOCKFISH_DEPTH", "20")
    monkeypatch.setenv("STOCKFISH_CONFIRM_DEPTH", "8")  # below base
    assert get_confirm_depth() == 20


# --- Full principal-variation persistence (SCORECARD dim 12 -> 9) ---


def test_compute_solution_pv_walks_bounded_line():
    """The PV walk plays out the engine's best line, starting with the solution
    move, up to the ply cap."""
    from services.api.puzzles.generator import _compute_solution_pv

    # A natural opening line that is legal move-by-move from the start position.
    continuation = ["e7e5", "g1f3", "b8c6", "f1b5"]
    calls = {"n": 0}

    def eval_side_effect(fen, engine=None, cache_stats=None, depth=None):
        move = continuation[calls["n"]]
        calls["n"] += 1
        return EvalResult(best_move_uci=move, eval=1.0)

    with patch(
        "services.api.puzzles.generator.get_or_compute_eval",
        side_effect=eval_side_effect,
    ):
        pv = _compute_solution_pv(
            fen=chess.STARTING_FEN,
            first_move_uci="e2e4",
            engine=Mock(),
            depth=18,
            cache_stats={},
            max_plies=4,
        )

    # Capped at 4 plies: the solution move + 3 walked replies.
    assert pv == ["e2e4", "e7e5", "g1f3", "b8c6"]


def test_compute_solution_pv_stops_at_illegal_move():
    """An illegal engine move ends the walk rather than being appended."""
    from services.api.puzzles.generator import _compute_solution_pv

    with patch(
        "services.api.puzzles.generator.get_or_compute_eval",
        # a1a8 is illegal in the start position (rook is blocked) -> walk stops.
        return_value=EvalResult(best_move_uci="a1a8", eval=1.0),
    ):
        pv = _compute_solution_pv(
            fen=chess.STARTING_FEN,
            first_move_uci="e2e4",
            engine=Mock(),
            depth=18,
            cache_stats={},
            max_plies=6,
        )

    assert pv == ["e2e4"]


def test_compute_solution_pv_stops_at_terminal_position():
    """The walk stops once the position is game-over (e.g. after mate)."""
    from services.api.puzzles.generator import _compute_solution_pv

    # Fool's-mate position after 1. f3 e5 2. g4, Black to move and mate with
    # Qd8-h4#. The solution move ends the game, so the walk must stop right after
    # it without evaluating any further ply.
    fen = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2"

    called = {"n": 0}

    def eval_side_effect(f, engine=None, cache_stats=None, depth=None):
        called["n"] += 1
        return EvalResult(best_move_uci="e5e4", eval=1.0)

    with patch(
        "services.api.puzzles.generator.get_or_compute_eval",
        side_effect=eval_side_effect,
    ):
        pv = _compute_solution_pv(
            fen=fen,
            first_move_uci="d8h4",  # Qh4#
            engine=Mock(),
            depth=18,
            cache_stats={},
            max_plies=8,
        )

    # The mating move ends the game, so no further plies are walked or evaluated.
    assert pv == ["d8h4"]
    assert called["n"] == 0


@patch("services.api.puzzles.generator.get_top_moves")
@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_generate_persists_solution_pv(
    mock_create_engine, mock_ply, mock_top, temp_storage
):
    """A generated puzzle stores the full forcing line, not just move 1."""
    db = temp_storage
    mock_create_engine.return_value = Mock()
    mock_ply.return_value = (0, 100)
    # Uniqueness: d1d5 clearly best, a2a3 far worse.
    mock_top.return_value = [
        MoveEval(uci="d1d5", eval=9.0),
        MoveEval(uci="a2a3", eval=0.1),
    ]

    fen = "3q3k/6pp/8/8/8/8/PP4PP/3Q2K1 w - - 0 1"
    pgn = f"""[Event "PV"]
[White "testuser"]
[Black "opponent"]
[FEN "{fen}"]
[SetUp "1"]

1. a3 *"""
    _store_game(db, pgn)

    def eval_side_effect(f, engine=None, cache_stats=None, depth=None):
        board = chess.Board(f)
        if f == fen:
            # The mistake position: a winning queen move is available.
            return EvalResult(best_move_uci="d1d5", eval=9.0)
        # PV walk / after-move evals: give a legal move for whoever is to move so
        # the forcing line plays out for a couple of plies.
        if board.turn == chess.BLACK:
            return EvalResult(best_move_uci="h7h6", eval=9.0)
        return EvalResult(best_move_uci="a2a4", eval=0.0)

    with patch(
        "services.api.puzzles.generator.get_or_compute_eval",
        side_effect=eval_side_effect,
    ):
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    assert result.generated == 1
    puzzle = PuzzleRepository(db).get_all_puzzles("testuser")[0]
    assert puzzle.solution_pv is not None
    pv = puzzle.solution_pv.split()
    assert pv[0] == "d1d5"  # the line starts with the solution move
    assert len(pv) >= 2  # a real multi-ply line, not just the solution move


@patch("services.api.puzzles.generator.get_top_moves")
@patch("services.api.puzzles.generator.get_ply_range")
@patch("services.api.puzzles.generator.create_engine")
def test_generate_leaves_pv_null_for_single_move_solution(
    mock_create_engine, mock_ply, mock_top, temp_storage
):
    """When the solution has no forced continuation, solution_pv stays NULL so
    the puzzle trains as a single move (legacy behaviour)."""
    db = temp_storage
    mock_create_engine.return_value = Mock()
    mock_ply.return_value = (0, 100)
    mock_top.return_value = [
        MoveEval(uci="d1d5", eval=9.0),
        MoveEval(uci="a2a3", eval=0.1),
    ]

    fen = "3q3k/6pp/8/8/8/8/PP4PP/3Q2K1 w - - 0 1"
    pgn = f"""[Event "PV-single"]
[White "testuser"]
[Black "opponent"]
[FEN "{fen}"]
[SetUp "1"]

1. a3 *"""
    _store_game(db, pgn)

    def eval_side_effect(f, engine=None, cache_stats=None, depth=None):
        if f == fen:
            return EvalResult(best_move_uci="d1d5", eval=9.0)
        # After the solution move, the engine's "best move" is illegal for the
        # side to move, so the PV walk cannot extend past move 1.
        return EvalResult(best_move_uci="a2a4", eval=0.0)

    with patch(
        "services.api.puzzles.generator.get_or_compute_eval",
        side_effect=eval_side_effect,
    ):
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    assert result.generated == 1
    puzzle = PuzzleRepository(db).get_all_puzzles("testuser")[0]
    assert puzzle.solution_pv is None  # single-move puzzle -> no stored line
