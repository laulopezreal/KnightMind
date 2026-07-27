import { describe, it, expect } from 'vitest';
import { formatMotifName, weakestMotif } from './motif';
import type { MotifPerformance } from '../api/users';

const m = (over: Partial<MotifPerformance>): MotifPerformance => ({
  name: 'fork', total_puzzles: 10, passed: 8, accuracy: 0.8,
  rank: 'learning', attempts: 10, insufficient_data: false, ...over,
});

describe('formatMotifName', () => {
  it('humanises snake_case motif keys', () => {
    expect(formatMotifName('back_rank')).toBe('Back Rank');
    expect(formatMotifName('fork')).toBe('Fork');
    expect(formatMotifName('discovered_attack')).toBe('Discovered Attack');
  });

  it('is safe on empty / degenerate input', () => {
    expect(formatMotifName('')).toBe('');
    expect(formatMotifName('a')).toBe('A');
  });
});

describe('weakestMotif', () => {
  it('picks the lowest-accuracy reliable motif, ignoring insufficient_data', () => {
    const { weakest, allStrong } = weakestMotif([
      m({ name: 'fork', accuracy: 0.8 }),
      m({ name: 'back_rank', accuracy: 0.39 }),
      m({ name: 'skewer', accuracy: 0.1, insufficient_data: true }), // ignored
    ]);
    expect(weakest?.name).toBe('back_rank');
    expect(allStrong).toBe(false);
  });

  it('returns null weakest and allStrong=false when nothing is reliable', () => {
    const { weakest, allStrong } = weakestMotif([m({ insufficient_data: true, accuracy: 0.2 })]);
    expect(weakest).toBeNull();
    expect(allStrong).toBe(false);
  });

  it('flags allStrong when every reliable motif is at/above the mastery threshold', () => {
    const { weakest, allStrong } = weakestMotif([m({ accuracy: 0.9 }), m({ accuracy: 0.85 })]);
    expect(allStrong).toBe(true);
    expect(weakest).not.toBeNull(); // still returns the min; caller decides how to render
  });

  it('handles an empty list', () => {
    expect(weakestMotif([])).toEqual({ weakest: null, allStrong: false });
  });
});
