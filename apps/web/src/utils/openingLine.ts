import { Chess } from 'chess.js';
import type { OpeningNode } from '../api';

/**
 * A root-to-node path through the opening tree, as the graph reports it.
 * Index 0 is the synthetic "Start" root, so the moves begin at index 1.
 */
export type OpeningPath = OpeningNode[];

/** SAN moves along a path, with the synthetic root dropped. */
export function pathMoves(path: OpeningPath): string[] {
  return path.filter((node) => node.move_san !== 'Start').map((node) => node.move_san);
}

/**
 * Render a path the way a chess player writes it: "1. e4 c5 2. Nf3".
 * Half-moves are numbered from the start of the game, so a path beginning with
 * Black's reply still reads correctly.
 */
export function formatLine(path: OpeningPath): string {
  const moves = pathMoves(path);
  if (moves.length === 0) return 'Starting position';

  const parts: string[] = [];
  moves.forEach((san, index) => {
    const isWhite = index % 2 === 0;
    if (isWhite) parts.push(`${index / 2 + 1}. ${san}`);
    else parts.push(san);
  });
  return parts.join(' ');
}

/**
 * Replay a path to the position it reaches.
 *
 * Returns null when a move will not apply — the tree is built from stored PGNs
 * and should always be legal, but a corrupt import must degrade to "no deep
 * link offered" rather than sending a broken FEN to the Engine.
 */
export function fenForPath(path: OpeningPath): string | null {
  const game = new Chess();
  for (const san of pathMoves(path)) {
    try {
      const applied = game.move(san);
      if (!applied) return null;
    } catch {
      return null;
    }
  }
  return game.fen();
}

/** Deep link to the Engine for the position a path reaches. */
export function engineHrefForPath(path: OpeningPath): string | null {
  const fen = fenForPath(path);
  return fen ? `/engine?fen=${encodeURIComponent(fen)}` : null;
}
