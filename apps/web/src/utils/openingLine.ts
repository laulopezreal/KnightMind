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

/**
 * Walk a move sequence to the nodes it names in this tree.
 *
 * The moves are the durable identity of a line; the nodes are not. They carry
 * the figures of the tree they came from, so after a refetch they are stale and
 * after a colour-filter change they answer a different question entirely.
 * Re-walking returns the *current* nodes, or null when the line is not in this
 * tree — the honest answer for "your Sicilian, as White".
 */
export function resolveMoves(tree: OpeningNode, moves: string[]): OpeningPath | null {
  const resolved: OpeningNode[] = [tree];
  let current = tree;

  for (const san of moves) {
    const next = current.children?.find((child) => child.move_san === san);
    if (!next) return null;
    resolved.push(next);
    current = next;
  }
  return resolved;
}

/**
 * Query-string form of a line: `e4_c5_Nf3`.
 *
 * Underscore never occurs in SAN, and unlike a comma it survives
 * `URLSearchParams` unescaped — so the common case stays a URL you can read.
 */
export function encodeLine(path: OpeningPath): string {
  return pathMoves(path).join('_');
}

/** Moves from a `line` parameter. Junk decodes to a line no tree contains. */
export function decodeLine(param: string | null): string[] {
  return param ? param.split('_').filter(Boolean) : [];
}

/** Deep link to the Engine for the position a path reaches. */
export function engineHrefForPath(path: OpeningPath): string | null {
  const fen = fenForPath(path);
  return fen ? `/engine?fen=${encodeURIComponent(fen)}` : null;
}
