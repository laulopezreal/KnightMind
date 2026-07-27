import { describe, it, expect } from 'vitest';
import { Chess } from 'chess.js';
import { pathMoves, formatLine, fenForPath, engineHrefForPath, resolvePath } from './openingLine';
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

describe('resolvePath', () => {
  const tree: OpeningNode = {
    ...node('Start'),
    games_count: 40,
    children: [
      { ...node('e4'), games_count: 30, children: [{ ...node('c5'), games_count: 12 }] },
      node('d4'),
    ],
  };

  it('returns the same line with the new tree’s figures', () => {
    // A selection made against an older tree must not keep reporting its
    // numbers after a refetch.
    const stale = [node('Start'), { ...node('e4'), games_count: 999 }];
    const fresh = resolvePath(tree, stale)!;

    expect(fresh.map((n) => n.move_san)).toEqual(['Start', 'e4']);
    expect(fresh[1].games_count).toBe(30);
  });

  it('resolves a deeper line', () => {
    const fresh = resolvePath(tree, [node('Start'), node('e4'), node('c5')])!;

    expect(fresh).toHaveLength(3);
    expect(fresh[2].games_count).toBe(12);
  });

  it('returns null when the line is absent from this tree', () => {
    // e.g. the same selection after switching to "as Black".
    expect(resolvePath(tree, [node('Start'), node('e4'), node('e5')])).toBeNull();
  });

  it('returns null when a whole branch is gone', () => {
    expect(resolvePath(tree, [node('Start'), node('Nf3')])).toBeNull();
  });

  it('resolves the bare root', () => {
    expect(resolvePath(tree, [node('Start')])).toEqual([tree]);
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
