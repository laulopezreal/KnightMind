import { describe, it, expect } from 'vitest';
import { formatMotifName } from './motif';

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
