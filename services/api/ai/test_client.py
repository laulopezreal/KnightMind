"""Tests for the AI diagnosis call and its validation gate.

Fully mocked — no network, no key required. The gate is the reason this feature
can ship with the flag ON, so every rejection path has its own test: a gate that
is never exercised is a gate nobody knows is broken.
"""

import json
from types import SimpleNamespace

import chess
import pytest

from services.api.ai import client as ai_client
from services.api.ai import config
from services.api.ai.prompts import SYSTEM_PROMPT, build_user_prompt
from services.api.diagnosis.causes import classify_causes
from services.api.diagnosis.evidence import (
    GameFacts,
    PuzzleFacts,
    extract_evidence,
    to_evidence_items,
)

# Black's queen on d5 is undefended; the user played Qd2 and missed Qxd5.
HANGING_QUEEN = "6k1/pp3ppp/8/3q4/8/8/PP3PPP/3Q2K1 w - - 0 1"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("KNIGHTMIND_AI_DIAGNOSIS", raising=False)
    ai_client.reset_clients()
    yield
    ai_client.reset_clients()


def packet_and_assessment():
    board = chess.Board(HANGING_QUEEN)
    puzzle = PuzzleFacts(
        fen=HANGING_QUEEN,
        played_move_uci="d1d2",
        best_move_uci="d1d5",
        ply=41,
        eval_before=1.5,
        eval_after=-7.5,
        swing=9.0,
        accept_moves_uci=("d1d5",),
        solution_pv=("d1d5",),
    )
    packet = extract_evidence(
        puzzle, GameFacts(user_is_white=board.turn == chess.WHITE)
    )
    return packet, classify_causes(packet)


def valid_payload(packet, assessment, **overrides):
    payload = {
        "primary_cause": assessment.primary_cause,
        "secondary_causes": [],
        "evidence_ids": [to_evidence_items(packet)[0].id],
        "confidence": 0.8,
        "explanation": "You left the queen takeable and played elsewhere.",
        "training_recommendation": "Scan for undefended pieces before committing.",
    }
    payload.update(overrides)
    return payload


def fake_response(payload, stop_reason="end_turn", model="claude-opus-5"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        model=model,
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=1500, output_tokens=400),
    )


def patch_response(monkeypatch, response):
    """Replace the network call, leaving interpretation and the gate real."""
    monkeypatch.setattr(ai_client, "_call", lambda *a, **k: response)


def patch_raising(monkeypatch, exc):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(ai_client, "_call", boom)


class TestNoCallPaths:
    def test_kill_switch_prevents_any_call(self, monkeypatch):
        """KNIGHTMIND_AI_DIAGNOSIS=0 must be total — an incident is resolved
        with an env change and a restart, not a rollback."""
        monkeypatch.setenv("KNIGHTMIND_AI_DIAGNOSIS", "0")
        patch_raising(monkeypatch, AssertionError("the model must not be called"))

        packet, assessment = packet_and_assessment()
        outcome = ai_client.enrich(packet, assessment)
        assert outcome.status == ai_client.SKIPPED
        assert outcome.reason == "disabled"

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
    def test_falsey_values_all_disable(self, monkeypatch, value):
        monkeypatch.setenv("KNIGHTMIND_AI_DIAGNOSIS", value)
        assert not config.is_enabled()

    def test_enabled_by_default_and_on_blank(self, monkeypatch):
        monkeypatch.delenv("KNIGHTMIND_AI_DIAGNOSIS", raising=False)
        assert config.is_enabled()
        monkeypatch.setenv("KNIGHTMIND_AI_DIAGNOSIS", "   ")
        assert config.is_enabled()

    def test_a_missing_key_is_a_skip_not_an_error(self, monkeypatch):
        """The service runs fine without a key; diagnosis is enrichment, not a
        load-bearing dependency like DATABASE_URL."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        patch_raising(monkeypatch, AssertionError("the model must not be called"))

        packet, assessment = packet_and_assessment()
        outcome = ai_client.enrich(packet, assessment)
        assert outcome.status == ai_client.SKIPPED
        assert outcome.reason == "no_api_key"

    def test_blank_key_counts_as_missing(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
        assert config.api_key() is None

    def test_nothing_to_rank_is_not_asked(self, monkeypatch):
        """Asking the model to explain an absence of candidates invites it to
        invent one — the single thing the design forbids."""
        patch_raising(monkeypatch, AssertionError("the model must not be called"))
        packet, _ = packet_and_assessment()
        quiet = classify_causes(
            extract_evidence(
                PuzzleFacts(
                    fen="4k3/pp4pp/8/8/8/8/PP4PP/4K3 w - - 0 20",
                    played_move_uci="e1d2",
                    best_move_uci="e1f2",
                    ply=39,
                    eval_before=0.3,
                    eval_after=-0.4,
                    swing=0.7,
                ),
                GameFacts(user_is_white=True),
            )
        )
        assert quiet.insufficient_evidence
        outcome = ai_client.enrich(packet, quiet)
        assert outcome.status == ai_client.SKIPPED
        assert outcome.reason == "no_candidates"


class TestHappyPath:
    def test_a_valid_response_is_accepted(self, monkeypatch):
        packet, assessment = packet_and_assessment()
        patch_response(monkeypatch, fake_response(valid_payload(packet, assessment)))

        outcome = ai_client.enrich(packet, assessment)
        assert outcome.usable
        assert outcome.diagnosis is not None
        assert outcome.diagnosis.primary_cause == assessment.primary_cause
        assert outcome.agreed_with_rules is True
        assert outcome.input_tokens == 1500
        assert outcome.output_tokens == 400

    def test_a_re_rank_within_the_candidate_set_is_allowed(self, monkeypatch):
        """Re-ranking is the model's entire remit — it must not be mistaken for
        a violation."""
        packet, assessment = packet_and_assessment()
        others = [
            c for c in assessment.supported_causes if c != assessment.primary_cause
        ]
        assert others, "fixture needs more than one candidate"

        patch_response(
            monkeypatch,
            fake_response(valid_payload(packet, assessment, primary_cause=others[0])),
        )
        outcome = ai_client.enrich(packet, assessment)
        assert outcome.usable
        assert outcome.diagnosis is not None
        assert outcome.diagnosis.primary_cause == others[0]
        assert outcome.agreed_with_rules is False


class TestTheGate:
    """Every rejection reason. A gate nobody exercises is a gate nobody knows
    is broken."""

    def reject(self, monkeypatch, **overrides):
        packet, assessment = packet_and_assessment()
        patch_response(
            monkeypatch,
            fake_response(valid_payload(packet, assessment, **overrides)),
        )
        return ai_client.enrich(packet, assessment)

    def test_an_invented_cause_is_rejected(self, monkeypatch):
        """The core invariant: the model ranks the rules' candidates, never
        adds to them."""
        outcome = self.reject(monkeypatch, primary_cause="vibes_based_blunder")
        assert outcome.status == ai_client.REJECTED
        assert outcome.reason.startswith("cause_not_supported")
        assert outcome.diagnosis is None

    def test_an_invented_secondary_cause_is_rejected(self, monkeypatch):
        outcome = self.reject(monkeypatch, secondary_causes=["astrology"])
        assert outcome.reason.startswith("secondary_not_supported")

    def test_a_repeated_cause_is_rejected(self, monkeypatch):
        packet, assessment = packet_and_assessment()
        patch_response(
            monkeypatch,
            fake_response(
                valid_payload(
                    packet, assessment, secondary_causes=[assessment.primary_cause]
                )
            ),
        )
        outcome = ai_client.enrich(packet, assessment)
        assert outcome.reason == "primary_repeated_in_secondary"

    def test_an_uncited_evidence_id_is_rejected(self, monkeypatch):
        """A citation pointing at a fact the packet does not carry is exactly
        the unverifiable claim this design exists to prevent."""
        outcome = self.reject(monkeypatch, evidence_ids=["clock.seconds_left"])
        assert outcome.reason.startswith("evidence_not_in_packet")

    @pytest.mark.parametrize(
        "field,reason",
        [
            ("explanation", "empty_explanation"),
            ("training_recommendation", "empty_recommendation"),
        ],
    )
    def test_empty_prose_is_rejected(self, monkeypatch, field, reason):
        outcome = self.reject(monkeypatch, **{field: "   "})
        assert outcome.reason == reason

    def test_runaway_prose_is_rejected(self, monkeypatch):
        outcome = self.reject(monkeypatch, explanation="x" * 5000)
        assert outcome.reason == "explanation_too_long"

    def test_out_of_range_confidence_is_rejected_by_the_schema(self, monkeypatch):
        outcome = self.reject(monkeypatch, confidence=1.7)
        assert outcome.reason.startswith("schema:")

    def test_malformed_json_is_rejected(self, monkeypatch):
        packet, assessment = packet_and_assessment()
        patch_response(monkeypatch, fake_response("{not json at all"))
        outcome = ai_client.enrich(packet, assessment)
        assert outcome.reason is not None
        assert outcome.reason.startswith("schema:")
        # The raw text is preserved for the audit trail even when unusable.
        assert outcome.raw_response == "{not json at all"

    def test_a_missing_field_is_rejected(self, monkeypatch):
        packet, assessment = packet_and_assessment()
        patch_response(monkeypatch, fake_response({"primary_cause": "x"}))
        second_outcome = ai_client.enrich(packet, assessment)
        assert second_outcome.reason is not None
        assert second_outcome.reason.startswith("schema:")


class TestProviderOutcomes:
    def test_a_refusal_is_handled_before_reading_content(self, monkeypatch):
        """A refused response has no usable content — indexing into it first
        would raise instead of degrading."""
        packet, assessment = packet_and_assessment()
        patch_response(
            monkeypatch,
            SimpleNamespace(
                model="claude-opus-5",
                stop_reason="refusal",
                content=[],
                usage=SimpleNamespace(input_tokens=10, output_tokens=0),
            ),
        )
        outcome = ai_client.enrich(packet, assessment)
        assert outcome.status == ai_client.REJECTED
        assert outcome.reason == "refusal"

    def test_a_truncated_response_is_rejected_not_salvaged(self, monkeypatch):
        """Partial JSON is not partially usable. Rejecting keeps a too-small
        max_tokens visible as a rejection rate rather than corrupting rows."""
        packet, assessment = packet_and_assessment()
        patch_response(
            monkeypatch,
            fake_response(valid_payload(packet, assessment), stop_reason="max_tokens"),
        )
        assert ai_client.enrich(packet, assessment).reason == "truncated"

    def test_an_empty_response_is_rejected(self, monkeypatch):
        packet, assessment = packet_and_assessment()
        patch_response(
            monkeypatch,
            SimpleNamespace(model="m", stop_reason="end_turn", content=[], usage=None),
        )
        assert ai_client.enrich(packet, assessment).reason == "empty_response"

    def test_a_provider_failure_degrades_and_never_raises(self, monkeypatch):
        """AI enrichment must not be able to fail a diagnosis job."""
        packet, assessment = packet_and_assessment()
        patch_raising(monkeypatch, ConnectionError("provider unreachable"))

        outcome = ai_client.enrich(packet, assessment)
        assert outcome.status == ai_client.ERROR
        assert outcome.reason == "ConnectionError"
        assert not outcome.usable


class TestPromptCarriesNoIdentity:
    def test_neither_prompt_mentions_a_user(self):
        """The packet type has no identity field, so this cannot regress
        silently — but the prompt is where a careless edit would leak one."""
        packet, assessment = packet_and_assessment()
        rendered = (SYSTEM_PROMPT + build_user_prompt(packet, assessment)).lower()
        for token in ("username", "email", "account", "diaguser", "testplayer"):
            assert token not in rendered

    def test_the_prompt_lists_only_real_candidates_and_evidence(self):
        packet, assessment = packet_and_assessment()
        rendered = build_user_prompt(packet, assessment)
        for candidate in assessment.supported_causes:
            assert candidate in rendered
        for item in to_evidence_items(packet):
            assert item.id in rendered
