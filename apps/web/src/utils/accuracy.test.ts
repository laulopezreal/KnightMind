import { describe, it, expect } from 'vitest';
import { calculateAccuracy } from './accuracy';

describe('calculateAccuracy', () => {
  it('should return 0 when no attempts', () => {
    expect(calculateAccuracy(0, 0)).toBe(0);
  });

  it('should return 100 when all pass', () => {
    expect(calculateAccuracy(10, 0)).toBe(100);
    expect(calculateAccuracy(1, 0)).toBe(100);
  });

  it('should return 0 when all fail', () => {
    expect(calculateAccuracy(0, 10)).toBe(0);
    expect(calculateAccuracy(0, 1)).toBe(0);
  });

  it('should calculate correct percentages', () => {
    expect(calculateAccuracy(1, 1)).toBe(50);
    expect(calculateAccuracy(3, 1)).toBe(75);
    expect(calculateAccuracy(1, 3)).toBe(25);
  });

  it('should round to nearest integer', () => {
    expect(calculateAccuracy(1, 2)).toBe(33);
    expect(calculateAccuracy(2, 1)).toBe(67);
    expect(calculateAccuracy(1, 6)).toBe(14);
  });

  it('should handle large numbers', () => {
    expect(calculateAccuracy(999, 1)).toBe(100);
    expect(calculateAccuracy(500, 500)).toBe(50);
  });
});
