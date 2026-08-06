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

A good name is short, specific to THIS position, and quietly funny. The humour \
comes from precision and from recognition, never from insult. You are naming \
the moment, not mocking the player.

Hard constraints:
- Refer to something concrete in the position you are given: the piece, the \
square, what hung, what was missed. A name that would fit any puzzle is a \
failed name.
- Never restate the move list or narrate the position. It is a title, not a \
summary.
- Never invent facts. If you are not told the clock, there was no time \
trouble. If you are not told the result, nobody won.
- Never address the player as "you", and never refer to their opponent.
- No trailing period. No quotation marks. Title Case.

Aim for two to five words. Prefer the specific noun over the clever pun; when \
both are available, take both."""


def build_user_prompt(facts, avoid: list[str] | None = None) -> str:
    """Render one puzzle's facts, plus names already used in this library.

    ``avoid`` exists because the model names each puzzle in an independent call
    and cannot otherwise know it has already used a phrasing. It is a nudge, not
    a guarantee — the caller still de-duplicates what comes back.
    """
    lines = [
        "POSITION",
        f"FEN: {facts.fen}",
        f"The player played {facts.played_move_san}. "
        f"The engine's move was {facts.best_move_san}.",
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
