import { describe, it, expect } from 'vitest';
import { Chess } from 'chess.js';
import { pathMoves, formatLine, fenForPath, engineHrefForPath } from './openingLine';
import type { OpeningNode } from '../api';

function node(move_san: string): OpeningNode {
  return { move_san, ply: 0, games_count: 1, wins: 1, draws: 0, losses: 0, win_rate: 100 };
}

const path = (...moves: string[]) => [node('Start'), ...moves.map(node)];

describe('pathMoves', () => {
  it('drops the synthetic root', () => {
    expect(pathMoves(path('e4', 'c5'))).toEqual(['e4', 'c5']);
  });

  it('returns nothing for the root alone', () => {
    expect(pathMoves(path())).toEqual([]);
  });
});

describe('formatLine', () => {
  it('numbers moves the way a player writes them', () => {
    expect(formatLine(path('e4', 'c5', 'Nf3', 'd6'))).toBe('1. e4 c5 2. Nf3 d6');
  });

  it('handles a line ending on White’s move', () => {
    expect(formatLine(path('e4', 'c5', 'Nf3'))).toBe('1. e4 c5 2. Nf3');
  });

  it('names the root', () => {
    expect(formatLine(path())).toBe('Starting position');
  });
});

describe('fenForPath', () => {
  it('returns the start position for an empty line', () => {
    expect(fenForPath(path())).toBe(new Chess().fen());
  });

  it('replays a line to the right position', () => {
    const expected = new Chess();
    ['e4', 'c5', 'Nf3', 'd6', 'd4'].forEach((m) => expected.move(m));

    expect(fenForPath(path('e4', 'c5', 'Nf3', 'd6', 'd4'))).toBe(expected.fen());
  });

  it('handles captures and castling', () => {
    const line = path('e4', 'e5', 'Nf3', 'Nc6', 'Bc4', 'Bc5', 'O-O');
    const fen = fenForPath(line);

    expect(fen).not.toBeNull();
    // White has castled: king on g1, rook on f1.
    expect(fen!.split(' ')[0]).toContain('RK1');
  });

  it('returns null rather than a broken FEN for an illegal line', () => {
    // A corrupt import must degrade to "no deep link", not send nonsense on.
    expect(fenForPath(path('e4', 'e5', 'Qxf7'))).toBeNull();
  });

  it('returns null for a move that is not notation at all', () => {
    expect(fenForPath(path('e4', 'not-a-move'))).toBeNull();
  });
});

describe('engineHrefForPath', () => {
  it('builds an encoded deep link', () => {
    const href = engineHrefForPath(path('e4'))!;

    expect(href.startsWith('/engine?fen=')).toBe(true);
    // The FEN's spaces and slashes must survive the round trip.
    const fen = decodeURIComponent(href.slice('/engine?fen='.length));
    expect(fen).toBe(fenForPath(path('e4')));
    expect(href).not.toContain(' ');
  });

  it('offers no link when the line cannot be replayed', () => {
    expect(engineHrefForPath(path('e4', 'e5', 'Qxf7'))).toBeNull();
  });
});
