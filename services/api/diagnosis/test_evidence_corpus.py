"""
Golden mini-corpus for evidence extraction.

A small, hand-verified set of positions covering the shapes the cause rules will
key on: loose pieces, forks, back-rank geometry, quiet-versus-forcing solutions,
promotion, en passant, and phase boundaries.

Engine-free by design, in the same spirit as
``services/api/puzzles/test_golden_corpus.py``: every expectation below is a
pure chess-rules fact checkable by hand from the FEN, so the suite is
deterministic and needs neither Stockfish nor a database.

Each entry is the *user's* position — in KnightMind a puzzle always is a real
mistake, so ``fen`` is what the user faced, ``played`` is what they actually
played, and ``best`` is what the engine wanted. "own" is therefore always the
user's side and "opponent" always theirs.
"""

import chess
import pytest

from services.api.diagnosis.evidence import (
    GameFacts,
    PuzzleFacts,
    evidence_hash,
    extract_evidence,
    to_evidence_items,
)
from services.api.diagnosis.pgn_context import (
    extract_game_context,
    parse_time_control,
)

# name -> (fen, played_uci, best_uci, ply, expectations)
GOLDEN_MISTAKES = {
    # Missed back-rank mate: the solution is a check, the played move is quiet.
    "missed_back_rank_mate": (
        "6k1/5ppp/8/8/8/8/8/4R1K1 w - - 0 1",
        "e1e2",
        "e1e8",
        61,
        {
            "phase": "endgame",
            "played_quiet": True,
            "best_quiet": False,
            "best_check": True,
            "legal_checks": 1,
            "opponent_forcing_replies": 0,  # Re8 is mate; nothing follows
        },
    ),
    # Missed capture of an undefended queen, with the user's own queen equally
    # loose — the textbook loose-piece pair.
    "missed_hanging_queen": (
        "6k1/pp3ppp/8/3q4/8/8/PP3PPP/3Q2K1 w - - 0 1",
        "d1d2",
        "d1d5",
        41,
        {
            "phase": "middlegame",
            "played_quiet": True,
            "best_capture": True,
            "best_captured_value": 9,
            "own_loose": ["d1"],
            "opponent_loose": ["d5"],
            "own_loose_value": 9,
            "back_rank_boxed": True,
            "ring_attackers": 1,
        },
    ),
    # Missed knight fork of king and queen.
    "missed_knight_fork": (
        "3q3k/8/8/4N3/8/8/8/6K1 w - - 0 1",
        "e5c4",
        "e5f7",
        61,
        {
            "phase": "endgame",
            "played_quiet": True,
            "best_check": True,
            "opponent_loose": ["d8"],
        },
    ),
    # The flagship loose-piece case: two undefended enemy pieces, and the
    # solution is a *quiet* knight move that forks them.
    "missed_fork_of_two_loose_pieces": (
        "6k1/4r3/1q6/8/8/2N5/8/7K w - - 0 1",
        "c3e2",
        "c3d5",
        51,
        {
            "phase": "middlegame",
            "played_quiet": True,
            "best_quiet": True,
            "opponent_loose": ["b6", "e7"],
            "legal_checks": 0,
        },
    ),
    # The solution is quiet and positional (a pin), not a capture or check.
    "missed_quiet_pin": (
        "4k3/8/2n5/8/8/8/8/5BK1 w - - 0 1",
        "f1e2",
        "f1b5",
        61,
        {
            "phase": "endgame",
            "played_quiet": True,
            "best_quiet": True,
            "own_loose": [],
            "opponent_loose": ["c6"],
        },
    ),
    # Missed promotion.
    "missed_promotion": (
        "8/P4k2/8/8/8/8/5K2/8 w - - 0 1",
        "f2e3",
        "a7a8q",
        81,
        {
            "phase": "endgame",
            "played_quiet": True,
            "best_quiet": False,
            "best_promotion": True,
        },
    ),
    # Missed en passant — the capture value must be a pawn even though the
    # destination square is empty.
    "missed_en_passant": (
        "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2",
        "e1d2",
        "e5d6",
        41,
        {
            "phase": "endgame",
            "best_capture": True,
            "best_captured_value": 1,
        },
    ),
    # A full board early in the game: phase must read as opening.
    "opening_missed_capture": (
        "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
        "b1c3",
        "f3e5",
        7,
        {
            "phase": "opening",
            "played_quiet": True,
            "best_capture": True,
            "legal_captures": 2,
        },
    ),
}


def _packet(fen, played, best, ply):
    board = chess.Board(fen)
    puzzle = PuzzleFacts(
        fen=fen,
        played_move_uci=played,
        best_move_uci=best,
        ply=ply,
        eval_before=1.5,
        eval_after=-1.5,
        swing=3.0,
        accept_moves_uci=(best,),
        solution_pv=(best,),
        confirmed_depth=18,
    )
    return extract_evidence(puzzle, GameFacts(user_is_white=board.turn == chess.WHITE))


@pytest.mark.parametrize("name", sorted(GOLDEN_MISTAKES))
def test_corpus_positions_are_legal(name):
    """Guards the corpus itself: an illegal move here would make every other
    expectation meaningless."""
    fen, played, best, _, _ = GOLDEN_MISTAKES[name]
    board = chess.Board(fen)
    assert chess.Move.from_uci(played) in board.legal_moves
    assert chess.Move.from_uci(best) in board.legal_moves


@pytest.mark.parametrize("name", sorted(GOLDEN_MISTAKES))
def test_extracted_facts_match_hand_labels(name):
    fen, played, best, ply, expected = GOLDEN_MISTAKES[name]
    packet = _packet(fen, played, best, ply)

    checks = {
        "phase": lambda: packet.position.phase,
        "played_quiet": lambda: packet.played.move.is_quiet,
        "best_quiet": lambda: packet.best.move.is_quiet,
        "best_check": lambda: packet.best.move.is_check,
        "best_capture": lambda: packet.best.move.is_capture,
        "best_promotion": lambda: packet.best.move.is_promotion,
        "best_captured_value": lambda: packet.best.move.captured_value,
        "own_loose": lambda: [p.square for p in packet.loose.own],
        "opponent_loose": lambda: [p.square for p in packet.loose.opponent],
        "own_loose_value": lambda: packet.loose.own_value,
        "legal_checks": lambda: packet.threats.legal_checks,
        "legal_captures": lambda: packet.threats.legal_captures,
        "opponent_forcing_replies": lambda: packet.threats.opponent_forcing_replies,
        "back_rank_boxed": lambda: packet.king.back_rank_boxed,
        "ring_attackers": lambda: packet.king.ring_attackers,
    }

    for key, want in expected.items():
        assert checks[key]() == want, f"{name}: {key}"


@pytest.mark.parametrize("name", sorted(GOLDEN_MISTAKES))
def test_every_corpus_position_yields_citable_evidence(name):
    """A packet with no citable facts would make the AI stage's citation
    requirement unsatisfiable, so extraction must never come back empty."""
    fen, played, best, ply, _ = GOLDEN_MISTAKES[name]
    items = to_evidence_items(_packet(fen, played, best, ply))
    assert len(items) >= 5
    assert all(item.value for item in items)


@pytest.mark.parametrize("name", sorted(GOLDEN_MISTAKES))
def test_hashes_are_stable_and_distinct(name):
    fen, played, best, ply, _ = GOLDEN_MISTAKES[name]
    assert evidence_hash(_packet(fen, played, best, ply)) == evidence_hash(
        _packet(fen, played, best, ply)
    )


def test_corpus_hashes_do_not_collide():
    hashes = {
        name: evidence_hash(_packet(fen, played, best, ply))
        for name, (fen, played, best, ply, _) in GOLDEN_MISTAKES.items()
    }
    assert len(set(hashes.values())) == len(hashes)


# ---------------------------------------------------------------------------
# End-to-end: a real PGN, replayed, feeding a real extraction
# ---------------------------------------------------------------------------

RECAPTURE_PGN = """[Event "Live Chess"]
[White "alice"]
[Black "bob"]
[TimeControl "180+2"]

1. e4 {[%clk 0:02:58]} e5 {[%clk 0:00:40]} 2. Nf3 {[%clk 0:02:55]} Nc6 {[%clk 0:00:30]}
3. Bb5 {[%clk 0:02:50]} a6 {[%clk 0:00:16]} 4. Bxc6 {[%clk 0:02:45]} dxc6 {[%clk 0:00:12]} *
"""
# Black's clock falls steadily to a genuine scramble: 16s before the mistake,
# 12s after, so the recapture took ~6s of real thought. The earlier fixture
# jumped 2:40 -> 0:12 on a single move, which reads as a long think that
# happened to end low rather than as time pressure — an ambiguous case for the
# assertion below to rest on.


def test_pgn_replay_feeds_recapture_and_clock_evidence():
    """The whole point of keeping the PGN: a FEN alone cannot say 'they
    recaptured on autopilot with twelve seconds left'."""
    time_control = parse_time_control("180+2")
    context = extract_game_context(
        RECAPTURE_PGN, ply=8, user_is_white=False, time_control=time_control
    )
    # Black recaptures on c6 after White's Bxc6.
    fen = context.fen_before_move
    assert fen is not None
    puzzle = PuzzleFacts(
        fen=fen,
        played_move_uci="d7c6",
        best_move_uci="b7c6",
        ply=8,
        eval_before=0.2,
        eval_after=-0.9,
        swing=1.1,
        accept_moves_uci=("b7c6",),
    )
    packet = extract_evidence(
        puzzle,
        GameFacts(user_is_white=False, time_control=time_control, rated=True),
        context,
    )

    assert not packet.game.pgn_desync
    assert packet.played.is_recapture
    # A fast move on a low clock — both halves of the scramble, not just a low
    # reading that a long think happened to end on.
    assert packet.clock.move_time_seconds == 6.0
    assert packet.clock.seconds_left_after_move == 12.0
    assert packet.clock.is_time_pressure
    assert packet.position.phase == "opening"

    cited = {item.id for item in to_evidence_items(packet)}
    assert {"played.is_recapture", "clock.seconds_left", "clock.move_time"} <= cited
