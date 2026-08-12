"""Prompt construction for AI puzzle naming.

Pure string building from a ``NameFacts``, which carries no username, no
opponent handle, and no account id — the same identity boundary
``ai/prompts.py`` documents for diagnosis. That is a deliberate product
decision: names could have referenced the opponent ("Outfoxed by <handle>")
and the choice was to keep handles off the wire. ``NameFacts`` has no field to
put one in, so this cannot regress by someone editing the prompt carelessly.

On telling the model the answer
-------------------------------
The name is shown beside a board the user is about to solve, so it must not
give the solution away. The obvious way to guarantee that is to withhold the
winning move. That was tried, measured, and abandoned:

- Withheld, with the motif: 20/20 names were the move and its point joined by
  a comma ("Check on h5, d7 Was the Fork").
- Withheld, motif also removed: the clock became the only varied fact left and
  13 of 40 names became "N Seconds of ...".
- Withheld, clock rationed too: 11 of 19 collapsed onto "<Piece> <verb> to
  <square>", with "Knight Hops to _" three times in twenty.

Each removal moved the crutch rather than removing it. Given the tactic *as
context for choosing an angle* and forbidden from stating it, the same model
produced varied, specific names and zero gate violations on the same twenty
positions. So the answer is in the prompt, and ``ai_naming._validate`` is what
enforces it stays out of the name. That makes the gate load-bearing, which is
a deliberate trade and the reason its rules are tested individually.

The system prompt is byte-identical on every call — the cacheable prefix, the
same trick the diagnosis prompt relies on.
"""

from services.api.puzzles.naming_schema import MAX_NAME_CHARS

# Below this many seconds is a snap decision, above it is a long think. In
# between, the clock says nothing worth a name — and offering it anyway is how
# a whole batch ended up called "N Seconds of ...".
CLOCK_FAST_SECONDS = 3.0
CLOCK_SLOW_SECONDS = 60.0

SYSTEM_PROMPT = f"""\
You name chess puzzles. Each puzzle is one position where a player missed \
something in their own game. The name sits in their library beside a board \
they are about to try to solve — a shelf of their own blunders they scroll \
past every day.

Name the moment. The tactic, what they played instead, what a piece was doing, \
what got overlooked, how long they took, whether they somehow won anyway — all \
of it is fair material.

The ONE thing you may not do is name the square the winning move lands on. \
The player is about to look for that move; do not point at it. Everything \
short of the square is yours.

GOOD:
  Dark Bishop Slept In
  Queen Walked Right Past It
  Nowhere Left for the Bishop
  Fork Season Opens Early
  Grabbed a Pawn Instead
  Knight Went Wandering
  Pinned and Cheerful About It
  Won It Anyway

BAD — names the square, so it gives the move away:
  Mate Was on d8
  Nf7 Was Sitting There

BAD — dull, templated, or opens with "The":
  The Bishop Sat on c3
  Knight Hops to g5
  Nine Seconds of Free Pawn

Write a TITLE. Two to four words. ONE clause — no commas. At most \
{MAX_NAME_CHARS} characters.

Do NOT begin with "The". Open with a verb, a number, a noun, a preposition — \
anything but that article.

Other constraints:
- Never invent facts. If you are not told the clock, there was no time \
trouble. If you are not told the result, nobody won.
- Never address the player as "you", and never refer to their opponent.
- Title Case. No trailing period. No quotation marks.

Dry wit, not jokes. Specific beats clever; when you can have both, take both."""


def build_user_prompt(facts, avoid: list[str] | None = None) -> str:
    """Render one puzzle's facts, plus names already used in this library.

    ``avoid`` exists because the model names each puzzle in an independent call
    and cannot otherwise know it has already used a phrasing. It is a nudge, not
    a guarantee — the caller still de-duplicates what comes back.
    """
    lines = [
        "POSITION",
        f"FEN: {facts.fen}",
        f"The move they actually played: {facts.played_move_san}.",
    ]

    if facts.best_move_san:
        # The answer, labelled as context rather than as material. See the
        # module docstring for why it is here at all.
        missed = facts.best_move_san
        if facts.primary_motif:
            missed = f"{missed} ({facts.primary_motif})"
        lines.append(
            f"What they missed (name the idea if you like — never the square): "
            f"{missed}"
        )

    if facts.move_number is not None:
        lines.append(f"Move number: {facts.move_number}")
    if facts.phase:
        lines.append(f"Phase: {facts.phase}")
    if facts.opening_name:
        lines.append(f"Opening: {facts.opening_name}")
    if facts.move_time_seconds is not None and (
        facts.move_time_seconds < CLOCK_FAST_SECONDS
        or facts.move_time_seconds > CLOCK_SLOW_SECONDS
    ):
        # Only when the clock is actually notable. Supplying it every time made
        # it the default hook for a whole batch of names.
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
