import { describe, it, expect } from 'vitest';
import { uciLineToSan } from './chess';

describe('uciLineToSan', () => {
  it('converts a single mating move to SAN', () => {
    const fen = 'r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4';
    expect(uciLineToSan(fen, ['h5f7'])).toEqual(['Qxf7#']);
  });

  it('converts a multi-move line, tracking the evolving position', () => {
    const fen = '8/8/8/7k/8/8/R7/1R4K1 w - - 0 1';
    expect(uciLineToSan(fen, ['b1b5', 'h5h6', 'a2a6'])).toEqual(['Rb5+', 'Kh6', 'Ra6+']);
  });

  it('carries promotions', () => {
    const fen = '8/P6k/8/8/8/8/8/K7 w - - 0 1';
    expect(uciLineToSan(fen, ['a7a8q'])).toEqual(['a8=Q']);
  });

  it('falls back to raw UCI from the first illegal move onward', () => {
    const fen = '8/8/8/7k/8/8/R7/1R4K1 w - - 0 1';
    expect(uciLineToSan(fen, ['b1b5', 'zzzz'])).toEqual(['Rb5+', 'zzzz']);
  });

  it('is safe on an invalid FEN', () => {
    expect(uciLineToSan('not a fen', ['e2e4'])).toEqual(['e2e4']);
  });
});
