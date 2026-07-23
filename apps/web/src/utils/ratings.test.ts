import { describe, it, expect } from 'vitest';
import { TC_LABEL, formatSigned } from './ratings';

describe('TC_LABEL', () => {
  it('maps each time control to its display label', () => {
    expect(TC_LABEL.rapid).toBe('Rapid');
    expect(TC_LABEL.blitz).toBe('Blitz');
    expect(TC_LABEL.bullet).toBe('Bullet');
  });
});

describe('formatSigned', () => {
  it('prefixes positive values with + and leaves others bare', () => {
    expect(formatSigned(18)).toBe('+18');
    expect(formatSigned(-9)).toBe('-9');
    expect(formatSigned(0)).toBe('0'); // zero is not "positive" → no plus
  });

  it('preserves the natural integer form when no digits given', () => {
    expect(formatSigned(1500)).toBe('+1500');
  });

  it('fixes decimals when digits are provided (score deltas)', () => {
    expect(formatSigned(2.5, 1)).toBe('+2.5');
    expect(formatSigned(-1.8, 1)).toBe('-1.8');
    expect(formatSigned(0, 1)).toBe('0.0');
  });
});
