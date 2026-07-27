"""The model call and — more importantly — the gate on what comes back.

This module holds no database access. It takes facts in and returns a verdict,
so every rejection path is testable without a session. Budget accounting and
audit persistence belong to the caller (see ``diagnosis/job.py``).

The gate is the reason this feature can ship with the flag ON. A prompt
regression, a model change, or a hallucinated cause all land in the same place:
the response is rejected, the rules-only diagnosis stands, and the rejection is
recorded with its reason.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from services.api.ai import config
from services.api.ai.prompts import SYSTEM_PROMPT, build_user_prompt
from services.api.ai.schema import (
    MAX_EXPLANATION_CHARS,
    MAX_RECOMMENDATION_CHARS,
    RESPONSE_SCHEMA,
    AIDiagnosis,
)
from services.api.diagnosis.causes import CauseAssessment
from services.api.diagnosis.evidence import EvidencePacket, to_evidence_items

logger = logging.getLogger(__name__)

# Outcome statuses.
ACCEPTED = "accepted"
REJECTED = "rejected"  # the model answered, but the answer failed the gate
SKIPPED = "skipped"  # no call attempted (flag off, no key, no candidates)
ERROR = "error"  # the call itself failed (network, auth, overload)


@dataclass(frozen=True)
class EnrichmentOutcome:
    status: str
    reason: str | None = None
    diagnosis: AIDiagnosis | None = None
    raw_response: str | None = None
    model_version: str | None = None
    # True when the model's primary cause matched the rules' own top pick.
    # Rolled up on /ops/status: a sudden drop is the earliest signal that a
    # prompt or model change regressed, and it is why this feature can ship
    # without a dark-launch measurement period.
    agreed_with_rules: bool | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def usable(self) -> bool:
        return self.status == ACCEPTED and self.diagnosis is not None


_clients: dict[str, Any] = {}


def _client(key: str):
    """One SDK client per key, created lazily.

    Cached because a backfill makes hundreds of calls in a row and each client
    builds its own HTTP connection pool. Keyed on the key itself so rotating
    ``ANTHROPIC_API_KEY`` swaps the client instead of silently reusing the old
    credential.
    """
    cached = _clients.get(key)
    if cached is None:
        from anthropic import Anthropic

        cached = Anthropic(
            api_key=key,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            max_retries=config.MAX_RETRIES,
        )
        _clients[key] = cached
    return cached


def reset_clients() -> None:
    """Drop cached SDK clients. Used by tests."""
    _clients.clear()


def enrich(packet: EvidencePacket, assessment: CauseAssessment) -> EnrichmentOutcome:
    """Ask the model to rank the rules' candidates and write the prose.

    Never raises. Every failure mode — disabled, unkeyed, refused, malformed,
    unreachable — returns an outcome the caller stores and moves on from. AI
    enrichment must not be able to fail a diagnosis job.
    """
    if not config.is_enabled():
        return EnrichmentOutcome(SKIPPED, reason="disabled")

    key = config.api_key()
    if not key:
        # Deliberately not an error: the service runs fine without a key and
        # serves rules-only diagnoses.
        return EnrichmentOutcome(SKIPPED, reason="no_api_key")

    if not assessment.candidates or assessment.insufficient_evidence:
        # Nothing to rank. Asking the model to explain an absence invites it to
        # invent a cause, which is the one thing the design forbids.
        return EnrichmentOutcome(SKIPPED, reason="no_candidates")

    try:
        response = _call(key, packet, assessment)
    except Exception as exc:  # noqa: BLE001 - all provider failures degrade alike
        logger.warning("AI diagnosis call failed: %s", exc.__class__.__name__)
        return EnrichmentOutcome(ERROR, reason=f"{exc.__class__.__name__}")

    return _interpret(response, packet, assessment)


def _call(key: str, packet: EvidencePacket, assessment: CauseAssessment):
    return _client(key).beta.messages.create(
        model=config.MODEL,
        max_tokens=config.MAX_TOKENS,
        # Server-side fallback: on a policy decline the API retries on a
        # recommended model rather than returning the refusal. Chess content is
        # unlikely to trip a classifier, and the rules-only path already covers
        # us if it does — this just avoids losing the enrichment to a false
        # positive.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": config.EFFORT,
            "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
        },
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                # The system prompt is byte-identical on every call, and Claude
                # Opus 5's cache minimum is 512 tokens — low enough that a
                # prefix this size actually caches.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": build_user_prompt(packet, assessment)}],
    )


def _interpret(
    response, packet: EvidencePacket, assessment: CauseAssessment
) -> EnrichmentOutcome:
    model_version = getattr(response, "model", config.MODEL)
    usage = getattr(response, "usage", None)
    tokens = {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
    }

    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        # Checked before reading content: a refused response has no usable
        # content to index into.
        return EnrichmentOutcome(
            REJECTED, reason="refusal", model_version=model_version, **tokens
        )
    if stop == "max_tokens":
        # Truncated JSON is not partially usable. Treating it as a rejection
        # keeps a too-small MAX_TOKENS visible as a rejection rate rather than
        # silently corrupting rows.
        return EnrichmentOutcome(
            REJECTED, reason="truncated", model_version=model_version, **tokens
        )

    raw = _first_text(response)
    if not raw:
        return EnrichmentOutcome(
            REJECTED, reason="empty_response", model_version=model_version, **tokens
        )

    try:
        parsed = AIDiagnosis.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        return EnrichmentOutcome(
            REJECTED,
            reason=f"schema:{exc.__class__.__name__}",
            raw_response=raw,
            model_version=model_version,
            **tokens,
        )

    violation = _validate(parsed, packet, assessment)
    if violation:
        logger.info("AI diagnosis rejected: %s", violation)
        return EnrichmentOutcome(
            REJECTED,
            reason=violation,
            raw_response=raw,
            model_version=model_version,
            **tokens,
        )

    return EnrichmentOutcome(
        ACCEPTED,
        diagnosis=parsed,
        raw_response=raw,
        model_version=model_version,
        agreed_with_rules=parsed.primary_cause == assessment.primary_cause,
        **tokens,
    )


def _first_text(response) -> str | None:
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            text = (getattr(block, "text", "") or "").strip()
            if text:
                return text
    return None


def _validate(
    parsed: AIDiagnosis, packet: EvidencePacket, assessment: CauseAssessment
) -> str | None:
    """The gate. Returns a rejection reason, or None when the answer is sound.

    Checks truth, not shape — the JSON schema already handled shape. Every rule
    here exists because violating it would put an unverifiable claim in front of
    a user.
    """
    supported = assessment.supported_causes

    if parsed.primary_cause not in supported:
        # The core invariant: the model may rank the rules' candidates, never
        # add to them.
        return f"cause_not_supported:{parsed.primary_cause}"

    unsupported = [c for c in parsed.secondary_causes if c not in supported]
    if unsupported:
        return f"secondary_not_supported:{','.join(sorted(unsupported))}"

    if parsed.primary_cause in parsed.secondary_causes:
        # A cause listed twice reads as two findings in the UI.
        return "primary_repeated_in_secondary"

    available = {item.id for item in to_evidence_items(packet)}
    uncited = [i for i in parsed.evidence_ids if i not in available]
    if uncited:
        # A citation pointing at a fact the packet does not carry is exactly
        # the unverifiable claim this whole design exists to prevent.
        return f"evidence_not_in_packet:{','.join(sorted(uncited))}"

    if not parsed.explanation.strip():
        return "empty_explanation"
    if not parsed.training_recommendation.strip():
        return "empty_recommendation"

    # Length caps are advisory in the schema; enforce them here so an
    # over-long response is rejected rather than silently breaking the card.
    if len(parsed.explanation) > MAX_EXPLANATION_CHARS * 2:
        return "explanation_too_long"
    if len(parsed.training_recommendation) > MAX_RECOMMENDATION_CHARS * 2:
        return "recommendation_too_long"

    return None
