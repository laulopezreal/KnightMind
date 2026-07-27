"""The response contract for an AI diagnosis.

Two representations of the same shape: a JSON Schema the API enforces during
generation, and a Pydantic model used to validate what actually came back.
Belt and braces — the schema stops most malformed output at the source, and the
model catches anything that still slips through before it reaches the database.
"""

from pydantic import BaseModel, Field

# Prose limits. These are not stylistic preferences: the explanation and
# recommendation are rendered in a card beside a chessboard, and an essay there
# is a UI bug. Enforced in the schema so the model targets the right length
# rather than being truncated after the fact.
MAX_EXPLANATION_CHARS = 400
MAX_RECOMMENDATION_CHARS = 200
MAX_SECONDARY_CAUSES = 3
MAX_EVIDENCE_IDS = 6


class AIDiagnosis(BaseModel):
    """What the model is allowed to return.

    Every field is checked again in ``client._validate`` against the actual
    candidate set and evidence ids for this puzzle. The schema constrains
    *shape*; validation constrains *truth*.
    """

    primary_cause: str
    secondary_causes: list[str] = Field(default_factory=list)
    # Ids from the packet's citable evidence. This is the field that makes the
    # explanation checkable: a claim with no citation is not a diagnosis.
    evidence_ids: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str
    training_recommendation: str


RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "primary_cause": {
            "type": "string",
            "description": (
                "The single most likely cause. MUST be one of the candidate "
                "cause ids provided; do not invent one."
            ),
        },
        "secondary_causes": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": MAX_SECONDARY_CAUSES,
            "description": (
                "Contributing causes, most relevant first. Each MUST be one of "
                "the candidate cause ids. Empty when only one cause applies."
            ),
        },
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": MAX_EVIDENCE_IDS,
            "description": (
                "Ids of the evidence facts that support the primary cause. "
                "MUST be ids from the supplied evidence list."
            ),
        },
        "confidence": {
            "type": "number",
            "description": (
                "How well the evidence supports the primary cause over the "
                "alternatives, 0 to 1. Use a low value when several candidates "
                "fit equally well."
            ),
        },
        "explanation": {
            "type": "string",
            "description": (
                "Two sentences, addressed to the player as 'you', explaining "
                "what they missed and why. Reference only the supplied facts."
            ),
        },
        "training_recommendation": {
            "type": "string",
            "description": (
                "One short sentence: the concrete habit or check to practise "
                "next time."
            ),
        },
    },
    "required": [
        "primary_cause",
        "secondary_causes",
        "evidence_ids",
        "confidence",
        "explanation",
        "training_recommendation",
    ],
    "additionalProperties": False,
}
