"""The response contract for an AI puzzle name.

Two representations of the same shape, mirroring ``ai/schema.py``: a JSON Schema
the API enforces during generation, and a Pydantic model that validates what
actually came back.

The gate here is thinner than the diagnosis gate on purpose. A diagnosis is
checkable — a cause either is or is not in the candidate set. A *name* has no
ground truth to check against, so validation covers shape and safety only:
length, one line, actually contains letters. Porting the diagnosis gate's
"is this supported by the evidence" checks onto a joke would be theatre.
"""

from pydantic import BaseModel, Field, field_validator

# Tighter than the card can physically fit (position_names.MAX_NAME_CHARS, 48).
# This is a writing constraint, not a layout one: at 48 the model had room for
# two clauses and used it every single time, producing "Rook to f5, Pawn to g4"
# rather than a title. Taking the room away is what forces compression —
# asking for brevity in the prompt did not.
MAX_NAME_CHARS = 30
MIN_NAME_CHARS = 3


class PuzzleName(BaseModel):
    """What the model is allowed to return."""

    name: str = Field(min_length=MIN_NAME_CHARS, max_length=MAX_NAME_CHARS)

    @field_validator("name")
    @classmethod
    def _single_clean_line(cls, value: str) -> str:
        # A name with a newline in it breaks the card and usually means the
        # model wrote a list instead of a title.
        if "\n" in value or "\r" in value:
            raise ValueError("name must be a single line")
        cleaned = value.strip().strip('"').strip()
        if not cleaned:
            raise ValueError("name must not be blank")
        # Guards against "24", "1. e4 e5", and other non-names that satisfy a
        # length bound but are not titles.
        if not any(ch.isalpha() for ch in cleaned):
            raise ValueError("name must contain letters")
        return cleaned


RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": (
                "The puzzle's name: a short, funny, specific title of at most "
                f"{MAX_NAME_CHARS} characters. Title Case, no trailing period, "
                "no quotation marks. It must refer to something concrete in "
                "the position given."
            ),
        },
    },
    "required": ["name"],
    "additionalProperties": False,
}
