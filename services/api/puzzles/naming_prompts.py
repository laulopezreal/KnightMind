"""Prompt construction for AI puzzle naming.

Pure string building from a ``NameFacts``, which carries no username, no
opponent handle, and no account id — the same identity boundary
``ai/prompts.py`` documents for diagnosis. That is a deliberate product
decision, not an oversight: names could have referenced the opponent
("Outfoxed by <handle>") and the choice was to keep handles off the wire.
``ai_naming.NameFacts`` has no field to put one in, so this cannot regress by
someone editing the prompt carelessly.

The system prompt is byte-identical on every call — the cacheable prefix, the
same trick the diagnosis prompt relies on.
"""

from services.api.puzzles.naming_schema import MAX_NAME_CHARS

SYSTEM_PROMPT = """\
You name chess puzzles. Each puzzle is one position where a player missed \
something in their own game. The name sits in their library beside a board \
they are about to try to solve.

Because they are about to solve it, THE NAME MUST NOT GIVE AWAY THE ANSWER.

You are not told the winning move, and you must not work it out and hint at \
it. Never name the tactic. Never name the square the winning move lands on. \
Never use the words fork, pin, skewer, mate, hanging, or loose. If someone \
could read your name and find the move without studying the board, the name \
has failed.

Name the moment instead — everything around the mistake is fair game:

- the move they played instead
- how long they spent on it
- where in the game it happened
- the opening they were in
- whether they somehow won anyway

Write a TITLE. Two to four words. ONE clause — no commas.

Do NOT begin with "The". Open with a verb, a number, a square, a noun, a \
preposition, anything but that article.

GOOD:
  Blitzed Past It
  h6 Seemed Reasonable
  Confidence in the Sicilian
  Won It Anyway
  Move 31 Optimism
  Four Seconds of Certainty
  Nobody's Finest Rook Move

BAD — gives the answer away:
  The e5 Fork Never Came
  Mate Was on d8
  The g5 Knight Was Free

BAD — dull, or opens with "The":
  The Bishop Sat on c3
  The Knight Skipped f3

Other constraints:
- Never invent facts. If you are not told the clock, there was no time \
trouble. If you are not told the result, nobody won.
- Never address the player as "you", and never refer to their opponent.
- Title Case. No trailing period. No quotation marks.

Dry wit, not jokes. Aim at the feeling of the mistake, never its solution."""


def build_user_prompt(facts, avoid: list[str] | None = None) -> str:
    """Render one puzzle's facts, plus names already used in this library.

    ``avoid`` exists because the model names each puzzle in an independent call
    and cannot otherwise know it has already used a phrasing. It is a nudge, not
    a guarantee — the caller still de-duplicates what comes back.
    """
    # The played move, NOT the winning one. The name goes beside a board the
    # user is about to solve, so the solution must not be in this prompt at
    # all: a rule saying "don't reveal it" is weaker than not knowing it.
    # The motif is withheld for the same reason — "fork" is most of the answer.
    lines = [
        "POSITION",
        f"FEN: {facts.fen}",
        f"The move they actually played: {facts.played_move_san}.",
    ]
    if facts.move_number is not None:
        lines.append(f"Move number: {facts.move_number}")
    if facts.phase:
        lines.append(f"Phase: {facts.phase}")
    if facts.opening_name:
        lines.append(f"Opening: {facts.opening_name}")
    if facts.move_time_seconds is not None:
        lines.append(f"Time spent on the move: {facts.move_time_seconds:g} seconds")
    if facts.user_won is not None:
        lines.append(
            "The player won this game anyway."
            if facts.user_won
            else "The player lost this game."
        )

    prompt = "\n".join(lines)

    if avoid:
        # Most-recent-first: the collisions worth preventing are with names the
        # model just produced for adjacent, similar puzzles.
        listed = "\n".join(f"- {n}" for n in avoid)
        prompt += (
            "\n\nNAMES ALREADY USED IN THIS LIBRARY (do not reuse or "
            f"near-repeat these)\n{listed}"
        )

    return prompt + (
        f"\n\nReturn the name as JSON. At most {MAX_NAME_CHARS} characters."
    )
