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
You name chess puzzles. Each puzzle is one mistake a player made in their own \
game, and the name is what they will see in their puzzle library — a shelf of \
their own blunders they scroll past every day.

Write a TITLE, not a description. Two to four words.

The one failure that matters, and the one to avoid, is naming two things at \
once:

  BAD:  Knight Leaps to e4, Bishop Still on c4
  BAD:  Check on h5, d7 Was the Fork
  BAD:  Queen to a4, Mate Sitting on d8

Every one of those is a summary wearing a title's clothes. It lists the move \
and then the point, joined by a comma. Do not write these.

Pick ONE thing — the sharpest detail in the position — and name only that:

  GOOD: The f7 Fork
  GOOD: Bishop Goes Hungry
  GOOD: Mate Was on d8
  GOOD: The Lonely h3 Queen
  GOOD: Rook Takes the Long Way

Hard constraints:
- ONE clause. No commas.
- Refer to something concrete in this position: a piece, a square, what hung, \
what was missed. A name that would fit any puzzle is a failed name.
- Never list moves. Never narrate what happened.
- Never invent facts. If you are not told the clock, there was no time \
trouble. If you are not told the result, nobody won.
- Never address the player as "you", and never refer to their opponent.
- Title Case. No trailing period. No quotation marks.

Dry and specific beats jokey. If nothing about the position is funny, be exact \
instead — an accurate name is better than a forced joke."""


def build_user_prompt(facts, avoid: list[str] | None = None) -> str:
    """Render one puzzle's facts, plus names already used in this library.

    ``avoid`` exists because the model names each puzzle in an independent call
    and cannot otherwise know it has already used a phrasing. It is a nudge, not
    a guarantee — the caller still de-duplicates what comes back.
    """
    # Only the move that was MISSED. Sending the played move as well produced
    # names that dutifully mentioned both — "Knight Leaps to e4, Bishop Still
    # on c4" — because two moves in the prompt reads as two things to name.
    lines = [
        "POSITION",
        f"FEN: {facts.fen}",
        f"The move that was there and was not played: {facts.best_move_san}.",
    ]
    if facts.move_number is not None:
        lines.append(f"Move number: {facts.move_number}")
    if facts.phase:
        lines.append(f"Phase: {facts.phase}")
    if facts.primary_motif:
        lines.append(f"Tactical motif the analyser found: {facts.primary_motif}")
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
