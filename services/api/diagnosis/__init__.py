"""Mistake Cause Intelligence.

Turns a puzzle (which, in KnightMind, always *is* a blunder the user played in a
real game) into an evidence-backed explanation of *why* the mistake happened.

The pipeline is staged, deterministic-first:

1. ``pgn_context`` — replay the stored PGN for the facts a FEN cannot carry
   (the opponent's previous move, the clock, whether the user had castled).
2. ``evidence`` — board analysis into a typed, citable evidence packet.

Later stages (rule-based cause classification, AI ranking, clustering) build on
the packet and never re-derive chess facts of their own. See
``docs/mistake-cause-intelligence-plan.md``.
"""
