import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useClue } from './useClue';

const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

describe('useClue', () => {
    it('should start at stage 0 with empty styles', () => {
        const { result } = renderHook(() => useClue('e2e4', STARTING_FEN));

        expect(result.current.clueStage).toBe(0);
        expect(result.current.squareStyles).toEqual({});
        expect(result.current.pieceHint).toBe('');
        expect(result.current.isExhausted).toBe(false);
        expect(result.current.isDisabled).toBe(false);
        expect(result.current.label).toBe('Clue');
    });

    it('should advance from stage 0 to 1', () => {
        const { result } = renderHook(() => useClue('e2e4', STARTING_FEN));

        act(() => result.current.advance());

        expect(result.current.clueStage).toBe(1);
        expect(result.current.label).toBe('Reveal squares');
        expect(result.current.isExhausted).toBe(false);
        expect(result.current.isDisabled).toBe(false);
    });

    it('should highlight only source square at stage 1', () => {
        const { result } = renderHook(() => useClue('e2e4', STARTING_FEN));

        act(() => result.current.advance());

        expect(result.current.squareStyles).toEqual({
            e2: { backgroundColor: 'rgba(255, 235, 59, 0.45)' },
        });
    });

    it('should show piece hint at stage 1', () => {
        const { result } = renderHook(() => useClue('e2e4', STARTING_FEN));

        act(() => result.current.advance());

        expect(result.current.pieceHint).toBe('Move the pawn');
    });

    it('should advance from stage 1 to 2', () => {
        const { result } = renderHook(() => useClue('e2e4', STARTING_FEN));

        act(() => result.current.advance());
        act(() => result.current.advance());

        expect(result.current.clueStage).toBe(2);
        expect(result.current.label).toBe('Clue used');
        expect(result.current.isExhausted).toBe(true);
        expect(result.current.isDisabled).toBe(true);
    });

    it('should highlight both squares at stage 2', () => {
        const { result } = renderHook(() => useClue('e2e4', STARTING_FEN));

        act(() => result.current.advance());
        act(() => result.current.advance());

        expect(result.current.squareStyles).toEqual({
            e2: { backgroundColor: 'rgba(255, 235, 59, 0.45)' },
            e4: { backgroundColor: 'rgba(255, 235, 59, 0.45)' },
        });
    });

    it('should not advance past stage 2', () => {
        const { result } = renderHook(() => useClue('e2e4', STARTING_FEN));

        act(() => result.current.advance());
        act(() => result.current.advance());
        act(() => result.current.advance());

        expect(result.current.clueStage).toBe(2);
    });

    it('should reset back to stage 0', () => {
        const { result } = renderHook(() => useClue('e2e4', STARTING_FEN));

        act(() => result.current.advance());
        act(() => result.current.advance());
        act(() => result.current.reset());

        expect(result.current.clueStage).toBe(0);
        expect(result.current.squareStyles).toEqual({});
        expect(result.current.pieceHint).toBe('');
        expect(result.current.isExhausted).toBe(false);
        expect(result.current.label).toBe('Clue');
    });

    it('should be disabled when no bestMoveUci', () => {
        const { result } = renderHook(() => useClue('', STARTING_FEN));

        expect(result.current.isDisabled).toBe(true);
    });

    it('should not advance when bestMoveUci is empty', () => {
        const { result } = renderHook(() => useClue('', STARTING_FEN));

        act(() => result.current.advance());

        expect(result.current.clueStage).toBe(0);
    });

    it('should handle malformed UCI strings gracefully', () => {
        const { result } = renderHook(() => useClue('invalid', STARTING_FEN));

        act(() => result.current.advance());

        expect(result.current.clueStage).toBe(1);
        expect(result.current.pieceHint).toBe('Move the correct piece');
    });

    it('should maintain referential stability of advance and reset', () => {
        const { result, rerender } = renderHook(() => useClue('e2e4', STARTING_FEN));

        const firstAdvance = result.current.advance;
        const firstReset = result.current.reset;

        rerender();

        expect(result.current.advance).toBe(firstAdvance);
        expect(result.current.reset).toBe(firstReset);
    });
});
