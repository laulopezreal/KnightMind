"""The naming gate, and the promise that it always degrades rather than fails.

Every test here runs without a network and without a database. The point of
``ai_naming`` holding no session is that its rejection paths are testable in
isolation, and these are those tests.
"""

import json
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError

from services.api.puzzles import ai_naming
from services.api.puzzles.naming_schema import MAX_NAME_CHARS, PuzzleName

FEN = "rnbqkb1r/pppppppp/8/8/6n1/5N2/PPPPPPPP/RNBQKB1R w KQkq - 0 3"


def facts(**kw) -> ai_naming.NameFacts:
    # Annotated dict[str, Any]: a bare literal infers dict[str, object],
    # and splatting that into the dataclass is a mypy arg-type error.
    base: dict[str, Any] = {
        "fen": FEN,
        "played_move_san": "h3",
        "move_number": 12,
        "answer_square": "g4",
    }
    base.update(kw)
    return ai_naming.NameFacts(**base)


# --- fake SDK responses ------------------------------------------------------


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Usage:
    input_tokens: int = 10
    output_tokens: int = 5


class _Response:
    def __init__(self, text=None, stop_reason="end_turn", model="claude-opus-5"):
        self.content = [_Block(text)] if text is not None else []
        self.stop_reason = stop_reason
        self.model = model
        self.usage = _Usage()


def _respond_with(monkeypatch, response):
    monkeypatch.setattr(ai_naming, "_call", lambda *a, **k: response)


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv("KNIGHTMIND_AI_NAMING", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


# --- the skip paths ----------------------------------------------------------


def test_disabled_skips_without_calling(monkeypatch):
    monkeypatch.setenv("KNIGHTMIND_AI_NAMING", "0")
    monkeypatch.setattr(
        ai_naming, "_call", lambda *a, **k: pytest.fail("must not call")
    )
    outcome = ai_naming.name_puzzle(facts())
    assert (outcome.status, outcome.reason) == (ai_naming.SKIPPED, "disabled")


def test_naming_is_off_by_default(monkeypatch):
    """Unlike diagnosis, naming ships OFF: it overwrites a visible column."""
    monkeypatch.delenv("KNIGHTMIND_AI_NAMING", raising=False)
    assert ai_naming.name_puzzle(facts()).reason == "disabled"


def test_missing_key_skips(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ai_naming.name_puzzle(facts()).reason == "no_api_key"


def test_no_position_skips(monkeypatch):
    monkeypatch.setattr(
        ai_naming, "_call", lambda *a, **k: pytest.fail("must not call")
    )
    assert ai_naming.name_puzzle(facts(fen="")).reason == "no_position"


# --- the failure paths -------------------------------------------------------


def test_provider_failure_becomes_an_outcome_not_an_exception(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(ai_naming, "_call", boom)
    outcome = ai_naming.name_puzzle(facts())
    assert outcome.status == ai_naming.ERROR
    assert outcome.reason == "ConnectionError"
    assert not outcome.usable


@pytest.mark.parametrize(
    "stop,reason",
    [("refusal", "refusal"), ("max_tokens", "truncated")],
)
def test_stop_reasons_are_rejected(monkeypatch, stop, reason):
    _respond_with(monkeypatch, _Response(text=None, stop_reason=stop))
    outcome = ai_naming.name_puzzle(facts())
    assert (outcome.status, outcome.reason) == (ai_naming.REJECTED, reason)


def test_empty_response_is_rejected(monkeypatch):
    _respond_with(monkeypatch, _Response(text=None))
    assert ai_naming.name_puzzle(facts()).reason == "empty_response"


def test_malformed_json_is_rejected(monkeypatch):
    _respond_with(monkeypatch, _Response(text="not json"))
    reason = ai_naming.name_puzzle(facts()).reason
    assert reason and reason.startswith("schema:")


def test_over_long_name_is_rejected(monkeypatch):
    _respond_with(
        monkeypatch, _Response(text=json.dumps({"name": "x" * (MAX_NAME_CHARS + 5)}))
    )
    reason = ai_naming.name_puzzle(facts()).reason
    assert reason and reason.startswith("schema:")


def test_move_list_is_rejected(monkeypatch):
    """A model that narrates instead of naming still satisfies the schema."""
    _respond_with(monkeypatch, _Response(text=json.dumps({"name": "1. e4 Nf6 d4"})))
    assert ai_naming.name_puzzle(facts()).reason == "name_is_move_list"


def test_two_clause_name_is_rejected(monkeypatch):
    """The real failure from the first trial run: a move and its point, joined
    by a comma. Every one passed the rest of the gate."""
    _respond_with(monkeypatch, _Response(text=json.dumps({"name": "Rf5, Pawn g4"})))
    assert ai_naming.name_puzzle(facts()).reason == "name_has_two_clauses"


def test_a_name_landing_on_the_answer_square_is_rejected(monkeypatch):
    """The name sits beside a board the user is about to solve."""
    _respond_with(monkeypatch, _Response(text=json.dumps({"name": "Nothing on g4"})))
    outcome = ai_naming.name_puzzle(facts())
    assert outcome.reason == "name_reveals_answer_square:g4"


@pytest.mark.parametrize(
    "name",
    [
        "Missed Fork Again",
        "Pinned and Sorry",
        "Six Seconds of Free Knight",
        "Check With Interest",
    ],
)
def test_a_name_may_say_the_tactic(monkeypatch, name):
    """These were rejected until the UI was actually looked at.

    Puzzles.tsx renders `primary_motif` as a badge beside the title while the
    player is solving, so refusing the word "fork" here protected nothing and
    cost the names their best material.
    """
    _respond_with(monkeypatch, _Response(text=json.dumps({"name": name})))
    assert ai_naming.name_puzzle(facts()).usable


def test_a_square_that_is_not_the_answer_is_fine(monkeypatch):
    """Only the winning move's destination is a spoiler, not any square."""
    _respond_with(monkeypatch, _Response(text=json.dumps({"name": "h6 Looked Fine"})))
    assert ai_naming.name_puzzle(facts()).usable


def test_the_answer_reaches_the_prompt_with_only_the_square_off_limits():
    """The model IS told the solution and the motif — withholding either made
    names measurably worse without making them safer (see naming_prompts'
    docstring). The square is the one part still fenced off, and the gate, not
    the prompt, is what enforces it."""
    from services.api.puzzles.naming_prompts import build_user_prompt

    prompt = build_user_prompt(facts(best_move_san="Nxg4", primary_motif="fork"))
    assert "Nxg4" in prompt
    assert "fork" in prompt
    assert "never the square" in prompt
    assert "h3" in prompt  # the played move travels too


@pytest.mark.parametrize(
    "seconds,expected",
    [(1.5, True), (3.0, False), (30.0, False), (61.0, True)],
    ids=["snap", "boundary-fast", "unremarkable", "long-think"],
)
def test_the_clock_is_only_sent_when_it_is_notable(seconds, expected):
    """Offering the clock every time made it the default hook: 13 of 40 names
    in one batch were 'N Seconds of ...'."""
    from services.api.puzzles.naming_prompts import build_user_prompt

    prompt = build_user_prompt(facts(move_time_seconds=seconds))
    assert ("Time spent" in prompt) is expected


def test_rejection_keeps_the_raw_response_for_debugging(monkeypatch):
    raw = json.dumps({"name": "1. e4 e5"})
    _respond_with(monkeypatch, _Response(text=raw))
    assert ai_naming.name_puzzle(facts()).raw_response == raw


# --- the happy path ----------------------------------------------------------


def test_accepted_name_is_returned(monkeypatch):
    _respond_with(monkeypatch, _Response(text=json.dumps({"name": "The g5 Ambush"})))
    outcome = ai_naming.name_puzzle(facts())
    assert outcome.status == ai_naming.ACCEPTED
    assert outcome.name == "The g5 Ambush"
    assert outcome.usable
    assert (outcome.input_tokens, outcome.output_tokens) == (10, 5)


def test_surrounding_quotes_are_stripped(monkeypatch):
    _respond_with(monkeypatch, _Response(text=json.dumps({"name": '"The g5 Ambush"'})))
    assert ai_naming.name_puzzle(facts()).name == "The g5 Ambush"


# --- the identity boundary ---------------------------------------------------


def test_name_facts_cannot_carry_identity():
    """The invariant ai/prompts.py documents, enforced by the type here."""
    assert not {"username", "opponent", "opponent_username", "email"} & set(
        ai_naming.NameFacts.__dataclass_fields__
    )


def test_prompt_never_contains_a_handle():
    from services.api.puzzles.naming_prompts import build_user_prompt

    prompt = build_user_prompt(facts(user_won=True), avoid=["The g5 Ambush"])
    assert "lauureal" not in prompt
    assert "won this game" in prompt  # the derived bool does travel
    assert "The g5 Ambush" in prompt  # the avoid-list does too


# --- the schema --------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "  ", "\n", "ab", "12", "a\nb"])
def test_schema_rejects_non_names(bad):
    with pytest.raises(ValidationError):
        PuzzleName.model_validate({"name": bad})


def test_san_or_uci_falls_back_on_an_unparseable_move():
    # g5 is empty here, so this is a quiet move; f3g4 takes the black knight.
    assert ai_naming.san_or_uci(FEN, "f3g5") == "Ng5"
    assert ai_naming.san_or_uci(FEN, "f3g4") == "Nxg4"
    assert ai_naming.san_or_uci(FEN, "zzzz") == "zzzz"
    assert ai_naming.san_or_uci("not a fen", "f3g5") == "f3g5"
