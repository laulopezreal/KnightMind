import { Chess } from 'chess.js';

/**
 * Clue helpers for puzzle two-stage hint: parse best_move_uci and piece name from FEN.
 */

const PIECE_TYPE_TO_NAME: Record<string, string> = {
  p: 'pawn',
  n: 'knight',
  b: 'bishop',
  r: 'rook',
  q: 'queen',
  k: 'king',
};

const DEFAULT_HINT = 'Move the correct piece';

/**
 * Parse best_move_uci into from square, to square, and optional promotion.
 */
export function parseBestMoveUci(uci: string): { from: string; to: string; promotion?: string } {
  const normalized = (uci || '').toLowerCase().trim();
  if (normalized.length < 4) {
    return { from: '', to: '' };
  }
  const from = normalized.slice(0, 2);
  const to = normalized.slice(2, 4);
  const promotion = normalized.length >= 5 ? normalized.slice(4, 5) : undefined;
  return { from, to, promotion };
}

/**
 * Get human-readable piece name at square from FEN. Uses puzzle FEN (initial position).
 */
export function getPieceNameAtSquare(fen: string, square: string): string {
  if (!fen || !square) return DEFAULT_HINT;
  try {
    const chess = new Chess(fen);
    const piece = chess.get(square as 'a1');
    if (!piece || !piece.type) return DEFAULT_HINT;
    const name = PIECE_TYPE_TO_NAME[piece.type.toLowerCase()];
    return name ? `Move the ${name}` : DEFAULT_HINT;
  } catch {
    return DEFAULT_HINT;
  }
}
