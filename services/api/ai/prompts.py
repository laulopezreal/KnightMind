"""Prompt construction for AI diagnosis.

Pure string building from typed facts. The user turn is rendered *only* from an
``EvidencePacket`` and a ``CauseAssessment`` — neither of which carries a
username, an account id, or an email — so identity cannot reach the model even
if someone later edits this file carelessly. See the packet's module docstring.

The system prompt is deliberately stable across every call: it is the cacheable
prefix, and Claude Opus 5's 512-token cache minimum is low enough that it
actually caches.
"""

from services.api.ai.schema import MAX_EXPLANATION_CHARS, MAX_RECOMMENDATION_CHARS
from services.api.diagnosis.causes import CAUSE_LABELS, CauseAssessment
from services.api.diagnosis.evidence import EvidencePacket, to_evidence_items

SYSTEM_PROMPT = """\
You are a chess coach writing a short post-mortem for one mistake a player made \
in their own game. A deterministic analyser has already done the chess work: it \
extracted the facts and produced a shortlist of candidate causes. Your job is \
narrow and you must stay inside it.

You do three things:
1. Choose which candidate cause best explains the mistake, and which others \
contributed.
2. Cite the specific facts that support that choice.
3. Write two sentences the player can act on.

Hard constraints:
- Choose ONLY from the candidate causes given. Never introduce a cause that is \
not on the list, however well it seems to fit.
- Cite ONLY evidence ids from the list given. Every id you return must appear \
there verbatim.
- Use ONLY the supplied facts. Do not infer the player's rating, emotional \
state, time trouble, intentions, or anything about the game beyond what the \
facts state. If the facts do not mention the clock, the clock is unknown — not \
comfortable.
- Do not restate the move list or narrate the position. The player can see the \
board.

Write in second person, plainly, without praise or padding. If the evidence is \
thin, say so through a low confidence value rather than by hedging the prose."""


def build_user_prompt(packet: EvidencePacket, assessment: CauseAssessment) -> str:
    """Render the fact packet and the candidate set for one mistake."""
    items = to_evidence_items(packet)

    candidates = "\n".join(
        f"- {c.cause} ({CAUSE_LABELS.get(c.cause, c.cause)}) — "
        f"supported by: {', '.join(c.evidence_ids)}"
        for c in assessment.candidates
    )
    evidence = "\n".join(f"- {item.id}: {item.label} = {item.value}" for item in items)

    return f"""\
POSITION
The player was to move and played {packet.played.move.san}. The engine's move \
was {packet.best.move.san}. Phase: {packet.position.phase}, move \
{packet.position.move_number}.

CANDIDATE CAUSES (choose from these only)
{candidates}

EVIDENCE (cite these ids only)
{evidence}

The analyser's own ranking put "{assessment.primary_cause}" first. Follow it \
unless the evidence clearly supports a different candidate — you are checking \
its work, not rubber-stamping it.

Return the diagnosis as JSON. Keep the explanation under \
{MAX_EXPLANATION_CHARS} characters and the recommendation under \
{MAX_RECOMMENDATION_CHARS}."""
