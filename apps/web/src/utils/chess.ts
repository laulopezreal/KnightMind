import { Chess } from 'chess.js';

/**
 * Convert a UCI move line into human-readable SAN, played out from `fen`.
 * Returns one SAN token per input move ("Rb5+", "Kh6", "Ra6#"). If a move in
 * the line is illegal from the evolving position (corrupt data), conversion
 * stops and the remaining moves fall back to their raw UCI so the user still
 * sees something rather than a crash or a silent truncation.
 */
export function uciLineToSan(fen: string, ucis: string[]): string[] {
  const out: string[] = [];
  let board: Chess;
  try {
    board = new Chess(fen);
  } catch {
    return [...ucis];
  }
  for (let i = 0; i < ucis.length; i++) {
    const uci = ucis[i];
    try {
      const move = board.move({
        from: uci.slice(0, 2),
        to: uci.slice(2, 4),
        promotion: uci.slice(4, 5) || undefined,
      });
      if (!move) {
        out.push(...ucis.slice(i));
        break;
      }
      out.push(move.san);
    } catch {
      out.push(...ucis.slice(i));
      break;
    }
  }
  return out;
}
