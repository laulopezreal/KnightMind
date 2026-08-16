import { describe, it, expect } from 'vitest';
import { parseBestMoveUci, getPieceNameAtSquare } from './puzzle-clue';

describe('parseBestMoveUci', () => {
  it('should parse basic UCI moves correctly', () => {
    expect(parseBestMoveUci('e2e4')).toEqual({ from: 'e2', to: 'e4' });
    expect(parseBestMoveUci('g1f3')).toEqual({ from: 'g1', to: 'f3' });
    expect(parseBestMoveUci('a7a8')).toEqual({ from: 'a7', to: 'a8' });
  });

  it('should parse UCI moves with promotion correctly', () => {
    expect(parseBestMoveUci('e7e8q')).toEqual({ from: 'e7', to: 'e8', promotion: 'q' });
    expect(parseBestMoveUci('h7h8r')).toEqual({ from: 'h7', to: 'h8', promotion: 'r' });
    expect(parseBestMoveUci('a2a1b')).toEqual({ from: 'a2', to: 'a1', promotion: 'b' });
  });

  it('should handle edge cases', () => {
    expect(parseBestMoveUci('')).toEqual({ from: '', to: '' });
    expect(parseBestMoveUci(undefined as unknown as string)).toEqual({ from: '', to: '' });
    expect(parseBestMoveUci('abc')).toEqual({ from: '', to: '' });
    expect(parseBestMoveUci('a')).toEqual({ from: '', to: '' });
    expect(parseBestMoveUci('a1')).toEqual({ from: '', to: '' });
    expect(parseBestMoveUci('a1a')).toEqual({ from: '', to: '' });
  });

  it('should handle case insensitivity', () => {
    expect(parseBestMoveUci('E2E4')).toEqual({ from: 'e2', to: 'e4' });
    expect(parseBestMoveUci('e2E4')).toEqual({ from: 'e2', to: 'e4' });
    expect(parseBestMoveUci('E7E8Q')).toEqual({ from: 'e7', to: 'e8', promotion: 'q' });
  });

  it('should trim whitespace', () => {
    expect(parseBestMoveUci(' e2e4 ')).toEqual({ from: 'e2', to: 'e4' });
    expect(parseBestMoveUci('  e7e8q  ')).toEqual({ from: 'e7', to: 'e8', promotion: 'q' });
  });
});

describe('getPieceNameAtSquare', () => {
  const startingFen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

  it('should return correct piece names for all piece types', () => {
    expect(getPieceNameAtSquare(startingFen, 'a1')).toBe('Move the rook');
    expect(getPieceNameAtSquare(startingFen, 'b1')).toBe('Move the knight');
    expect(getPieceNameAtSquare(startingFen, 'c1')).toBe('Move the bishop');
    expect(getPieceNameAtSquare(startingFen, 'd1')).toBe('Move the queen');
    expect(getPieceNameAtSquare(startingFen, 'e1')).toBe('Move the king');
    expect(getPieceNameAtSquare(startingFen, 'a2')).toBe('Move the pawn');

    expect(getPieceNameAtSquare(startingFen, 'a8')).toBe('Move the rook');
    expect(getPieceNameAtSquare(startingFen, 'b8')).toBe('Move the knight');
    expect(getPieceNameAtSquare(startingFen, 'c8')).toBe('Move the bishop');
    expect(getPieceNameAtSquare(startingFen, 'd8')).toBe('Move the queen');
    expect(getPieceNameAtSquare(startingFen, 'e8')).toBe('Move the king');
    expect(getPieceNameAtSquare(startingFen, 'a7')).toBe('Move the pawn');
  });

  it('should handle invalid FEN strings gracefully', () => {
    expect(getPieceNameAtSquare('invalid', 'a1')).toBe('Move the correct piece');
    expect(getPieceNameAtSquare('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR', 'a1')).toBe('Move the correct piece');
    expect(getPieceNameAtSquare('', 'a1')).toBe('Move the correct piece');
  });

  it('should handle invalid square parameters', () => {
    expect(getPieceNameAtSquare(startingFen, '')).toBe('Move the correct piece');
    expect(getPieceNameAtSquare(startingFen, undefined as unknown as string)).toBe('Move the correct piece');
    expect(getPieceNameAtSquare(startingFen, 'i9')).toBe('Move the correct piece');
    expect(getPieceNameAtSquare(startingFen, 'a0')).toBe('Move the correct piece');
  });

  it('should return default hint for empty squares', () => {
    expect(getPieceNameAtSquare(startingFen, 'e4')).toBe('Move the correct piece');
    expect(getPieceNameAtSquare(startingFen, 'd4')).toBe('Move the correct piece');
  });

  it('should return default hint when piece type is not recognized', () => {
    // This is unlikely in normal chess but tests the fallback
    const customFen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
    expect(getPieceNameAtSquare(customFen, 'a1')).toBe('Move the rook');
  });

  it('should handle FEN with different piece positions', () => {
    const afterE4Fen = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1';
    expect(getPieceNameAtSquare(afterE4Fen, 'e4')).toBe('Move the pawn');
    expect(getPieceNameAtSquare(afterE4Fen, 'e1')).toBe('Move the king');
  });
});
