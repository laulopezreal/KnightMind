"""Tests for puzzle naming.

Three of these exist because the *plan* was wrong, not because the code was —
see docs/puzzle-naming-and-metadata-plan.md §7:

* :func:`test_epithet_is_stable_across_processes` — the spec said to seed on
  ``hash()``, which Python salts per process.
* :func:`test_no_second_person_anywhere` — the tone rule is a property of a word
  list, and word lists drift.
* :func:`test_no_two_puzzles_in_one_game_share_a_name` — the uniqueness claim is
  the whole reason this module exists.
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

from services.api.puzzles.naming import (
    ABSURD_OPENINGS,
    EPITHETS,
    NAME_MAX_CHARS,
    PuzzleFacts,
    compose_insight_phrase,
    compose_name,
    earned_suffix,
    move_number,
    select_rule,
)


def facts(**overrides) -> PuzzleFacts:
    base = {"puzzle_id": "p_default", "ply": 47}
    base.update(overrides)
    return PuzzleFacts(**base)


# --- Move number ----------------------------------------------------------


@pytest.mark.parametrize(
    "ply,expected",
    [
        (1, 1),  # White's first move
        (2, 1),  # Black's first move — same move number
        (3, 2),
        (24, 12),
        (47, 24),
        (99, 50),
    ],
)
def test_move_number_is_one_based(ply: int, expected: int):
    """``ply`` is 1-based: generator increments before use, and pgn_context
    documents it as a 1-based mainline index."""
    assert move_number(ply) == expected


# --- Salience ladder ------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,expected_rule",
    [
        ({"opening_family": "Bongcloud Attack"}, "absurd_opening"),
        ({"move_time_seconds": 2.0, "time_control_class": "rapid"}, "snap_decision"),
        ({"move_time_seconds": 250.0, "time_control_class": "rapid"}, "long_think"),
        ({"ply": 101}, "late_move"),
        ({"ply": 9}, "early_move"),
        ({"user_won": True}, "won_anyway"),
        ({"user_castled": False}, "uncastled"),
        ({"time_control_class": "bullet"}, "bullet"),
        ({"phase": "endgame"}, "endgame"),
        ({}, "floor"),
    ],
)
def test_each_rung_fires(kwargs: dict, expected_rule: str):
    assert select_rule(facts(**kwargs))[0] == expected_rule


def test_higher_salience_wins_over_lower():
    """A Bongcloud game that was also bullet is named for the Bongcloud."""
    rule, name = select_rule(
        facts(opening_family="Bongcloud Attack", time_control_class="bullet")
    )
    assert rule == "absurd_opening"
    assert "Bongcloud" in name


def test_snap_decision_excludes_bullet():
    """Three seconds is the whole game in bullet, so it is not a punchline."""
    assert (
        select_rule(facts(move_time_seconds=2.0, time_control_class="bullet"))[0]
        == "bullet"
    )


def test_clock_rules_exclude_correspondence():
    """``classify_time_control`` returns None for "daily"; a two-day move time
    is not a joke. Both clock rules must decline."""
    got = select_rule(facts(move_time_seconds=200_000.0, time_control_class=None))[0]
    assert got not in {"snap_decision", "long_think"}


def test_floor_always_fires_on_ply_alone():
    """There is no failure mode that yields "Puzzle"."""
    name = compose_name(facts(puzzle_id="p_bare", ply=31))
    assert name and name != "Puzzle"
    assert "16" in name  # move 16


# --- Determinism ----------------------------------------------------------


def test_epithet_is_stable_across_processes():
    """The seed must survive a process boundary.

    The plan specified ``hash(puzzle_id)``, and Python salts ``str`` hashing per
    process — every API worker would name the same puzzle differently and every
    restart would rename the corpus. An in-process ``assert f(x) == f(x)`` passes
    against that broken spec, so this test has to actually fork.
    """
    script = (
        "from services.api.puzzles.naming import PuzzleFacts, compose_name; "
        "print(compose_name(PuzzleFacts(puzzle_id='p_seed_check', ply=41)))"
    )
    runs = {
        subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        ).stdout.strip()
        for _ in range(3)
    }
    assert len(runs) == 1, f"name varies across processes: {runs}"


def test_same_facts_give_the_same_name():
    assert compose_name(facts(puzzle_id="p_x")) == compose_name(facts(puzzle_id="p_x"))


# --- Uniqueness -----------------------------------------------------------


def test_no_two_puzzles_in_one_game_share_a_name():
    """``generator._is_user_move`` fixes ply parity within a game, so no two
    puzzles from one game can share a move number — and every template carries
    the move number. This is the property the old motif-derived titles lacked.
    """
    # One game, the user is Black: every mistake is on an even ply.
    names = {
        compose_name(PuzzleFacts(puzzle_id=f"p_{ply}", ply=ply))
        for ply in range(2, 120, 2)
    }
    assert len(names) == len(range(2, 120, 2))


def test_floor_names_vary_across_a_corpus():
    """The floor is where a large corpus lands (plan F2). If it produced one
    name per move number the Library would read as samey, so the epithet has to
    actually spread."""
    names = {compose_name(PuzzleFacts(puzzle_id=f"p_{i}", ply=47)) for i in range(200)}
    # Same move number for all 200 — every difference comes from the epithet.
    assert len(names) >= len(EPITHETS) // 2


# --- Tone and layout ------------------------------------------------------

_SECOND_PERSON = re.compile(r"\b(you|your|yours|yourself)\b", re.IGNORECASE)


def test_no_second_person_anywhere():
    """Plan §3.5: punch at the position, not the player.

    These names are read at the moment the user has just failed the same puzzle
    for the fourth time. A vocabulary can drift out of that rule silently, so it
    is asserted rather than trusted.
    """
    for epithet in EPITHETS:
        assert not _SECOND_PERSON.search(epithet), epithet

    # And across every rendered name the ladder can produce.
    for kwargs in (
        {"opening_family": "Grob Opening"},
        {"move_time_seconds": 1.0, "time_control_class": "blitz"},
        {"move_time_seconds": 300.0, "time_control_class": "rapid"},
        {"ply": 120},
        {"ply": 3},
        {"user_won": True},
        {"user_castled": False},
        {"time_control_class": "bullet"},
        {"phase": "endgame"},
        {},
    ):
        assert not _SECOND_PERSON.search(compose_name(facts(**kwargs)))


def test_names_fit_the_row():
    """Names are capped in the generator, not by CSS — an elided name is not a
    name (plan F8)."""
    for kwargs in (
        {"opening_family": max(ABSURD_OPENINGS, key=len)},
        {"ply": 199},
        {"phase": "endgame", "puzzle_id": "p_" + "z" * 40},
        {},
    ):
        assert len(compose_name(facts(**kwargs))) <= NAME_MAX_CHARS


def test_absurd_openings_are_real_eco_families():
    """A curated list can rot into entries that can never match. Every name here
    must appear as a family (the part before ':') in the shipped ECO table."""
    from pathlib import Path

    tsv = Path(__file__).resolve().parents[1] / "openings" / "eco.tsv"
    families = {
        line.split("\t")[1].split(":", 1)[0].strip()
        for line in tsv.read_text().splitlines()[1:]
        if "\t" in line
    }
    assert ABSURD_OPENINGS <= families, ABSURD_OPENINGS - families


# --- User titles and manual puzzles ---------------------------------------


def test_user_title_always_wins():
    """``PuzzleStats.title`` means "the name the user picked"; a generated name
    must never overwrite one."""
    assert compose_name(facts(), user_title="My Nemesis") == "My Nemesis"


def test_manual_puzzles_are_not_named_after_a_ply():
    """A hand-entered position has no source game, so its ply means nothing."""
    name = compose_name(facts(puzzle_id="p_manual_1", is_manual=True))
    assert name.startswith("Saved position")


# --- Plain register -------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        (
            {"opening_name": "Sicilian Defense: Najdorf"},
            "Sicilian Defense: Najdorf · move 24",
        ),
        ({"opening_family": "Sicilian Defense"}, "Sicilian Defense · move 24"),
        (
            {"phase": "middlegame", "opponent": "hikaru"},
            "Middlegame vs hikaru · move 24",
        ),
        ({"opponent": "hikaru"}, "Move 24 vs hikaru"),
        ({}, "Move 24"),
    ],
)
def test_plain_register_degrades_without_emptying(kwargs: dict, expected: str):
    assert compose_name(facts(**kwargs), register="plain") == expected


def test_registers_are_two_outputs_of_one_pass():
    """Same facts, both registers, both non-empty — not two naming systems."""
    f = facts(opening_family="Bongcloud Attack")
    assert compose_name(f, "playful") != compose_name(f, "plain")
    assert compose_name(f, "playful") and compose_name(f, "plain")


# --- Earned nicknames -----------------------------------------------------


@pytest.mark.parametrize(
    "fail_count,expected",
    [(0, ""), (2, ""), (3, " (nemesis)"), (5, " (nemesis)"), (6, " (arch-nemesis)")],
)
def test_earned_suffix_thresholds(fail_count: int, expected: str):
    assert earned_suffix(fail_count) == expected


def test_nickname_never_becomes_part_of_the_identity():
    """The root is the searchable, stable name; the badge is decoration."""
    f = facts()
    assert compose_name(f) == compose_name(f)  # unchanged by any fail count
    assert earned_suffix(9) not in compose_name(f)


# --- Insight phrase -------------------------------------------------------


@pytest.mark.parametrize(
    "motif,cause,expected",
    [
        ("fork", None, "Missed a fork"),
        ("back_rank", None, "Missed a back-rank mate"),
        ("hanging_queen", "loose_piece_awareness", "Missed a hanging queen"),
        # "blunder" is the absence of a motif, so it falls through to the cause.
        ("blunder", "king_safety_blindness", "King safety blindness"),
        (None, "recapture_assumption", "Recapture assumption"),
        # Neither: honest silence, never "The Missed Win".
        ("blunder", None, None),
        (None, None, None),
    ],
)
def test_insight_phrase(motif, cause, expected):
    assert compose_insight_phrase(motif, cause) == expected
