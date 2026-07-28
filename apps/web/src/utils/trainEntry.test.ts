import { describe, it, expect } from 'vitest';
import { needsImportFirst, trainEntryDestination } from './trainEntry';

describe('trainEntry', () => {
    it('sends a brand-new user to the import door, not the board', () => {
        // Regression: the hero read "Start First Session" and navigated to
        // /puzzles, whose empty state's only action was "Go to Home" — a loop.
        const state = { totalSessions: 0, dueCount: 0, needsWarmup: false };
        expect(needsImportFirst(state)).toBe(true);
        expect(trainEntryDestination(state)).toBe('/');
    });

    it('sends a first-timer who already has puzzles to the board', () => {
        const state = { totalSessions: 0, dueCount: 5, needsWarmup: false };
        expect(needsImportFirst(state)).toBe(false);
        expect(trainEntryDestination(state)).toBe('/puzzles');
    });

    it('routes a returning user who has been away into the warmup', () => {
        expect(trainEntryDestination({ totalSessions: 12, dueCount: 8, needsWarmup: true }))
            .toBe('/puzzles?warmup=true');
    });

    it('does not treat a caught-up returning user as needing an import', () => {
        const state = { totalSessions: 12, dueCount: 0, needsWarmup: false };
        expect(needsImportFirst(state)).toBe(false);
        expect(trainEntryDestination(state)).toBe('/puzzles');
    });
});
